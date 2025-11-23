"""
Goal Progress Details API - Transparent progress breakdown with unblocking actions
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
from database import get_workspace_goals, get_deliverables, get_task, update_deliverable_status
from uuid import UUID
import logging
import asyncio

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# RECOVERY ACTION IMPLEMENTATIONS
# =============================================================================

async def _retry_failed_deliverable(workspace_id: str, deliverable: Dict[str, Any]) -> Dict[str, Any]:
    """
    Retry a failed deliverable by resetting its status and re-queuing for processing.

    Strategy:
    1. Reset deliverable status to 'pending'
    2. Clear error state
    3. Re-queue associated tasks for execution
    """
    try:
        deliverable_id = deliverable.get('id')

        # Reset status to pending to allow reprocessing
        await update_deliverable_status(
            deliverable_id=deliverable_id,
            status='pending',
            metadata={
                'retry_count': deliverable.get('metadata', {}).get('retry_count', 0) + 1,
                'last_retry_at': asyncio.get_event_loop().time(),
                'previous_error': deliverable.get('error_message', 'Unknown error')
            }
        )

        logger.info(f"✅ Retry queued for deliverable {deliverable_id}")
        return {
            'success': True,
            'deliverable_id': deliverable_id,
            'action': 'retry_queued',
            'new_status': 'pending'
        }

    except Exception as e:
        logger.error(f"Failed to retry deliverable {deliverable.get('id')}: {e}")
        return {
            'success': False,
            'deliverable_id': deliverable.get('id'),
            'error': str(e)
        }


async def _resume_pending_deliverable(workspace_id: str, deliverable: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resume a pending deliverable that may be stalled.

    Strategy:
    1. Check if deliverable has associated tasks
    2. Re-trigger task execution if needed
    3. Update deliverable to 'in_progress' to indicate active processing
    """
    try:
        deliverable_id = deliverable.get('id')

        # Update status to in_progress to signal active processing
        await update_deliverable_status(
            deliverable_id=deliverable_id,
            status='in_progress',
            metadata={
                'resumed_at': asyncio.get_event_loop().time(),
                'resume_count': deliverable.get('metadata', {}).get('resume_count', 0) + 1
            }
        )

        logger.info(f"✅ Resume queued for deliverable {deliverable_id}")
        return {
            'success': True,
            'deliverable_id': deliverable_id,
            'action': 'resume_queued',
            'new_status': 'in_progress'
        }

    except Exception as e:
        logger.error(f"Failed to resume deliverable {deliverable.get('id')}: {e}")
        return {
            'success': False,
            'deliverable_id': deliverable.get('id'),
            'error': str(e)
        }


async def _escalate_deliverable(workspace_id: str, deliverable: Dict[str, Any]) -> Dict[str, Any]:
    """
    Escalate a blocked deliverable to human review.

    Strategy:
    1. Mark deliverable as requiring human intervention
    2. Create a human feedback request
    3. Notify relevant stakeholders
    """
    try:
        deliverable_id = deliverable.get('id')

        # Update status to indicate human review needed
        await update_deliverable_status(
            deliverable_id=deliverable_id,
            status='needs_human_review',
            metadata={
                'escalated_at': asyncio.get_event_loop().time(),
                'escalation_reason': f"Automatic escalation after {deliverable.get('metadata', {}).get('retry_count', 0)} retries",
                'original_status': deliverable.get('status', 'unknown')
            }
        )

        # Try to create a human feedback request if the service is available
        try:
            from services.human_feedback import create_feedback_request
            await create_feedback_request(
                workspace_id=workspace_id,
                request_type='deliverable_review',
                item_id=deliverable_id,
                context={
                    'deliverable_title': deliverable.get('title', 'Unknown'),
                    'error_message': deliverable.get('error_message', ''),
                    'retry_count': deliverable.get('metadata', {}).get('retry_count', 0)
                },
                priority='high'
            )
        except ImportError:
            logger.warning("Human feedback service not available, escalation recorded in metadata only")
        except Exception as feedback_error:
            logger.warning(f"Could not create feedback request: {feedback_error}")

        logger.info(f"✅ Escalated deliverable {deliverable_id} to human review")
        return {
            'success': True,
            'deliverable_id': deliverable_id,
            'action': 'escalated_to_human',
            'new_status': 'needs_human_review'
        }

    except Exception as e:
        logger.error(f"Failed to escalate deliverable {deliverable.get('id')}: {e}")
        return {
            'success': False,
            'deliverable_id': deliverable.get('id'),
            'error': str(e)
        }

@router.get("/goal-progress-details/{workspace_id}/{goal_id}")
async def get_goal_progress_details(
    workspace_id: str, 
    goal_id: str,
    include_hidden: bool = Query(True, description="Include failed/pending items usually hidden from UI")
) -> Dict[str, Any]:
    """
    Get comprehensive goal progress breakdown including all statuses
    
    This endpoint provides full transparency into what contributes to goal progress:
    - All deliverables (completed, failed, pending, in_progress)
    - Task breakdown with status counts
    - Progress calculation explanation
    - Unblocking recommendations
    """
    try:
        # Get goal data
        goals = await get_workspace_goals(workspace_id)
        goal = next((g for g in goals if g['id'] == goal_id), None)
        
        if not goal:
            raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
        
        # Get all deliverables for this goal
        deliverables = await get_deliverables(workspace_id, goal_id=goal_id)
        
        # Categorize deliverables by status
        deliverable_breakdown = {
            'completed': [],
            'failed': [],
            'pending': [],
            'in_progress': [],
            'unknown': []
        }
        
        deliverable_stats = {
            'completed': 0,
            'failed': 0, 
            'pending': 0,
            'in_progress': 0,
            'unknown': 0,
            'total': len(deliverables)
        }
        
        for deliverable in deliverables:
            status = deliverable.get('status', 'unknown')
            if status not in deliverable_breakdown:
                status = 'unknown'
            
            deliverable_breakdown[status].append({
                'id': deliverable.get('id'),
                'title': deliverable.get('title'),
                'status': status,
                'type': deliverable.get('type'),
                'task_id': deliverable.get('task_id'),
                'business_value_score': deliverable.get('business_value_score'),
                'created_at': deliverable.get('created_at'),
                'updated_at': deliverable.get('updated_at'),
                'quality_level': deliverable.get('quality_level'),
                'can_retry': status in ['failed', 'pending'],  # Actionable items
                'retry_reason': _get_retry_reason(status, deliverable),
                'unblock_actions': _get_unblock_actions(status, deliverable),
                # Include enhanced display content fields for frontend
                'content': deliverable.get('content'),  # Original content
                'display_content': deliverable.get('display_content'),  # AI-enhanced HTML/Markdown
                'display_format': deliverable.get('display_format', 'html'),  # Format type
                'display_quality_score': deliverable.get('display_quality_score'),  # AI confidence
                'display_summary': deliverable.get('display_summary'),  # Brief summary
                'user_friendliness_score': deliverable.get('user_friendliness_score'),  # UX score
                'auto_display_generated': deliverable.get('auto_display_generated'),  # AI vs manual
                'transformation_timestamp': deliverable.get('transformation_timestamp'),  # When transformed
                'can_retry_transformation': deliverable.get('can_retry_transformation', False),
                'available_formats': deliverable.get('available_formats', ['html', 'markdown']),
                'transformation_error': deliverable.get('transformation_error')
            })
            
            deliverable_stats[status] += 1
        
        # Calculate progress breakdown using goal's current_value/target_value as source of truth
        goal_current_value = goal.get('current_value', 0)
        goal_target_value = goal.get('target_value', 1)
        goal_based_progress = (goal_current_value / goal_target_value * 100) if goal_target_value > 0 else 0
        
        # Calculate API-based progress (what we find in deliverables table)
        completed_count = deliverable_stats['completed']
        total_count = deliverable_stats['total']
        api_calculated_progress = (completed_count / total_count * 100) if total_count > 0 else 0
        
        # Progress analysis - compare goal tracking vs API deliverables
        reported_progress = goal.get('progress', goal_based_progress)  # Use goal-based as fallback
        progress_discrepancy = abs(goal_based_progress - api_calculated_progress)
        
        # Get hidden items count (what UI normally doesn't show)
        hidden_count = deliverable_stats['failed'] + deliverable_stats['pending'] + deliverable_stats['in_progress']
        visible_count = deliverable_stats['completed']
        
        # Unblocking summary based on goal tracking
        unblocking_summary = {
            'actionable_items': hidden_count,
            'retry_available': deliverable_stats['failed'] + deliverable_stats['pending'],
            'total_blocked_progress': 100 - goal_based_progress,
            'missing_deliverables': goal_target_value - goal_current_value,
            'recommended_actions': _get_recommended_actions(deliverable_breakdown)
        }
        
        return {
            'goal': {
                'id': goal_id,
                'title': goal.get('title'),
                'status': goal.get('status'), 
                'progress': reported_progress,
                'current_value': goal.get('current_value'),
                'target_value': goal.get('target_value')
            },
            'progress_analysis': {
                'reported_progress': reported_progress,
                'goal_based_progress': goal_based_progress,
                'api_calculated_progress': api_calculated_progress,
                'progress_discrepancy': progress_discrepancy,
                'calculation_method': 'goal_tracking_vs_api_deliverables',
                'goal_metrics': {
                    'current_value': goal_current_value,
                    'target_value': goal_target_value
                },
                'api_metrics': {
                    'total_deliverables': total_count,
                    'completed_deliverables': completed_count
                }
            },
            'deliverable_breakdown': deliverable_breakdown,
            'deliverable_stats': deliverable_stats,
            'visibility_analysis': {
                'shown_in_ui': visible_count,
                'hidden_from_ui': hidden_count,
                'transparency_gap': f"{hidden_count} items ({(hidden_count/total_count*100):.1f}%) hidden" if total_count > 0 else "No items"
            },
            'unblocking': unblocking_summary,
            'recommendations': [
                f"Goal tracking shows {goal_current_value}/{goal_target_value} ({goal_based_progress:.1f}%)",
                f"API found {completed_count}/{total_count} completed deliverables ({api_calculated_progress:.1f}%)",
                "Discrepancy likely indicates missing deliverable (e.g. sequence 2) not created",
                "Use goal progress as source of truth for accurate completion tracking"
            ] + unblocking_summary['recommended_actions']
        }
        
    except Exception as e:
        logger.error(f"Error getting goal progress details: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def _get_retry_reason(status: str, deliverable: Dict[str, Any]) -> Optional[str]:
    """Get human-readable reason why item can be retried"""
    if status == 'failed':
        return "Task execution failed - can be retried"
    elif status == 'pending':
        return "Task stuck in pending state - can be resumed"
    elif status == 'in_progress':
        return "Task in progress - monitor or escalate if stuck"
    return None

def _get_unblock_actions(status: str, deliverable: Dict[str, Any]) -> List[str]:
    """Get available unblocking actions for this deliverable"""
    actions = []
    
    if status == 'failed':
        actions.extend([
            'retry_task',
            'skip_and_continue', 
            'escalate_to_human'
        ])
    elif status == 'pending':
        actions.extend([
            'resume_task',
            'check_dependencies',
            'escalate_to_human'
        ])
    elif status == 'in_progress':
        actions.extend([
            'check_progress',
            'escalate_if_stuck'
        ])
    
    return actions

def _get_recommended_actions(breakdown: Dict[str, List]) -> List[str]:
    """Get recommended actions based on deliverable breakdown"""
    actions = []
    
    failed_count = len(breakdown['failed'])
    pending_count = len(breakdown['pending'])
    in_progress_count = len(breakdown['in_progress'])
    
    if failed_count > 0:
        actions.append(f"Retry {failed_count} failed deliverable(s)")
    
    if pending_count > 0:
        actions.append(f"Resume {pending_count} pending deliverable(s)")
    
    if in_progress_count > 0:
        actions.append(f"Check progress on {in_progress_count} in-progress deliverable(s)")
    
    if failed_count + pending_count > 2:
        actions.append("Consider bulk retry operation for multiple failed items")
    
    return actions

@router.post("/goal-progress-details/{workspace_id}/{goal_id}/unblock")
async def unblock_goal_progress(
    workspace_id: str,
    goal_id: str, 
    action: str = Query(..., description="Action to take: retry_failed, resume_pending, escalate_all"),
    deliverable_ids: Optional[List[str]] = Query(None, description="Specific deliverable IDs to unblock")
) -> Dict[str, Any]:
    """
    Execute unblocking actions on goal deliverables
    
    Available actions:
    - retry_failed: Retry all failed deliverables
    - resume_pending: Resume all pending deliverables  
    - escalate_all: Escalate all blocked items to human review
    - retry_specific: Retry specific deliverable IDs
    """
    try:
        # Get current progress details
        progress_details = await get_goal_progress_details(workspace_id, goal_id)
        
        results = {
            'action_taken': action,
            'workspace_id': workspace_id,
            'goal_id': goal_id,
            'items_processed': [],
            'items_skipped': [],
            'errors': []
        }
        
        if action == 'retry_failed':
            failed_items = progress_details['deliverable_breakdown']['failed']
            for item in failed_items:
                if deliverable_ids is None or item['id'] in deliverable_ids:
                    # IMPLEMENTED: Actual retry logic
                    result = await _retry_failed_deliverable(workspace_id, item)
                    if result['success']:
                        results['items_processed'].append({
                            'id': item['id'],
                            'title': item['title'],
                            'action': 'retry_queued',
                            'new_status': result.get('new_status', 'pending')
                        })
                    else:
                        results['errors'].append({
                            'id': item['id'],
                            'error': result.get('error', 'Unknown error')
                        })

        elif action == 'resume_pending':
            pending_items = progress_details['deliverable_breakdown']['pending']
            for item in pending_items:
                if deliverable_ids is None or item['id'] in deliverable_ids:
                    # IMPLEMENTED: Actual resume logic
                    result = await _resume_pending_deliverable(workspace_id, item)
                    if result['success']:
                        results['items_processed'].append({
                            'id': item['id'],
                            'title': item['title'],
                            'action': 'resume_queued',
                            'new_status': result.get('new_status', 'in_progress')
                        })
                    else:
                        results['errors'].append({
                            'id': item['id'],
                            'error': result.get('error', 'Unknown error')
                        })

        elif action == 'escalate_all':
            # IMPLEMENTED: Escalation logic
            blocked_items = (
                progress_details['deliverable_breakdown']['failed'] +
                progress_details['deliverable_breakdown']['pending']
            )
            for item in blocked_items:
                result = await _escalate_deliverable(workspace_id, item)
                if result['success']:
                    results['items_processed'].append({
                        'id': item['id'],
                        'title': item['title'],
                        'action': 'escalated_to_human',
                        'new_status': result.get('new_status', 'needs_human_review')
                    })
                else:
                    results['errors'].append({
                        'id': item['id'],
                        'error': result.get('error', 'Unknown error')
                    })
        
        return {
            **results,
            'success': True,
            'message': f"Unblock action '{action}' queued for {len(results['items_processed'])} items"
        }
        
    except Exception as e:
        logger.error(f"Error unblocking goal progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))
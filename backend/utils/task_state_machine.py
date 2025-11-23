"""
Task Status State Machine

Enforces valid task status transitions to prevent invalid state changes.
Based on the TaskStatus enum: PENDING, IN_PROGRESS, COMPLETED, FAILED

State Diagram:
    ┌──────────────────────────────────────────────────────────────────┐
    │                                                                  │
    │                          ┌───────────┐                          │
    │               ┌─────────►│ COMPLETED │                          │
    │               │          └───────────┘                          │
    │               │                                                  │
    │  ┌────────┐   │   ┌─────────────┐                               │
    │  │PENDING │───┼──►│ IN_PROGRESS │───────────┐                   │
    │  └────────┘   │   └─────────────┘           │                   │
    │      │        │         │                    │                   │
    │      │        │         │                    ▼                   │
    │      │        │         │              ┌──────────┐             │
    │      └────────┼─────────┼─────────────►│  FAILED  │◄────────────┘
    │               │         │              └──────────┘
    │               │         │                    │
    │               │         │                    │
    │               │         └────────────────────┘
    │               │              (retry)
    │               │                │
    │               │                ▼
    │               │          ┌─────────┐
    │               └──────────│ PENDING │ (reset/retry)
    │                          └─────────┘
    └──────────────────────────────────────────────────────────────────┘

Transitions:
- PENDING -> IN_PROGRESS: Task execution started
- PENDING -> FAILED: Task failed before starting (validation error)
- IN_PROGRESS -> COMPLETED: Task finished successfully
- IN_PROGRESS -> FAILED: Task execution failed
- IN_PROGRESS -> PENDING: Task reset (retry)
- FAILED -> PENDING: Task retry requested
- FAILED -> IN_PROGRESS: Direct retry (immediate re-execution)
"""

import logging
from enum import Enum
from typing import Dict, Set, Optional, Tuple, Callable, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task status states - mirrors models.TaskStatus"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StateTransition:
    """Represents a state transition with metadata"""
    from_state: TaskStatus
    to_state: TaskStatus
    timestamp: datetime
    reason: Optional[str] = None
    actor: Optional[str] = None  # Who/what triggered the transition


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted"""
    def __init__(self, from_state: TaskStatus, to_state: TaskStatus, allowed: Set[TaskStatus]):
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = allowed
        super().__init__(
            f"Invalid transition from '{from_state.value}' to '{to_state.value}'. "
            f"Allowed transitions: {[s.value for s in allowed]}"
        )


class TaskStateMachine:
    """
    State machine for task status transitions.

    Enforces valid transitions and provides hooks for side effects.
    """

    # Valid transitions: from_state -> set of valid to_states
    VALID_TRANSITIONS: Dict[TaskStatus, Set[TaskStatus]] = {
        TaskStatus.PENDING: {
            TaskStatus.IN_PROGRESS,  # Task started
            TaskStatus.FAILED,       # Validation failure
            TaskStatus.COMPLETED,    # Instant completion (simple tasks)
        },
        TaskStatus.IN_PROGRESS: {
            TaskStatus.COMPLETED,    # Success
            TaskStatus.FAILED,       # Failure
            TaskStatus.PENDING,      # Reset/retry
        },
        TaskStatus.COMPLETED: {
            # Completed is typically terminal, but allow reset for correction
            TaskStatus.PENDING,      # Reset for re-execution
        },
        TaskStatus.FAILED: {
            TaskStatus.PENDING,      # Retry
            TaskStatus.IN_PROGRESS,  # Direct retry
        },
    }

    def __init__(self, task_id: str, initial_state: TaskStatus = TaskStatus.PENDING):
        self.task_id = task_id
        self._current_state = initial_state
        self._history: list[StateTransition] = []
        self._on_enter_callbacks: Dict[TaskStatus, list[Callable]] = {s: [] for s in TaskStatus}
        self._on_exit_callbacks: Dict[TaskStatus, list[Callable]] = {s: [] for s in TaskStatus}

    @property
    def current_state(self) -> TaskStatus:
        return self._current_state

    @property
    def history(self) -> list[StateTransition]:
        return self._history.copy()

    def can_transition(self, to_state: TaskStatus) -> bool:
        """Check if transition to given state is valid"""
        return to_state in self.VALID_TRANSITIONS.get(self._current_state, set())

    def get_valid_transitions(self) -> Set[TaskStatus]:
        """Get set of valid target states from current state"""
        return self.VALID_TRANSITIONS.get(self._current_state, set()).copy()

    def transition(
        self,
        to_state: TaskStatus,
        reason: Optional[str] = None,
        actor: Optional[str] = None,
        force: bool = False
    ) -> StateTransition:
        """
        Transition to a new state.

        Args:
            to_state: Target state
            reason: Optional reason for transition
            actor: Optional identifier of who/what triggered transition
            force: If True, skip validation (use with caution)

        Returns:
            StateTransition record

        Raises:
            InvalidTransitionError if transition is not valid and force=False
        """
        if not force and not self.can_transition(to_state):
            raise InvalidTransitionError(
                self._current_state,
                to_state,
                self.get_valid_transitions()
            )

        from_state = self._current_state

        # Execute exit callbacks for current state
        for callback in self._on_exit_callbacks[from_state]:
            try:
                callback(from_state, to_state, self.task_id)
            except Exception as e:
                logger.error(f"Exit callback error for task {self.task_id}: {e}")

        # Create transition record
        transition = StateTransition(
            from_state=from_state,
            to_state=to_state,
            timestamp=datetime.utcnow(),
            reason=reason,
            actor=actor
        )

        # Update state
        self._current_state = to_state
        self._history.append(transition)

        # Execute enter callbacks for new state
        for callback in self._on_enter_callbacks[to_state]:
            try:
                callback(from_state, to_state, self.task_id)
            except Exception as e:
                logger.error(f"Enter callback error for task {self.task_id}: {e}")

        logger.debug(
            f"Task {self.task_id} transitioned: {from_state.value} -> {to_state.value}"
            f"{f' (reason: {reason})' if reason else ''}"
        )

        return transition

    def on_enter(self, state: TaskStatus, callback: Callable) -> None:
        """Register callback to execute when entering a state"""
        self._on_enter_callbacks[state].append(callback)

    def on_exit(self, state: TaskStatus, callback: Callable) -> None:
        """Register callback to execute when exiting a state"""
        self._on_exit_callbacks[state].append(callback)

    def is_terminal(self) -> bool:
        """Check if current state is terminal (completed or failed)"""
        return self._current_state in {TaskStatus.COMPLETED, TaskStatus.FAILED}

    def is_active(self) -> bool:
        """Check if task is currently being processed"""
        return self._current_state == TaskStatus.IN_PROGRESS

    def get_state_duration(self) -> float:
        """Get duration in seconds since last state change"""
        if not self._history:
            return 0.0
        last_transition = self._history[-1]
        return (datetime.utcnow() - last_transition.timestamp).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """Convert state machine to dictionary for serialization"""
        return {
            "task_id": self.task_id,
            "current_state": self._current_state.value,
            "is_terminal": self.is_terminal(),
            "is_active": self.is_active(),
            "transition_count": len(self._history),
            "history": [
                {
                    "from": t.from_state.value,
                    "to": t.to_state.value,
                    "timestamp": t.timestamp.isoformat(),
                    "reason": t.reason,
                    "actor": t.actor
                }
                for t in self._history
            ]
        }


# =============================================================================
# VALIDATION UTILITIES
# =============================================================================

def validate_transition(
    current_status: str,
    new_status: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate if a status transition is allowed.

    Args:
        current_status: Current task status string
        new_status: Proposed new status string

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        from_state = TaskStatus(current_status)
        to_state = TaskStatus(new_status)
    except ValueError as e:
        return False, f"Invalid status value: {e}"

    valid_targets = TaskStateMachine.VALID_TRANSITIONS.get(from_state, set())

    if to_state in valid_targets:
        return True, None
    else:
        return False, (
            f"Invalid transition from '{current_status}' to '{new_status}'. "
            f"Allowed: {[s.value for s in valid_targets]}"
        )


def get_valid_next_states(current_status: str) -> list[str]:
    """
    Get list of valid next states for a given status.

    Args:
        current_status: Current task status string

    Returns:
        List of valid next status strings
    """
    try:
        from_state = TaskStatus(current_status)
        valid = TaskStateMachine.VALID_TRANSITIONS.get(from_state, set())
        return [s.value for s in valid]
    except ValueError:
        return []


def create_validated_update(
    current_status: str,
    new_status: str,
    reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a validated status update payload.

    Args:
        current_status: Current task status
        new_status: Proposed new status
        reason: Optional reason for the update

    Returns:
        Dict with status, is_valid, and metadata

    Raises:
        InvalidTransitionError if transition is not valid
    """
    is_valid, error = validate_transition(current_status, new_status)

    if not is_valid:
        raise InvalidTransitionError(
            TaskStatus(current_status),
            TaskStatus(new_status),
            set(TaskStatus(s) for s in get_valid_next_states(current_status))
        )

    return {
        "status": new_status,
        "previous_status": current_status,
        "transition_validated": True,
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat()
    }


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_task_state_machine(
    task_id: str,
    initial_status: str = "pending"
) -> TaskStateMachine:
    """
    Factory function to create a TaskStateMachine.

    Args:
        task_id: Unique task identifier
        initial_status: Initial status string (default: "pending")

    Returns:
        Configured TaskStateMachine instance
    """
    try:
        initial_state = TaskStatus(initial_status)
    except ValueError:
        logger.warning(f"Invalid initial status '{initial_status}', defaulting to 'pending'")
        initial_state = TaskStatus.PENDING

    return TaskStateMachine(task_id, initial_state)

import { NextRequest, NextResponse } from 'next/server'
import { getApiUrl } from '@/utils/environment'

export async function POST(request: NextRequest) {
  try {
    // Parse the request body
    const body = await request.json()

    // Forward the request to the FastAPI backend using centralized environment utility
    const backendUrl = getApiUrl()
    const response = await fetch(`${backendUrl}/api/analyze-task-business-value`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body)
    })
    
    if (!response.ok) {
      throw new Error(`Backend responded with status: ${response.status}`)
    }
    
    const data = await response.json()
    return NextResponse.json(data)
    
  } catch (error) {
    console.error('API proxy error:', error)
    return NextResponse.json(
      { error: 'Failed to proxy request to backend', details: error instanceof Error ? error.message : 'Unknown error' },
      { status: 500 }
    )
  }
}
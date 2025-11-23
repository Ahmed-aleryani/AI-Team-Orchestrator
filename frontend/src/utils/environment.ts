/**
 * Environment detection utilities for the AI Team Orchestrator
 *
 * CENTRALIZED API URL MANAGEMENT
 * All components/hooks MUST use these utilities instead of hardcoding URLs
 */

// Cache the API URL to avoid recalculation on every call
let cachedApiUrl: string | null = null
let cachedWsUrl: string | null = null

export const environment = {
  /**
   * Check if we're running in development mode
   */
  isDevelopment: (): boolean => {
    // Check multiple conditions for development
    return (
      process.env.NODE_ENV === 'development' ||
      (typeof window !== 'undefined' && (
        window.location.hostname === 'localhost' ||
        window.location.hostname === '127.0.0.1' ||
        window.location.hostname.includes('localhost')
      )) ||
      process.env.NEXT_PUBLIC_ENVIRONMENT === 'development'
    )
  },

  /**
   * Check if we're running in production mode
   */
  isProduction: (): boolean => {
    return !environment.isDevelopment()
  },

  /**
   * Get current environment string
   */
  getEnvironment: (): 'development' | 'production' => {
    return environment.isDevelopment() ? 'development' : 'production'
  },

  /**
   * Check if debug features should be enabled
   */
  isDebugEnabled: (): boolean => {
    return environment.isDevelopment() || process.env.NEXT_PUBLIC_DEBUG === 'true'
  },

  /**
   * Get API base URL based on environment (CENTRALIZED - use this everywhere)
   * Supports both server-side and client-side rendering
   *
   * Priority:
   * 1. NEXT_PUBLIC_API_URL environment variable (production)
   * 2. Window location-based detection (same-origin deployment)
   * 3. Default localhost for development
   */
  getApiUrl: (): string => {
    // Return cached value if available
    if (cachedApiUrl) return cachedApiUrl

    // Priority 1: Explicit environment variable
    if (process.env.NEXT_PUBLIC_API_URL) {
      cachedApiUrl = process.env.NEXT_PUBLIC_API_URL
      return cachedApiUrl
    }

    // Priority 2: Client-side detection for same-origin deployments
    if (typeof window !== 'undefined') {
      const { hostname, protocol, port } = window.location

      // Development localhost
      if (hostname === 'localhost' || hostname === '127.0.0.1') {
        cachedApiUrl = 'http://localhost:8000'
        return cachedApiUrl
      }

      // Production: assume API is at /api on same domain or port 8000
      // This handles deployments where frontend and backend share domain
      if (process.env.NEXT_PUBLIC_API_SAME_ORIGIN === 'true') {
        cachedApiUrl = `${protocol}//${hostname}${port ? `:${port}` : ''}`
      } else {
        // Default: API on port 8000 of same host
        cachedApiUrl = `${protocol}//${hostname}:8000`
      }
      return cachedApiUrl
    }

    // Server-side fallback
    cachedApiUrl = 'http://localhost:8000'
    return cachedApiUrl
  },

  /**
   * Get WebSocket base URL based on environment (CENTRALIZED)
   * Automatically converts http(s) to ws(s)
   */
  getWsUrl: (): string => {
    // Return cached value if available
    if (cachedWsUrl) return cachedWsUrl

    const apiUrl = environment.getApiUrl()
    cachedWsUrl = apiUrl.replace(/^http/, 'ws')
    return cachedWsUrl
  },

  /**
   * Build full API endpoint URL
   * @param path - API path (e.g., '/api/workspaces' or 'workspaces')
   * @returns Full URL (e.g., 'http://localhost:8000/api/workspaces')
   */
  buildApiUrl: (path: string): string => {
    const base = environment.getApiUrl()
    // Ensure path starts with /
    const normalizedPath = path.startsWith('/') ? path : `/${path}`
    // Ensure path includes /api prefix
    const apiPath = normalizedPath.startsWith('/api') ? normalizedPath : `/api${normalizedPath}`
    return `${base}${apiPath}`
  },

  /**
   * Build full WebSocket endpoint URL
   * @param path - WebSocket path (e.g., '/ws/tasks' or 'ws/tasks')
   * @returns Full WebSocket URL
   */
  buildWsUrl: (path: string): string => {
    const base = environment.getWsUrl()
    const normalizedPath = path.startsWith('/') ? path : `/${path}`
    return `${base}${normalizedPath}`
  },

  /**
   * Clear cached URLs (useful for testing or when env changes)
   */
  clearCache: (): void => {
    cachedApiUrl = null
    cachedWsUrl = null
  }
}

// Export individual functions for convenient imports
export const getApiUrl = environment.getApiUrl
export const getWsUrl = environment.getWsUrl
export const buildApiUrl = environment.buildApiUrl
export const buildWsUrl = environment.buildWsUrl

export default environment
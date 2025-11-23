'use client'

import React, { Component, ErrorInfo, ReactNode } from 'react'

/**
 * Error state displayed when an error boundary catches an error
 */
interface ErrorFallbackProps {
  error: Error
  errorInfo?: ErrorInfo
  resetError?: () => void
  componentStack?: string
}

/**
 * Default fallback UI for error boundary
 */
export const ErrorFallback: React.FC<ErrorFallbackProps> = ({
  error,
  errorInfo,
  resetError,
  componentStack
}) => {
  const isDevelopment = process.env.NODE_ENV === 'development'

  return (
    <div className="flex flex-col items-center justify-center min-h-[200px] p-6 bg-red-50 dark:bg-red-900/20 rounded-lg border border-red-200 dark:border-red-800">
      <div className="text-red-600 dark:text-red-400 mb-4">
        <svg
          className="w-12 h-12"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
          />
        </svg>
      </div>

      <h3 className="text-lg font-semibold text-red-800 dark:text-red-200 mb-2">
        Something went wrong
      </h3>

      <p className="text-sm text-red-600 dark:text-red-300 mb-4 text-center max-w-md">
        {error.message || 'An unexpected error occurred'}
      </p>

      {isDevelopment && componentStack && (
        <details className="w-full max-w-2xl mb-4">
          <summary className="cursor-pointer text-sm text-red-500 dark:text-red-400 hover:underline">
            View error details
          </summary>
          <pre className="mt-2 p-3 bg-red-100 dark:bg-red-900/40 rounded text-xs overflow-auto max-h-60 text-red-800 dark:text-red-200">
            {error.stack || 'No stack trace available'}
            {'\n\nComponent Stack:\n'}
            {componentStack}
          </pre>
        </details>
      )}

      <div className="flex gap-3">
        {resetError && (
          <button
            onClick={resetError}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-md transition-colors"
          >
            Try again
          </button>
        )}
        <button
          onClick={() => window.location.reload()}
          className="px-4 py-2 text-sm font-medium text-red-600 dark:text-red-400 bg-white dark:bg-gray-800 hover:bg-red-50 dark:hover:bg-red-900/20 border border-red-300 dark:border-red-700 rounded-md transition-colors"
        >
          Reload page
        </button>
      </div>
    </div>
  )
}

/**
 * Minimal error fallback for small components
 */
export const MinimalErrorFallback: React.FC<ErrorFallbackProps> = ({
  error,
  resetError
}) => {
  return (
    <div className="p-4 bg-red-50 dark:bg-red-900/20 rounded border border-red-200 dark:border-red-800">
      <p className="text-sm text-red-600 dark:text-red-400">
        Error: {error.message || 'Component failed to load'}
      </p>
      {resetError && (
        <button
          onClick={resetError}
          className="mt-2 text-xs text-red-500 hover:underline"
        >
          Retry
        </button>
      )}
    </div>
  )
}

/**
 * Props for ErrorBoundary component
 */
interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode | ((props: ErrorFallbackProps) => ReactNode)
  onError?: (error: Error, errorInfo: ErrorInfo) => void
  resetKeys?: any[]
}

/**
 * State for ErrorBoundary component
 */
interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
}

/**
 * React Error Boundary component for graceful error handling
 *
 * Usage:
 * ```tsx
 * <ErrorBoundary fallback={<ErrorFallback />}>
 *   <MyComponent />
 * </ErrorBoundary>
 *
 * // Or with custom fallback
 * <ErrorBoundary fallback={(props) => <CustomError {...props} />}>
 *   <MyComponent />
 * </ErrorBoundary>
 * ```
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null
    }
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Log error to console in development
    if (process.env.NODE_ENV === 'development') {
      console.error('ErrorBoundary caught an error:', error, errorInfo)
    }

    // Update state with error info
    this.setState({ errorInfo })

    // Call optional error handler
    this.props.onError?.(error, errorInfo)

    // In production, you might want to send to error tracking service
    // Example: sendToErrorTracking(error, errorInfo)
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    // Reset error state when resetKeys change
    if (
      this.state.hasError &&
      this.props.resetKeys &&
      !this.areResetKeysEqual(prevProps.resetKeys, this.props.resetKeys)
    ) {
      this.resetError()
    }
  }

  private areResetKeysEqual(a?: any[], b?: any[]): boolean {
    if (!a || !b || a.length !== b.length) return false
    return a.every((item, index) => item === b[index])
  }

  resetError = (): void => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null
    })
  }

  render(): ReactNode {
    const { hasError, error, errorInfo } = this.state
    const { children, fallback } = this.props

    if (hasError && error) {
      const errorProps: ErrorFallbackProps = {
        error,
        errorInfo: errorInfo || undefined,
        resetError: this.resetError,
        componentStack: errorInfo?.componentStack || undefined
      }

      if (typeof fallback === 'function') {
        return fallback(errorProps)
      }

      if (fallback) {
        return fallback
      }

      return <ErrorFallback {...errorProps} />
    }

    return children
  }
}

/**
 * Higher-order component for wrapping components with error boundary
 *
 * Usage:
 * ```tsx
 * const SafeComponent = withErrorBoundary(MyComponent, {
 *   fallback: <ErrorFallback />,
 *   onError: (error) => console.error(error)
 * })
 * ```
 */
export function withErrorBoundary<P extends object>(
  WrappedComponent: React.ComponentType<P>,
  errorBoundaryProps?: Omit<ErrorBoundaryProps, 'children'>
) {
  const displayName = WrappedComponent.displayName || WrappedComponent.name || 'Component'

  const ComponentWithErrorBoundary = (props: P) => (
    <ErrorBoundary {...errorBoundaryProps}>
      <WrappedComponent {...props} />
    </ErrorBoundary>
  )

  ComponentWithErrorBoundary.displayName = `withErrorBoundary(${displayName})`

  return ComponentWithErrorBoundary
}

/**
 * Custom hook for creating error boundary reset trigger
 *
 * Usage:
 * ```tsx
 * const { resetKey, triggerReset } = useErrorBoundaryReset()
 *
 * <ErrorBoundary resetKeys={[resetKey]}>
 *   <MyComponent onError={triggerReset} />
 * </ErrorBoundary>
 * ```
 */
export function useErrorBoundaryReset() {
  const [resetKey, setResetKey] = React.useState(0)

  const triggerReset = React.useCallback(() => {
    setResetKey((prev) => prev + 1)
  }, [])

  return { resetKey, triggerReset }
}

export default ErrorBoundary

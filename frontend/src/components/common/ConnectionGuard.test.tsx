import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConnectionGuard } from './ConnectionGuard'
import { getConfig } from '@/lib/config'

const { mockUsePathname } = vi.hoisted(() => ({
  mockUsePathname: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  usePathname: mockUsePathname,
}))

vi.mock('@/lib/config', () => ({
  getConfig: vi.fn(),
  resetConfig: vi.fn(),
}))

vi.mock('@/components/errors/ConnectionErrorOverlay', () => ({
  ConnectionErrorOverlay: () => <div>connection-error</div>,
}))

describe('ConnectionGuard', () => {
  beforeEach(() => {
    vi.mocked(getConfig).mockReset()
    mockUsePathname.mockReturnValue('/')
  })

  it('renders a visible loading state while the connection check is pending', () => {
    vi.mocked(getConfig).mockReturnValue(new Promise(() => undefined))

    render(
      <ConnectionGuard>
        <div>application</div>
      </ConnectionGuard>
    )

    expect(screen.getByRole('status')).toHaveTextContent('common.loading')
    expect(screen.getByRole('status')).toHaveAttribute(
      'data-course-workbench-ready',
      'connection-checking'
    )
    expect(screen.queryByText('application')).not.toBeInTheDocument()
  })

  it('exposes the exact new-course readiness marker during the route-specific SSR shell', () => {
    mockUsePathname.mockReturnValue('/courses/new')
    vi.mocked(getConfig).mockReturnValue(new Promise(() => undefined))

    render(
      <ConnectionGuard>
        <div>application</div>
      </ConnectionGuard>
    )

    expect(screen.getByRole('status')).toHaveAttribute(
      'data-course-workbench-ready',
      'new-course'
    )
  })

  it('uses the server-provided request path before usePathname hydrates', () => {
    mockUsePathname.mockReturnValue(null)
    vi.mocked(getConfig).mockReturnValue(new Promise(() => undefined))

    render(
      <ConnectionGuard initialPathname="/courses/new">
        <div>application</div>
      </ConnectionGuard>
    )

    expect(screen.getByRole('status')).toHaveAttribute(
      'data-course-workbench-ready',
      'new-course'
    )
  })
})

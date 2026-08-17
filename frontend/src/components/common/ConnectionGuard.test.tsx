import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConnectionGuard } from './ConnectionGuard'
import { getConfig } from '@/lib/config'

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
  })

  it('renders a visible loading state while the connection check is pending', () => {
    vi.mocked(getConfig).mockReturnValue(new Promise(() => undefined))

    render(
      <ConnectionGuard>
        <div>application</div>
      </ConnectionGuard>
    )

    expect(screen.getByRole('status')).toHaveTextContent('common.loading')
    expect(screen.queryByText('application')).not.toBeInTheDocument()
  })
})

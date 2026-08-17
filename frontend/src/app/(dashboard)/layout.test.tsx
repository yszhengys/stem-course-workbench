import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardLayout from './layout'

const { mockUsePathname } = vi.hoisted(() => ({ mockUsePathname: vi.fn() }))

vi.mock('next/navigation', () => ({
  usePathname: mockUsePathname,
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('@/lib/hooks/use-auth', () => ({
  useAuth: () => ({ isAuthenticated: false, isLoading: true }),
}))
vi.mock('@/lib/hooks/use-version-check', () => ({ useVersionCheck: vi.fn() }))
vi.mock('@/components/providers/ModalProvider', () => ({ ModalProvider: () => null }))
vi.mock('@/components/common/CommandPalette', () => ({ CommandPalette: () => null }))
vi.mock('@/lib/hooks/use-create-dialogs', () => ({
  CreateDialogsProvider: ({ children }: { children: React.ReactNode }) => children,
}))

describe('DashboardLayout startup shell', () => {
  beforeEach(() => mockUsePathname.mockReturnValue('/courses'))

  it('marks only the new-course route as launcher-ready during auth loading', () => {
    mockUsePathname.mockReturnValue('/courses/new')
    const { rerender } = render(<DashboardLayout><div>child</div></DashboardLayout>)
    expect(screen.getByRole('status')).toHaveAttribute('data-course-workbench-ready', 'new-course')

    mockUsePathname.mockReturnValue('/courses')
    rerender(<DashboardLayout><div>child</div></DashboardLayout>)
    expect(screen.getByRole('status')).not.toHaveAttribute('data-course-workbench-ready', 'new-course')
  })
})

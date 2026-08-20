import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import NewCoursePage from './page'
import { useCreateCourse } from '@/lib/hooks/use-courses'

const push = vi.fn()

vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/lib/hooks/use-courses', () => ({ useCreateCourse: vi.fn() }))

describe('NewCoursePage', () => {
  const mutateAsync = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useCreateCourse).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateCourse>)
  })

  it('always renders the stable launcher readiness marker and form', () => {
    render(<NewCoursePage />)
    expect(screen.getByTestId('new-course-ready')).toHaveAttribute(
      'data-course-workbench-ready',
      'new-course'
    )
    expect(screen.getByLabelText('course.titleLabel')).toBeVisible()
  })

  it('creates a course and safely encodes its record ID in the redirect', async () => {
    mutateAsync.mockResolvedValue({ id: 'course:微积分/一' })
    render(<NewCoursePage />)

    fireEvent.change(screen.getByLabelText('course.titleLabel'), {
      target: { value: '微积分' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.create' }))

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      title: '微积分',
      subject: 'math',
      description: null,
      language: 'zh-CN',
    }))
    expect(push).toHaveBeenCalledWith('/courses/course%3A%E5%BE%AE%E7%A7%AF%E5%88%86%2F%E4%B8%80/outline')
  })
})

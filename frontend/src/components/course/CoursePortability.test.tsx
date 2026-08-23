import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CoursePortability } from './CoursePortability'
import {
  useCreateCourseExport,
  useDownloadCourseExport,
  useImportCourseBundle,
} from '@/lib/hooks/use-courses'
import type { Course } from '@/lib/types/course'

vi.mock('@/lib/hooks/use-courses', () => ({
  useCreateCourseExport: vi.fn(),
  useDownloadCourseExport: vi.fn(),
  useImportCourseBundle: vi.fn(),
}))

const courses = [{
  id: 'course:calculus',
  title: 'Calculus',
  notebook: 'notebook:calculus',
  subject: 'math',
  description: null,
  language: 'zh-CN',
  status: 'ready',
  source_ids: [],
  primary_source_ids: [],
  supplement_source_ids: [],
  outline_version_id: null,
  error_message: null,
  outline: null,
  config: null,
  created: null,
  updated: null,
}] satisfies Course[]

describe('CoursePortability', () => {
  const createExport = vi.fn()
  const downloadExport = vi.fn()
  const importBundle = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useCreateCourseExport).mockReturnValue({
      mutateAsync: createExport,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateCourseExport>)
    vi.mocked(useDownloadCourseExport).mockReturnValue({
      mutateAsync: downloadExport,
      isPending: false,
    } as unknown as ReturnType<typeof useDownloadCourseExport>)
    vi.mocked(useImportCourseBundle).mockReturnValue({
      mutateAsync: importBundle,
      isPending: false,
    } as unknown as ReturnType<typeof useImportCourseBundle>)
  })

  it('exports only the selected course and downloads the verified bundle', async () => {
    createExport.mockResolvedValue({
      export_id: 'course_export:portable',
      course_id: 'course:calculus',
      status: 'succeeded',
      download_ready: true,
      manifest: null,
      error_message: null,
    })
    downloadExport.mockResolvedValue(undefined)
    render(<CoursePortability courses={courses} />)

    fireEvent.click(screen.getByLabelText('course.portabilityIncludeOriginals'))
    fireEvent.click(screen.getByRole('button', { name: 'course.portabilityCreateExport' }))

    await waitFor(() => expect(createExport).toHaveBeenCalledWith({
      courseId: 'course:calculus',
      includeOriginals: true,
    }))
    fireEvent.click(await screen.findByRole('button', { name: 'course.portabilityDownload' }))
    expect(downloadExport).toHaveBeenCalledWith({
      courseId: 'course:calculus',
      exportId: 'course_export:portable',
      filename: 'Calculus.stemcourse',
    })
  })

  it('rejects other extensions locally and links a successfully imported course', async () => {
    importBundle.mockResolvedValue({
      course_id: 'course:imported copy',
      course_title: 'Imported Calculus',
      record_counts: { course: 1 },
    })
    render(<CoursePortability courses={courses} />)
    const input = screen.getByLabelText('course.portabilityImportFile')

    fireEvent.change(input, {
      target: { files: [new File(['unsafe'], 'course.zip', { type: 'application/zip' })] },
    })
    expect(screen.getByText('course.portabilityInvalidFile')).toBeVisible()
    expect(screen.getByRole('button', { name: 'course.portabilityImport' })).toBeDisabled()

    fireEvent.change(input, {
      target: { files: [new File(['safe'], 'course.stemcourse')] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.portabilityImport' }))

    await waitFor(() => expect(importBundle).toHaveBeenCalledWith(expect.objectContaining({
      name: 'course.stemcourse',
    })))
    expect(await screen.findByRole('link', { name: 'Imported Calculus' })).toHaveAttribute(
      'href',
      '/courses/course%3Aimported%20copy/outline',
    )
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CourseLearnChapterPage from './page'
import {
  useAppendCourseLearningEvent,
  useCourse,
  useCourseExercises,
  useCourseLabs,
  useCourseLearningChapter,
  useCourseLearningNotes,
  useCourseLearningOverview,
  useCourseLearningSources,
  useCreateCourseLearningNote,
  useGradeCourseExercise,
  useGradeCourseTransfer,
  useNextCourseExerciseHint,
  useRevealCourseExerciseAnswer,
} from '@/lib/hooks/use-courses'

vi.mock('next/navigation', () => ({
  useParams: () => ({ courseId: 'course%3Aabc', chapterKey: 'limits' }),
}))
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/components/ui/markdown-renderer', () => ({
  MarkdownRenderer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/components/course/LabRenderer', () => ({
  LabRenderer: () => <div>course.labDataAlternative</div>,
}))
vi.mock('@/components/course/learning/ChapterTutor', () => ({
  ChapterTutor: () => <section>course.chapterTutor</section>,
}))
vi.mock('@/lib/hooks/use-courses', () => ({
  useAppendCourseLearningEvent: vi.fn(),
  useCourse: vi.fn(),
  useCourseExercises: vi.fn(),
  useCourseLabs: vi.fn(),
  useCourseLearningChapter: vi.fn(),
  useCourseLearningNotes: vi.fn(),
  useCourseLearningOverview: vi.fn(),
  useCourseLearningSources: vi.fn(),
  useCreateCourseLearningNote: vi.fn(),
  useGradeCourseExercise: vi.fn(),
  useGradeCourseTransfer: vi.fn(),
  useNextCourseExerciseHint: vi.fn(),
  useRevealCourseExerciseAnswer: vi.fn(),
}))

function queryResult(data: unknown) {
  return {
    data,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }
}

function mutationResult() {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }
}

const chapterSnapshot = 'a'.repeat(64)
const exerciseSnapshot = 'b'.repeat(64)

const mastery = {
  course_id: 'course:abc',
  course_version_id: 'course_version:published',
  chapter_key: 'limits',
  concept_key: 'limit-laws',
  status: 'practiced',
  successful_exercise_keys: ['limits-core'],
  unrevealed_success_count: 1,
  review_level: 0,
  review_due_at: null,
  last_event_at: '2026-08-22T08:00:00Z',
  snapshot_hash: 'c'.repeat(64),
}

const transfer = {
  key: 'limits-transfer',
  prompt: 'Transfer prompt',
  invariant_concept_keys: ['limit-laws'],
  dimensions: ['representation'],
  answer_type: 'numeric',
  answer_format: {
    kind: 'numeric', component_count: null, unit_required: false, parts: [],
  },
  difficulty: {
    concept_count: 1, reasoning_steps: 3, symbolic_depth: 1,
    representation_shifts: 1, proof_burden: 0, physics_constraints: 0,
  },
  anchor_ids: ['anchor:limits'],
}

const exercise = {
  key: 'limits-core',
  chapter_key: 'limits',
  prompt: 'Evaluate the limit.',
  concept_keys: ['continuity', 'limit-laws'],
  exercise_type: 'generated_core',
  answer_type: 'numeric',
  answer_format: {
    kind: 'numeric', component_count: null, unit_required: false, parts: [],
  },
  snapshot_token: exerciseSnapshot,
  source_anchor_ids: ['anchor:limits'],
  source_number: '4.1',
  source_section: 'Limits',
  difficulty: {
    concept_count: 2, reasoning_steps: 2, symbolic_depth: 1,
    representation_shifts: 0, proof_burden: 0, physics_constraints: 0,
  },
  is_core: true,
  is_gating: true,
  is_source_level: true,
  transfer,
}

const overviewData = () => ({
  course_id: 'course:abc',
  course_version_id: 'course_version:published',
  chapters: [{
    chapter_key: 'limits', chapter_no: 1, title: 'Limits',
    snapshot_token: chapterSnapshot, latest_position: null,
  }],
  concepts: [
    { key: 'continuity', label: '连续性' },
    { key: 'limit-laws', label: '极限定律' },
  ],
  masteries: [],
  review_queue: [{
    chapter_key: 'limits', concept_key: 'limit-laws', status: 'review_due',
    due_at: '2026-08-22T08:00:00Z', interval_days: 1,
  }],
})

const chapterData = () => ({
  course_id: 'course:abc',
  course_version_id: 'course_version:published',
  chapter_key: 'limits',
  chapter_no: 1,
  title: 'Limits',
  status: 'published',
  snapshot_token: chapterSnapshot,
  artifact: {
    purpose: 'Understand limits.',
    prerequisites: ['Algebra'],
    objectives: ['Apply limit laws'],
    definitions: ['A limit describes local behavior.'],
    sections: [{
      block_key: 'limits-definition',
      title: 'Definition',
      markdown: 'Grounded explanation.',
      anchor_ids: ['anchor:limits'],
      provenance: 'adapted',
    }],
    formulas: [],
    worked_examples: [],
    misconceptions: [],
    pitfalls: [],
    quick_reference: [],
    citations: ['anchor:limits'],
  },
})

describe('CourseLearnChapterPage', () => {
  const append = mutationResult()
  const grade = mutationResult()
  const gradeTransfer = mutationResult()
  const hint = mutationResult()
  const reveal = mutationResult()
  const createNote = mutationResult()
  const courseQuery = queryResult({ id: 'course:abc', title: 'Calculus', status: 'ready' })
  const overviewQuery = queryResult(overviewData())
  const chapterQuery = queryResult(chapterData())

  beforeEach(() => {
    vi.clearAllMocks()
    append.mutateAsync.mockResolvedValue({ event: {}, mastery: null })
    grade.mutateAsync.mockResolvedValue({
      grade: {
        correct: true, advisory: false, grants_mastery: true,
        feedback_code: 'correct', part_results: [],
      },
      mastery,
      event_key: 'grade-one',
      snapshot_token: exerciseSnapshot,
    })
    gradeTransfer.mutateAsync.mockResolvedValue({
      grade: {
        correct: true, advisory: false, grants_mastery: true,
        feedback_code: 'correct', part_results: [],
      },
      mastery: { ...mastery, status: 'mastered' },
      event_key: 'transfer-one',
      snapshot_token: exerciseSnapshot,
    })
    hint.mutateAsync.mockResolvedValue({
      snapshot_token: exerciseSnapshot,
      hint_index: 1,
      total_hints: 4,
      hint: 'Hint one',
      event: {},
      mastery: null,
    })
    reveal.mutateAsync.mockResolvedValue({
      snapshot_token: exerciseSnapshot,
      answer: 'Complete answer: 4',
      transfer,
      events: [{}, {}],
      mastery: null,
    })
    vi.mocked(useAppendCourseLearningEvent).mockReturnValue(append as never)
    vi.mocked(useGradeCourseExercise).mockReturnValue(grade as never)
    vi.mocked(useGradeCourseTransfer).mockReturnValue(gradeTransfer as never)
    vi.mocked(useNextCourseExerciseHint).mockReturnValue(hint as never)
    vi.mocked(useRevealCourseExerciseAnswer).mockReturnValue(reveal as never)
    vi.mocked(useCreateCourseLearningNote).mockReturnValue(createNote as never)
    vi.mocked(useCourse).mockReturnValue(courseQuery as never)
    vi.mocked(useCourseLearningOverview).mockReturnValue(overviewQuery as never)
    vi.mocked(useCourseLearningChapter).mockReturnValue(chapterQuery as never)
    vi.mocked(useCourseLearningSources).mockReturnValue(queryResult({
      snapshot_token: chapterSnapshot,
      sources: [{
        anchor_id: 'private-anchor', filename: 'Limits.pdf', kind: 'pdf_page', index: 4,
        quote: 'The source definition of a limit.', source_role: 'PRIMARY', bbox: null,
      }],
    }) as never)
    vi.mocked(useCourseLearningNotes).mockReturnValue(queryResult({
      snapshot_token: chapterSnapshot,
      notes: [{
        note_id: 'course_note:one', block_key: 'limits-definition',
        content: 'Approach is not equality.', orphan_status: 'active', created: null,
      }],
    }) as never)
    vi.mocked(useCourseExercises).mockReturnValue(queryResult([exercise]) as never)
    vi.mocked(useCourseLabs).mockReturnValue(queryResult([{
      id: 'course_lab:limits', lab_key: 'limits-lab',
      lab_type: 'function_plot', spec: {},
    }]) as never)
  })

  it('fetches one recorded hint and grades the selected due concept', async () => {
    render(<CourseLearnChapterPage />)

    expect(screen.queryByText('Hint one')).not.toBeInTheDocument()
    expect(screen.queryByText('Complete answer: 4')).not.toBeInTheDocument()
    expect(screen.queryByText('anchor:limits')).not.toBeInTheDocument()
    expect(screen.queryByText('limit-laws')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'course.nextHint' }))
    expect(await screen.findByText('Hint one')).toBeVisible()
    expect(hint.mutateAsync).toHaveBeenCalledWith({
      exerciseKey: 'limits-core',
      request: expect.objectContaining({
        snapshot_token: exerciseSnapshot,
        chapter_key: 'limits',
        concept_key: 'limit-laws',
        hint_index: 1,
      }),
    })

    fireEvent.change(screen.getByRole('textbox', { name: 'course.exerciseAnswer' }), {
      target: { value: '4' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.checkAnswer' }))

    await waitFor(() => expect(grade.mutateAsync).toHaveBeenCalledWith({
      exerciseKey: 'limits-core',
      request: expect.objectContaining({
        snapshot_token: exerciseSnapshot,
        chapter_key: 'limits',
        concept_key: 'limit-laws',
        answer: '4',
        hints_used: 1,
        answer_revealed: false,
        mode: 'review',
      }),
    }))
    expect(await screen.findByText('course.gradeCorrect')).toBeVisible()
  })

  it('shows only current-publication sources and saves a snapshot-bound note', async () => {
    render(<CourseLearnChapterPage />)

    expect(screen.getByText('course.chapterTutor')).toBeVisible()
    expect(screen.getByText('Limits.pdf')).toBeVisible()
    expect(screen.getByText('The source definition of a limit.')).toBeVisible()
    expect(screen.getByText('Approach is not equality.')).toBeVisible()
    expect(document.body.textContent).not.toContain('private-anchor')
    expect(document.body.textContent).not.toContain('course_note:one')

    fireEvent.change(screen.getByLabelText('course.notePlaceholder'), {
      target: { value: 'Remember the epsilon definition.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.saveNote' }))
    await waitFor(() => expect(createNote.mutateAsync).toHaveBeenCalledWith({
      snapshot_token: chapterSnapshot,
      block_key: 'limits-definition',
      content: 'Remember the epsilon definition.',
    }))
  })

  it('fails closed for source and note data from a different publication', () => {
    vi.mocked(useCourseLearningSources).mockReturnValue(queryResult({
      snapshot_token: 'd'.repeat(64),
      sources: [{
        anchor_id: 'stale-anchor', filename: 'Old.pdf', kind: 'pdf_page', index: 1,
        quote: 'Stale source quote.', source_role: 'PRIMARY', bbox: null,
      }],
    }) as never)
    vi.mocked(useCourseLearningNotes).mockReturnValue(queryResult({
      snapshot_token: 'd'.repeat(64),
      notes: [{
        note_id: 'course_note:stale', block_key: 'limits-definition',
        content: 'Stale note.', orphan_status: 'active', created: null,
      }],
    }) as never)

    render(<CourseLearnChapterPage />)

    expect(screen.queryByText('Stale source quote.')).not.toBeInTheDocument()
    expect(screen.queryByText('Stale note.')).not.toBeInTheDocument()
    expect(screen.getByText('course.learningSnapshotChanged')).toBeVisible()
  })

  it('requires confirmation, records the reveal server-side and completes transfer', async () => {
    render(<CourseLearnChapterPage />)

    fireEvent.click(screen.getByRole('button', { name: 'course.revealAnswer' }))
    expect(screen.queryByText('Complete answer: 4')).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'course.confirmReveal' }))

    expect(await screen.findByText('Complete answer: 4')).toBeVisible()
    expect(screen.getByText('Transfer prompt')).toBeVisible()
    expect(reveal.mutateAsync).toHaveBeenCalledWith({
      exerciseKey: 'limits-core',
      request: expect.objectContaining({
        snapshot_token: exerciseSnapshot,
        concept_key: 'limit-laws',
      }),
    })

    const answerFields = screen.getAllByRole('textbox', { name: 'course.exerciseAnswer' })
    fireEvent.change(answerFields.at(-1)!, { target: { value: '4' } })
    fireEvent.click(screen.getByRole('button', { name: 'course.checkTransferAnswer' }))

    await waitFor(() => expect(gradeTransfer.mutateAsync).toHaveBeenCalledWith({
      exerciseKey: 'limits-core',
      request: expect.objectContaining({
        snapshot_token: exerciseSnapshot,
        source_attempt_key: expect.stringMatching(/^attempt-/),
        transfer_task_key: 'limits-transfer',
        answer: '4',
      }),
    }))
    expect(await screen.findByText('course.transferCompleted')).toBeVisible()
  })

  it('fails closed when overview and chapter snapshots do not match', async () => {
    vi.mocked(useCourseLearningChapter).mockReturnValue(queryResult({
      ...chapterData(), snapshot_token: 'd'.repeat(64),
    }) as never)

    render(<CourseLearnChapterPage />)

    expect(screen.queryByRole('heading', { name: 'Limits' })).not.toBeInTheDocument()
    await waitFor(() => expect(append.mutateAsync).not.toHaveBeenCalled())
  })

  it('reloads all learner data after a publication conflict', async () => {
    const conflict = Object.assign(new Error('stale publication'), {
      response: { status: 409 },
    })
    grade.mutateAsync.mockRejectedValueOnce(conflict)
    render(<CourseLearnChapterPage />)

    fireEvent.change(screen.getByRole('textbox', { name: 'course.exerciseAnswer' }), {
      target: { value: '4' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.checkAnswer' }))

    expect(await screen.findByText('course.learningSnapshotChanged')).toBeVisible()
    await waitFor(() => {
      expect(overviewQuery.refetch).toHaveBeenCalled()
      expect(chapterQuery.refetch).toHaveBeenCalled()
    })
  })

  it('uses distinct idempotency keys for long reading block keys', async () => {
    const common = `block-${'a'.repeat(76)}`
    vi.mocked(useCourseLearningChapter).mockReturnValue(queryResult({
      ...chapterData(),
      artifact: {
        ...chapterData().artifact,
        sections: [
          {
            block_key: `${common}-one`, title: 'First long block', markdown: 'One.',
            anchor_ids: [], provenance: 'derived',
          },
          {
            block_key: `${common}-two`, title: 'Second long block', markdown: 'Two.',
            anchor_ids: [], provenance: 'derived',
          },
        ],
      },
    }) as never)
    render(<CourseLearnChapterPage />)

    fireEvent.focus(screen.getByRole('region', { name: 'First long block' }))
    fireEvent.focus(screen.getByRole('region', { name: 'Second long block' }))

    await waitFor(() => {
      const positions = append.mutateAsync.mock.calls
        .map(([request]) => request)
        .filter((request) => request.kind === 'reading_position')
      expect(positions).toHaveLength(2)
      expect(positions[0].idempotency_key).not.toBe(positions[1].idempotency_key)
    })
  })
})

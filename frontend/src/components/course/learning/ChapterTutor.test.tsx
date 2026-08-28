import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChapterTutor } from './ChapterTutor'
import {
  useCourseModelOptions,
  useCourseTutorSessions,
  useCreateCourseTutorSession,
  useSendCourseTutorMessage,
} from '@/lib/hooks/use-courses'
import type { CourseExercise, CourseTutorSession } from '@/lib/types/course'

vi.mock('@/lib/hooks/use-courses', () => ({
  useCourseModelOptions: vi.fn(),
  useCourseTutorSessions: vi.fn(),
  useCreateCourseTutorSession: vi.fn(),
  useSendCourseTutorMessage: vi.fn(),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { index?: number }) => (
      values?.index ? `${key} ${values.index}` : key
    ),
  }),
}))

const createSession = vi.fn()
const sendMessage = vi.fn()
const refetch = vi.fn()
const snapshot = 'a'.repeat(64)

function queryResult(data: unknown) {
  return {
    data,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch,
  }
}

const exercise: CourseExercise = {
  key: 'limits-core',
  chapter_key: 'limits',
  prompt: 'Evaluate the limit.',
  concept_keys: ['limit-laws', 'continuity'],
  exercise_type: 'generated_core',
  answer_type: 'numeric',
  answer_format: { kind: 'numeric', component_count: null, unit_required: false, parts: [] },
  snapshot_token: 'b'.repeat(64),
  source_anchor_ids: ['anchor:limit'],
  source_number: null,
  source_section: null,
  difficulty: {
    concept_count: 2,
    reasoning_steps: 2,
    symbolic_depth: 1,
    representation_shifts: 0,
    proof_burden: 0,
    physics_constraints: 0,
  },
  is_core: true,
  is_gating: true,
  is_source_level: false,
  verification: {
    level: 'L2',
    method: 'deterministic_solver',
    anchor_ids: [],
    reason: 'Deterministic answer check transcript sha256:abc',
    verified_at: null,
  },
  learning_blocked_reason: null,
  transfer: {
    key: 'limits-core-transfer',
    prompt: 'Apply it to a graph.',
    invariant_concept_keys: ['limit-laws', 'continuity'],
    dimensions: ['representation'],
    answer_type: 'numeric',
    answer_format: { kind: 'numeric', component_count: null, unit_required: false, parts: [] },
    difficulty: {
      concept_count: 2,
      reasoning_steps: 2,
      symbolic_depth: 1,
      representation_shifts: 1,
      proof_burden: 0,
      physics_constraints: 0,
    },
    anchor_ids: ['anchor:limit'],
  },
}

function session(status: CourseTutorSession['status'] = 'active'): CourseTutorSession {
  return {
    session_id: `course_tutor_session:${status}`,
    course_version_id: status === 'stale' ? 'course_version:old' : 'course_version:published',
    chapter_key: 'limits',
    model: { adapter: 'codex_cli', model: 'gpt-5.6-sol', reasoning_effort: 'max' },
    status,
    turns: [
      {
        turn_no: 1,
        role: 'user',
        content: 'Explain the definition.',
        anchor_ids: [],
        answer_revealed: false,
      },
      {
        turn_no: 2,
        role: 'assistant',
        content: 'A grounded explanation.',
        anchor_ids: ['anchor:limit'],
        answer_revealed: false,
      },
    ],
    created: '2026-08-24T08:00:00+00:00',
  }
}

describe('ChapterTutor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    createSession.mockResolvedValue(session())
    sendMessage.mockResolvedValue({})
    vi.mocked(useCourseModelOptions).mockReturnValue(queryResult({
      defaults: {},
      options: [{
        adapter: 'codex_cli',
        model: 'gpt-5.6-sol',
        reasoning_effort: 'max',
        reasoning_efforts: ['max'],
        optional: false,
        configured: true,
        selectable: true,
        name: 'GPT-5.6 Sol',
      }],
    }) as never)
    vi.mocked(useCourseTutorSessions).mockReturnValue(queryResult([]) as never)
    vi.mocked(useCreateCourseTutorSession).mockReturnValue({
      mutateAsync: createSession,
      isPending: false,
    } as never)
    vi.mocked(useSendCourseTutorMessage).mockReturnValue({
      mutateAsync: sendMessage,
      isPending: false,
    } as never)
  })

  it('requires an explicit model before creating a version-bound session', async () => {
    render(
      <ChapterTutor
        courseId="course:one"
        courseVersionId="course_version:published"
        chapterKey="limits"
        snapshotToken={snapshot}
        exercises={[exercise]}
        concepts={[
          { key: 'limit-laws', label: 'Limit laws' },
          { key: 'continuity', label: 'Continuity' },
        ]}
      />,
    )

    const create = screen.getByRole('button', { name: 'course.startTutorSession' })
    expect(create).toBeDisabled()
    fireEvent.change(screen.getByLabelText('course.tutorModel — course.modelLabel'), {
      target: { value: 'codex_cli|gpt-5.6-sol' },
    })
    fireEvent.click(create)

    await waitFor(() => expect(createSession).toHaveBeenCalledWith({
      snapshot_token: snapshot,
      chapter_key: 'limits',
      model: {
        adapter: 'codex_cli',
        model: 'gpt-5.6-sol',
        reasoning_effort: 'max',
      },
    }))
  })

  it('shows cited turns and requires confirmation for an exact reveal scope', async () => {
    vi.mocked(useCourseTutorSessions).mockReturnValue(
      queryResult([session()]) as never,
    )
    render(
      <ChapterTutor
        courseId="course:one"
        courseVersionId="course_version:published"
        chapterKey="limits"
        snapshotToken={snapshot}
        exercises={[exercise]}
        concepts={[
          { key: 'limit-laws', label: 'Limit laws' },
          { key: 'continuity', label: 'Continuity' },
        ]}
      />,
    )

    expect(screen.getByText('A grounded explanation.')).toBeInTheDocument()
    expect(screen.getByText('course.tutorCitation 1').closest('a')).toHaveAttribute(
      'href', '#course-source-anchor-3Alimit',
    )
    fireEvent.change(screen.getByLabelText('course.tutorIntent'), {
      target: { value: 'reveal' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorRevealExercise'), {
      target: { value: 'limits-core' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorRevealConcept'), {
      target: { value: 'continuity' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorMessage'), {
      target: { value: 'Reveal the complete answer.' },
    })
    const send = screen.getByRole('button', { name: 'course.sendTutorMessage' })
    expect(send).toBeDisabled()
    fireEvent.click(screen.getByLabelText('course.confirmTutorReveal'))
    fireEvent.click(send)

    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1))
    const request = sendMessage.mock.calls[0][0]
    expect(request).toMatchObject({
      snapshot_token: snapshot,
      idempotency_key: expect.stringMatching(/^tutor-/),
      content: 'Reveal the complete answer.',
      intent: 'reveal',
      exercise_key: 'limits-core',
      concept_key: 'continuity',
    })
    expect(request.attempt_key).toMatch(/^tutor-/)
    expect(request).not.toHaveProperty('anchor_ids')
  })

  it('binds hints and diagnoses to an active exercise attempt', async () => {
    vi.mocked(useCourseTutorSessions).mockReturnValue(
      queryResult([session()]) as never,
    )
    render(
      <ChapterTutor
        courseId="course:one"
        courseVersionId="course_version:published"
        chapterKey="limits"
        snapshotToken={snapshot}
        exercises={[exercise]}
        concepts={[{ key: 'limit-laws', label: 'Limit laws' }]}
        attempts={[{
          exerciseKey: 'limits-core',
          conceptKey: 'limit-laws',
          attemptKey: 'attempt-live-one',
          graded: true,
        }]}
      />,
    )

    fireEvent.change(screen.getByLabelText('course.tutorIntent'), {
      target: { value: 'hint' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorMessage'), {
      target: { value: 'Give me the next hint.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.sendTutorMessage' }))

    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        intent: 'hint',
        exercise_key: 'limits-core',
        concept_key: 'limit-laws',
        attempt_key: 'attempt-live-one',
        idempotency_key: expect.stringMatching(/^tutor-/),
      }),
    ))
  })

  it('keeps stale sessions readable and disables further messages', () => {
    vi.mocked(useCourseTutorSessions).mockReturnValue(
      queryResult([session('stale')]) as never,
    )
    render(
      <ChapterTutor
        courseId="course:one"
        courseVersionId="course_version:published"
        chapterKey="limits"
        snapshotToken={snapshot}
        exercises={[exercise]}
        concepts={[
          { key: 'limit-laws', label: 'Limit laws' },
          { key: 'continuity', label: 'Continuity' },
        ]}
      />,
    )

    expect(screen.getByText('A grounded explanation.')).toBeInTheDocument()
    expect(screen.getByText('course.tutorSessionReadOnly')).toBeInTheDocument()
    expect(screen.getByLabelText('course.tutorMessage')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'course.sendTutorMessage' })).toBeDisabled()
  })

  it('reuses the exact reveal attempt identity after a transient failure', async () => {
    vi.mocked(useCourseTutorSessions).mockReturnValue(
      queryResult([session()]) as never,
    )
    sendMessage.mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({})
    render(
      <ChapterTutor
        courseId="course:one"
        courseVersionId="course_version:published"
        chapterKey="limits"
        snapshotToken={snapshot}
        exercises={[exercise]}
        concepts={[{ key: 'limit-laws', label: 'Limit laws' }]}
      />,
    )
    fireEvent.change(screen.getByLabelText('course.tutorIntent'), {
      target: { value: 'reveal' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorRevealExercise'), {
      target: { value: 'limits-core' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorRevealConcept'), {
      target: { value: 'limit-laws' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorMessage'), {
      target: { value: 'Reveal the answer.' },
    })
    fireEvent.click(screen.getByLabelText('course.confirmTutorReveal'))
    const send = screen.getByRole('button', { name: 'course.sendTutorMessage' })

    fireEvent.click(send)
    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1))
    fireEvent.click(send)
    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(2))

    expect(sendMessage.mock.calls[0][0].attempt_key).toBe(
      sendMessage.mock.calls[1][0].attempt_key,
    )
    expect(sendMessage.mock.calls[0][0].idempotency_key).toBe(
      sendMessage.mock.calls[1][0].idempotency_key,
    )
  })

  it('rotates request identity when a failed reveal request is edited', async () => {
    vi.mocked(useCourseTutorSessions).mockReturnValue(
      queryResult([session()]) as never,
    )
    sendMessage.mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({})
    render(
      <ChapterTutor
        courseId="course:one"
        courseVersionId="course_version:published"
        chapterKey="limits"
        snapshotToken={snapshot}
        exercises={[exercise]}
        concepts={[
          { key: 'limit-laws', label: 'Limit laws' },
          { key: 'continuity', label: 'Continuity' },
        ]}
      />,
    )
    fireEvent.change(screen.getByLabelText('course.tutorIntent'), {
      target: { value: 'reveal' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorRevealExercise'), {
      target: { value: 'limits-core' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorRevealConcept'), {
      target: { value: 'limit-laws' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorMessage'), {
      target: { value: 'Reveal the answer.' },
    })
    fireEvent.click(screen.getByLabelText('course.confirmTutorReveal'))
    const send = screen.getByRole('button', { name: 'course.sendTutorMessage' })
    fireEvent.click(send)
    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('course.tutorRevealConcept'), {
      target: { value: 'continuity' },
    })
    fireEvent.change(screen.getByLabelText('course.tutorMessage'), {
      target: { value: 'Reveal the revised request.' },
    })
    fireEvent.click(screen.getByLabelText('course.confirmTutorReveal'))
    fireEvent.click(send)
    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(2))

    expect(sendMessage.mock.calls[1][0].idempotency_key).not.toBe(
      sendMessage.mock.calls[0][0].idempotency_key,
    )
    expect(sendMessage.mock.calls[1][0].attempt_key).not.toBe(
      sendMessage.mock.calls[0][0].attempt_key,
    )
  })
})

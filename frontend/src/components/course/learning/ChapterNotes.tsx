'use client'

import { useMemo, useState } from 'react'
import { NotebookPen } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useCreateCourseLearningNote } from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  CourseLearnerChapterArtifact,
  CourseLearnerNotesResponse,
} from '@/lib/types/course'

export function ChapterNotes({
  courseId,
  chapterKey,
  snapshotToken,
  artifact,
  response,
}: {
  courseId: string
  chapterKey: string
  snapshotToken: string
  artifact: CourseLearnerChapterArtifact
  response: CourseLearnerNotesResponse
}) {
  const { t } = useTranslation()
  const createNote = useCreateCourseLearningNote(courseId, chapterKey)
  const [blockKey, setBlockKey] = useState(artifact.sections[0]?.block_key ?? '')
  const [content, setContent] = useState('')
  const labels = useMemo(
    () => new Map(artifact.sections.map((section) => [section.block_key, section.title])),
    [artifact.sections],
  )
  const currentSnapshot = response.snapshot_token === snapshotToken

  async function saveNote() {
    const normalized = content.trim()
    if (!currentSnapshot || !blockKey || !normalized) return
    await createNote.mutateAsync({
      snapshot_token: snapshotToken,
      block_key: blockKey,
      content: normalized,
    })
    setContent('')
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <NotebookPen className="size-5" aria-hidden="true" />
          {t('course.notes')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {!currentSnapshot && (
          <p role="alert" className="text-sm text-destructive">
            {t('course.learningSnapshotChanged')}
          </p>
        )}
        <div className="space-y-2">
          <Label htmlFor="learner-note-block">{t('course.selectBlock')}</Label>
          <select
            id="learner-note-block"
            value={blockKey}
            disabled={!currentSnapshot}
            onChange={(event) => setBlockKey(event.target.value)}
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            {artifact.sections.map((section) => (
              <option key={section.block_key} value={section.block_key}>{section.title}</option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="learner-note-content">{t('course.notePlaceholder')}</Label>
          <Textarea
            id="learner-note-content"
            value={content}
            maxLength={20_000}
            disabled={!currentSnapshot}
            placeholder={t('course.notePlaceholder')}
            onChange={(event) => setContent(event.target.value)}
          />
        </div>
        <Button
          type="button"
          disabled={!currentSnapshot || !blockKey || !content.trim() || createNote.isPending}
          onClick={() => void saveNote()}
        >
          {t('course.saveNote')}
        </Button>

        <div className="space-y-3" aria-live="polite">
          {response.notes.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('course.noLearningNotes')}</p>
          ) : response.notes.map((note) => (
            <article key={note.note_id} className="rounded-md border p-3">
              <h3 className="text-sm font-semibold">
                {labels.get(note.block_key) ?? t('course.noteBlockUnavailable')}
              </h3>
              <p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">
                {note.content}
              </p>
            </article>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

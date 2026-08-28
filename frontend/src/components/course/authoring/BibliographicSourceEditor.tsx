'use client'

import { useMemo, useState } from 'react'

import { CourseInlineError, CourseInlineLoading } from '@/components/course/CoursePageState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { sourceRoleLabel } from '@/lib/course/course-labels'
import {
  useCourseBibliography,
  useSaveCourseBibliography,
} from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  BibliographicSource,
  CourseBibliographyUpdateRequest,
  EligibleCourseSource,
} from '@/lib/types/course'

interface BibliographicSourceEditorProps {
  courseId: string
  sources: EligibleCourseSource[]
}

interface BibliographyDraft {
  authors: string
  title: string
  edition: string
  publisher: string
  year: string
  doi: string
  isbn: string
  license: string
  manuallyReviewed: boolean
}

const EMPTY_DRAFT: BibliographyDraft = {
  authors: '',
  title: '',
  edition: '',
  publisher: '',
  year: '',
  doi: '',
  isbn: '',
  license: '',
  manuallyReviewed: false,
}

function recordDraft(record?: BibliographicSource): BibliographyDraft {
  if (!record) return { ...EMPTY_DRAFT }
  return {
    authors: record.authors.join('\n'),
    title: record.title ?? '',
    edition: record.edition ?? '',
    publisher: record.publisher ?? '',
    year: record.year?.toString() ?? '',
    doi: record.doi ?? '',
    isbn: record.isbn ?? '',
    license: record.license ?? '',
    manuallyReviewed: record.manually_reviewed,
  }
}

function optionalText(value: string): string | null {
  const clean = value.trim()
  return clean || null
}

function requestFromDraft(
  draft: BibliographyDraft,
  record?: BibliographicSource,
): CourseBibliographyUpdateRequest {
  const year = draft.year.trim()
  return {
    expected_updated: record?.updated ?? null,
    authors: draft.authors
      .split(/\r?\n|;/)
      .map((author) => author.trim())
      .filter(Boolean),
    title: optionalText(draft.title),
    edition: optionalText(draft.edition),
    publisher: optionalText(draft.publisher),
    year: year ? Number(year) : null,
    doi: optionalText(draft.doi),
    isbn: optionalText(draft.isbn),
    license: optionalText(draft.license),
    manually_reviewed: draft.manuallyReviewed,
  }
}

export function BibliographicSourceEditor({
  courseId,
  sources,
}: BibliographicSourceEditorProps) {
  const { t } = useTranslation()
  const bibliography = useCourseBibliography(courseId)
  const save = useSaveCourseBibliography(courseId)
  const [drafts, setDrafts] = useState<Record<string, BibliographyDraft>>({})
  const associated = sources.filter(
    (source) => source.associated && source.role !== null,
  )
  const records = useMemo(
    () => new Map(
      (bibliography.data ?? []).map((record) => [record.source, record]),
    ),
    [bibliography.data],
  )
  const secondaryFields = [
    { field: 'edition', label: t('course.bibliographyEdition'), maxLength: 100 },
    { field: 'publisher', label: t('course.bibliographyPublisher'), maxLength: 300 },
    { field: 'doi', label: t('course.bibliographyDoi'), maxLength: 255 },
    { field: 'isbn', label: t('course.bibliographyIsbn'), maxLength: 40 },
    { field: 'license', label: t('course.bibliographyLicense'), maxLength: 200 },
  ] as const

  const updateDraft = <Key extends keyof BibliographyDraft>(
    sourceId: string,
    record: BibliographicSource | undefined,
    key: Key,
    value: BibliographyDraft[Key],
  ) => {
    setDrafts((current) => ({
      ...current,
      [sourceId]: {
        ...(current[sourceId] ?? recordDraft(record)),
        [key]: value,
      },
    }))
  }

  if (bibliography.isLoading) return <CourseInlineLoading />
  if (bibliography.isError) {
    return <CourseInlineError onRetry={() => void bibliography.refetch()} />
  }
  if (associated.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {t('course.noBibliographicSources')}
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {associated.map((source) => {
        const record = records.get(source.source_id)
        const draft = drafts[source.source_id] ?? recordDraft(record)
        const prefix = `bibliography-${source.source_id.replace(/[^a-zA-Z0-9_-]/g, '-')}`
        const displayTitle = source.title?.trim() || t('course.untitledSource')
        return (
          <section key={source.source_id} className="space-y-4 rounded-md border p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-display font-bold">{displayTitle}</h3>
              <Badge variant="outline">
                {sourceRoleLabel(t, source.role as 'PRIMARY' | 'SUPPLEMENT')}
              </Badge>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2 md:col-span-2">
                <Label htmlFor={`${prefix}-authors`}>
                  {t('course.bibliographyAuthors')}
                </Label>
                <Textarea
                  id={`${prefix}-authors`}
                  value={draft.authors}
                  maxLength={4020}
                  placeholder={t('course.bibliographyAuthorsHint')}
                  onChange={(event) => updateDraft(
                    source.source_id, record, 'authors', event.target.value,
                  )}
                />
              </div>
              <div className="space-y-2 md:col-span-2">
                <Label htmlFor={`${prefix}-title`}>
                  {t('course.bibliographyTitleField')}
                </Label>
                <Input
                  id={`${prefix}-title`}
                  value={draft.title}
                  maxLength={500}
                  onChange={(event) => updateDraft(
                    source.source_id, record, 'title', event.target.value,
                  )}
                />
              </div>
              {secondaryFields.map(({ field, label, maxLength }) => (
                <div key={field} className="space-y-2">
                  <Label htmlFor={`${prefix}-${field}`}>{label}</Label>
                  <Input
                    id={`${prefix}-${field}`}
                    value={draft[field]}
                    maxLength={maxLength}
                    onChange={(event) => updateDraft(
                      source.source_id, record, field, event.target.value,
                    )}
                  />
                </div>
              ))}
              <div className="space-y-2">
                <Label htmlFor={`${prefix}-year`}>
                  {t('course.bibliographyYear')}
                </Label>
                <Input
                  id={`${prefix}-year`}
                  type="number"
                  min={1000}
                  max={2100}
                  value={draft.year}
                  onChange={(event) => updateDraft(
                    source.source_id, record, 'year', event.target.value,
                  )}
                />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                checked={draft.manuallyReviewed}
                onChange={(event) => updateDraft(
                  source.source_id,
                  record,
                  'manuallyReviewed',
                  event.target.checked,
                )}
              />
              {t('course.bibliographyManualReview')}
            </label>

            <Button
              type="button"
              disabled={save.isPending}
              onClick={() => void save.mutateAsync({
                sourceId: source.source_id,
                request: requestFromDraft(draft, record),
              })}
            >
              {save.isPending ? t('common.processing') : t('course.saveBibliography')}
            </Button>
          </section>
        )
      })}
    </div>
  )
}

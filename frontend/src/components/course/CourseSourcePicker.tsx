'use client'

import Link from 'next/link'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { EligibleCourseSource, SourceRole } from '@/lib/types/course'

interface CourseSourcePickerProps {
  sources: EligibleCourseSource[]
  sourceId: string
  role: SourceRole
  onSourceIdChange: (sourceId: string) => void
  onRoleChange: (role: SourceRole) => void
  disabled?: boolean
}

export function CourseSourcePicker({
  sources,
  sourceId,
  role,
  onSourceIdChange,
  onRoleChange,
  disabled = false,
}: CourseSourcePickerProps) {
  const { t } = useTranslation()

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="course-source-picker">{t('course.sourcePicker')}</Label>
        <select
          id="course-source-picker"
          aria-label={t('course.sourcePicker')}
          value={sources.some((source) => source.source_id === sourceId) ? sourceId : ''}
          onChange={(event) => onSourceIdChange(event.target.value)}
          disabled={disabled || sources.length === 0}
          className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
        >
          <option value="">{t('course.selectSource')}</option>
          {sources.map((source) => (
            <option key={source.source_id} value={source.source_id}>
              {source.filename}
            </option>
          ))}
        </select>
        {sources.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {t('course.noEligibleSources')}{' '}
            <Link className="font-medium text-primary underline-offset-4 hover:underline" href="/sources">
              {t('course.goToSources')}
            </Link>
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="course-source-id">{t('course.manualSourceId')}</Label>
        <Input
          id="course-source-id"
          aria-label={t('course.manualSourceId')}
          value={sourceId}
          onChange={(event) => onSourceIdChange(event.target.value)}
          placeholder="source:..."
          disabled={disabled}
        />
        <p className="text-xs text-muted-foreground">{t('course.manualSourceHint')}</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="course-source-role">{t('course.sourceRole')}</Label>
        <select
          id="course-source-role"
          value={role}
          onChange={(event) => onRoleChange(event.target.value as SourceRole)}
          disabled={disabled}
          className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
        >
          <option value="PRIMARY">{t('course.primary')}</option>
          <option value="SUPPLEMENT">{t('course.supplement')}</option>
        </select>
      </div>
    </div>
  )
}

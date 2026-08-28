'use client'

import { useMemo, useState } from 'react'
import { AlertTriangle, Download } from 'lucide-react'

import { CourseInlineError, CourseInlineLoading } from '@/components/course/CoursePageState'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { courseApi } from '@/lib/api/course'
import { sourceRoleLabel } from '@/lib/course/course-labels'
import { useCourseCoverage } from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { CourseCoverageRowFlag } from '@/lib/types/course'

interface CoverageReportProps {
  courseId: string
  enabled: boolean
}

const FILTERS: CourseCoverageRowFlag[] = [
  'unused',
  'low_confidence',
  'supplement_only',
  'missing_bibliography',
]

export function CoverageReport({ courseId, enabled }: CoverageReportProps) {
  const { t } = useTranslation()
  const coverage = useCourseCoverage(courseId, enabled)
  const [filters, setFilters] = useState<CourseCoverageRowFlag[]>([])
  const [isDownloading, setIsDownloading] = useState(false)
  const [downloadFailed, setDownloadFailed] = useState(false)

  const flagLabel = (flag: CourseCoverageRowFlag) => {
    switch (flag) {
      case 'unused': return t('course.coverageFilterUnused')
      case 'low_confidence': return t('course.coverageFilterLowConfidence')
      case 'supplement_only': return t('course.coverageFilterSupplementOnly')
      case 'missing_bibliography': return t('course.coverageFilterMissingBibliography')
    }
  }

  const rows = useMemo(() => {
    const all = coverage.data?.rows ?? []
    if (filters.length === 0) return all
    return all.filter((row) => filters.every((flag) => row.flags.includes(flag)))
  }, [coverage.data?.rows, filters])

  const toggleFilter = (flag: CourseCoverageRowFlag, checked: boolean) => {
    setFilters((current) => checked
      ? [...new Set([...current, flag])]
      : current.filter((item) => item !== flag))
  }

  const download = async () => {
    setDownloadFailed(false)
    setIsDownloading(true)
    try {
      await courseApi.downloadCoverage(courseId)
    } catch {
      setDownloadFailed(true)
    } finally {
      setIsDownloading(false)
    }
  }

  if (!enabled) {
    return <p className="text-sm text-muted-foreground">{t('course.coverageRequiresOutline')}</p>
  }
  if (coverage.isLoading) return <CourseInlineLoading />
  if (coverage.isError || !coverage.data) {
    return <CourseInlineError onRetry={() => void coverage.refetch()} />
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('course.coverageNotQualityScore')}
        </p>
        <Button
          type="button"
          variant="outline"
          disabled={isDownloading}
          onClick={() => void download()}
        >
          <Download className="mr-2 size-4" />
          {t('course.coverageDownload')}
        </Button>
      </div>

      {downloadFailed && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>{t('common.error')}</AlertTitle>
          <AlertDescription>{t('course.coverageDownloadFailed')}</AlertDescription>
        </Alert>
      )}

      {coverage.data.flags.includes('generation_limit_exceeded') && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>{t('course.coverageGenerationLimit')}</AlertTitle>
          <AlertDescription>{t('course.coverageGenerationLimitDescription')}</AlertDescription>
        </Alert>
      )}

      {coverage.data.chapter_flags.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950/30">
          <p className="font-medium">{t('course.coverageChapterFlags')}</p>
          <ul className="mt-2 space-y-1">
            {coverage.data.chapter_flags.map((item) => (
              <li key={item.chapter_key} className="flex flex-wrap items-center gap-2">
                <code>{item.chapter_key}</code>
                <Badge variant="outline">{t('course.coverageNoAnswerSource')}</Badge>
              </li>
            ))}
          </ul>
        </div>
      )}

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">{t('course.coverageFilters')}</legend>
        <div className="flex flex-wrap gap-4">
          {FILTERS.map((flag) => {
            const id = `coverage-filter-${flag}`
            return (
              <div key={flag} className="flex items-center gap-2">
                <Checkbox
                  id={id}
                  checked={filters.includes(flag)}
                  onCheckedChange={(checked) => toggleFilter(flag, checked === true)}
                />
                <Label htmlFor={id}>{flagLabel(flag)}</Label>
              </div>
            )
          })}
        </div>
      </fieldset>

      <div className="overflow-x-auto rounded-md border">
        <table className="w-full min-w-[960px] text-left text-sm">
          <thead className="border-b bg-muted/50">
            <tr>
              <th className="p-3 font-medium">{t('course.coverageSource')}</th>
              <th className="p-3 font-medium">{t('course.coverageLocator')}</th>
              <th className="p-3 font-medium">{t('course.coverageClassification')}</th>
              <th className="p-3 font-medium">{t('course.coverageUsages')}</th>
              <th className="p-3 font-medium">{t('course.coverageFlags')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.anchor_id} className="border-b align-top last:border-b-0">
                <td className="space-y-2 p-3">
                  <code className="block break-all">{row.source_id}</code>
                  <Badge variant="outline">{sourceRoleLabel(t, row.source_role)}</Badge>
                </td>
                <td className="space-y-1 p-3">
                  <p>
                    {row.locator.kind === 'pdf_page'
                      ? t('course.coveragePage')
                      : t('course.coverageSlide')}{' '}
                    {row.locator.index}
                  </p>
                  <p className="font-medium">{row.locator.block_key}</p>
                  <code className="block max-w-64 truncate text-xs text-muted-foreground" title={row.anchor_id}>
                    {row.anchor_id}
                  </code>
                </td>
                <td className="p-3">
                  <p>{row.category}</p>
                  <p className="text-xs text-muted-foreground">{row.confidence}</p>
                </td>
                <td className="p-3">
                  {row.usages.length ? (
                    <ul className="space-y-1">
                      {row.usages.map((usage) => (
                        <li key={`${usage.kind}:${usage.chapter_key ?? ''}:${usage.key}`}>
                          <span className="text-muted-foreground">{usage.kind}</span>{' '}
                          <code>{usage.key}</code>
                        </li>
                      ))}
                    </ul>
                  ) : <span className="text-muted-foreground">{t('course.coverageNoUsage')}</span>}
                </td>
                <td className="p-3">
                  {row.flags.length ? (
                    <div className="flex max-w-64 flex-wrap gap-1">
                      {row.flags.map((flag) => (
                        <Badge key={flag} variant="secondary">{flagLabel(flag)}</Badge>
                      ))}
                    </div>
                  ) : <span className="text-muted-foreground">{t('course.coverageNoFlags')}</span>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="p-8 text-center text-muted-foreground">
                  {t('course.coverageNoRows')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="break-all text-xs text-muted-foreground">
        {t('course.coverageReportHash')}: <code>{coverage.data.report_hash}</code>
      </p>
    </div>
  )
}

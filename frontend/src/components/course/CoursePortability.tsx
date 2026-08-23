'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Archive, Download, Upload } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  useCreateCourseExport,
  useDownloadCourseExport,
  useImportCourseBundle,
} from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { Course, CourseBundleImportResponse, CourseExportResponse } from '@/lib/types/course'

function safeBundleName(title: string): string {
  const stem = title.trim().replace(/[\\/:*?"<>|]/g, '-').slice(0, 120) || 'course'
  return `${stem}.stemcourse`
}

export function CoursePortability({ courses }: { courses: Course[] }) {
  const { t } = useTranslation()
  const createExport = useCreateCourseExport()
  const downloadExport = useDownloadCourseExport()
  const importBundle = useImportCourseBundle()
  const [selectedCourseId, setSelectedCourseId] = useState('')
  const [includeOriginals, setIncludeOriginals] = useState(false)
  const [exported, setExported] = useState<CourseExportResponse | null>(null)
  const [bundle, setBundle] = useState<File | null>(null)
  const [invalidFile, setInvalidFile] = useState(false)
  const [imported, setImported] = useState<CourseBundleImportResponse | null>(null)
  const courseId = selectedCourseId || courses[0]?.id || ''
  const selectedCourse = courses.find((course) => course.id === courseId)

  const handleExport = async () => {
    if (!courseId) return
    const result = await createExport.mutateAsync({ courseId, includeOriginals })
    setExported(result)
  }

  const handleFile = (file: File | undefined) => {
    const valid = Boolean(file?.name.toLowerCase().endsWith('.stemcourse'))
    setInvalidFile(Boolean(file) && !valid)
    setBundle(valid && file ? file : null)
    setImported(null)
  }

  const handleImport = async () => {
    if (!bundle) return
    setImported(await importBundle.mutateAsync(bundle))
  }

  return (
    <section className="grid gap-4 lg:grid-cols-2" aria-labelledby="course-portability-title">
      <div className="lg:col-span-2">
        <h2 id="course-portability-title" className="font-display text-xl font-semibold">
          {t('course.portabilityTitle')}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">{t('course.portabilityDescription')}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Archive className="size-5" />{t('course.portabilityExport')}</CardTitle>
          <CardDescription>{t('course.portabilityExportDescription')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="course-export-select">{t('course.portabilityCourse')}</Label>
            <select
              id="course-export-select"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={courseId}
              onChange={(event) => {
                setSelectedCourseId(event.target.value)
                setExported(null)
              }}
              disabled={!courses.length}
            >
              {courses.map((course) => <option key={course.id} value={course.id}>{course.title}</option>)}
            </select>
          </div>
          <label className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              aria-label={t('course.portabilityIncludeOriginals')}
              className="mt-1 size-4"
              checked={includeOriginals}
              onChange={(event) => setIncludeOriginals(event.target.checked)}
            />
            <span>
              <span className="block font-medium">{t('course.portabilityIncludeOriginals')}</span>
              <span className="text-muted-foreground">{t('course.portabilityIncludeOriginalsHint')}</span>
            </span>
          </label>
          <Button onClick={() => void handleExport()} disabled={!courseId || createExport.isPending}>
            <Archive className="mr-2 size-4" />
            {createExport.isPending ? t('common.processing') : t('course.portabilityCreateExport')}
          </Button>
          {exported?.download_ready && selectedCourse ? (
            <Button
              variant="outline"
              onClick={() => void downloadExport.mutateAsync({
                courseId: exported.course_id,
                exportId: exported.export_id,
                filename: safeBundleName(selectedCourse.title),
              })}
              disabled={downloadExport.isPending}
            >
              <Download className="mr-2 size-4" />{t('course.portabilityDownload')}
            </Button>
          ) : null}
          {exported?.error_message ? <p className="text-sm text-destructive">{exported.error_message}</p> : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Upload className="size-5" />{t('course.portabilityImport')}</CardTitle>
          <CardDescription>{t('course.portabilityImportDescription')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="course-import-file">{t('course.portabilityImportFile')}</Label>
            <Input
              id="course-import-file"
              type="file"
              accept=".stemcourse,application/octet-stream"
              onChange={(event) => handleFile(event.target.files?.[0])}
            />
            {invalidFile ? <p className="text-sm text-destructive">{t('course.portabilityInvalidFile')}</p> : null}
          </div>
          <Button onClick={() => void handleImport()} disabled={!bundle || importBundle.isPending}>
            <Upload className="mr-2 size-4" />
            {importBundle.isPending ? t('common.processing') : t('course.portabilityImport')}
          </Button>
          {imported ? (
            <p className="text-sm text-fern">
              {t('course.portabilityImportSucceeded')}{' '}
              <Link className="font-medium underline" href={`/courses/${encodeURIComponent(imported.course_id)}/outline`}>
                {imported.course_title}
              </Link>
            </p>
          ) : null}
        </CardContent>
      </Card>
    </section>
  )
}

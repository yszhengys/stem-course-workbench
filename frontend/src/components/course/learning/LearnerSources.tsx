'use client'

import { useEffect, useState } from 'react'
import { Download, ExternalLink, LibraryBig } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { courseApi } from '@/lib/api/course'
import { courseCitationTarget } from '@/lib/course/citations'
import { locatorKindLabel, sourceRoleLabel } from '@/lib/course/course-labels'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  CourseLearnerSource,
  CourseLearnerSourcesResponse,
} from '@/lib/types/course'

function SourceCard({
  courseId,
  source,
}: {
  courseId: string
  source: CourseLearnerSource
}) {
  const { t } = useTranslation()
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewFailed, setPreviewFailed] = useState(false)
  const [sourcePending, setSourcePending] = useState(false)
  const [sourceFailed, setSourceFailed] = useState(false)
  const isPdf = source.kind === 'pdf_page'

  useEffect(() => {
    if (isPdf) return
    let active = true
    let objectUrl: string | null = null
    setPreviewUrl(null)
    setPreviewFailed(false)
    void courseApi.getEvidencePreviewBlob(courseId, source.anchor_id)
      .then((blob) => {
        if (!active) return
        objectUrl = URL.createObjectURL(blob)
        setPreviewUrl(objectUrl)
      })
      .catch(() => {
        if (active) setPreviewFailed(true)
      })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [courseId, isPdf, source.anchor_id])

  async function loadSource(): Promise<string | null> {
    setSourcePending(true)
    setSourceFailed(false)
    try {
      const blob = await courseApi.getEvidenceSourceBlob(courseId, source.anchor_id)
      return URL.createObjectURL(blob)
    } catch {
      setSourceFailed(true)
      return null
    } finally {
      setSourcePending(false)
    }
  }

  async function openPdfPage() {
    const popup = window.open('about:blank', '_blank')
    if (!popup) {
      setSourceFailed(true)
      return
    }
    popup.opener = null
    const objectUrl = await loadSource()
    if (!objectUrl) {
      popup.close()
      return
    }
    popup.location.href = `${objectUrl}#page=${source.index}`
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  }

  async function downloadPptx() {
    const objectUrl = await loadSource()
    if (!objectUrl) return
    const download = document.createElement('a')
    download.href = objectUrl
    download.download = source.filename
    download.rel = 'noreferrer'
    download.click()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  }

  return (
    <article
      id={courseCitationTarget(source.anchor_id)}
      className="scroll-mt-6 space-y-3 rounded-md border bg-background p-4 text-sm"
    >
      <div>
        <h3 className="font-semibold">{source.filename}</h3>
        <p className="text-xs text-muted-foreground">
          {sourceRoleLabel(t, source.source_role)} · {locatorKindLabel(t, source.kind)} {source.index}
        </p>
        <code className="break-all text-xs text-muted-foreground">
          {source.anchor_id}
        </code>
      </div>
      <blockquote className="border-l-2 pl-3 text-muted-foreground">
        {source.quote}
      </blockquote>

      {!isPdf && (
        previewFailed ? (
          <p className="text-xs text-destructive">{t('course.sourcePreviewFailed')}</p>
        ) : previewUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={previewUrl}
            alt={t('course.slidePreview', { slide: source.index })}
            width={640}
            height={360}
            loading="lazy"
            className="h-auto w-full rounded border bg-muted"
            onError={() => setPreviewFailed(true)}
          />
        ) : (
          <div
            aria-busy="true"
            className="aspect-video w-full animate-pulse rounded border bg-muted"
          />
        )
      )}

      <div className="flex flex-wrap gap-3 text-xs font-medium">
        <button
          type="button"
          disabled={sourcePending}
          onClick={() => void (isPdf ? openPdfPage() : downloadPptx())}
          className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline disabled:opacity-50"
        >
          {isPdf ? <ExternalLink className="size-3.5" aria-hidden="true" /> : (
            <Download className="size-3.5" aria-hidden="true" />
          )}
          {isPdf
            ? t('course.openSourcePage', { page: source.index })
            : t('course.downloadOriginal')}
        </button>
      </div>
      {sourceFailed && (
        <p className="text-xs text-destructive">{t('course.operationFailed')}</p>
      )}
    </article>
  )
}

export function LearnerSources({
  courseId,
  response,
}: {
  courseId: string
  response: CourseLearnerSourcesResponse
}) {
  const { t } = useTranslation()
  if (response.sources.length === 0) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <LibraryBig className="size-5" aria-hidden="true" />
          {t('course.learningSources')}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        {response.sources.map((source) => (
          <SourceCard
            key={`${source.kind}-${source.index}-${source.anchor_id}`}
            courseId={courseId}
            source={source}
          />
        ))}
      </CardContent>
    </Card>
  )
}

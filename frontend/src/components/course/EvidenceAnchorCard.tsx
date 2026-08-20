'use client'

import { useEffect, useState } from 'react'
import { Download, ExternalLink } from 'lucide-react'

import { Checkbox } from '@/components/ui/checkbox'
import { courseApi } from '@/lib/api/course'
import { locatorKindLabel, sourceRoleLabel } from '@/lib/course/course-labels'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { EvidenceAnchor } from '@/lib/types/course'

interface EvidenceAnchorCardProps {
  courseId: string
  anchor: EvidenceAnchor
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}

export function EvidenceAnchorCard({
  courseId,
  anchor,
  checked,
  onCheckedChange,
}: EvidenceAnchorCardProps) {
  const { t } = useTranslation()
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewFailed, setPreviewFailed] = useState(false)
  const [sourcePending, setSourcePending] = useState(false)
  const [sourceFailed, setSourceFailed] = useState(false)
  const checkboxId = `evidence-${anchor.anchor_id.replace(/[^a-zA-Z0-9_-]/g, '-')}`
  const isPdf = anchor.locator.kind === 'pdf_page'

  useEffect(() => {
    setPreviewUrl(null)
    setPreviewFailed(false)
    if (isPdf || !anchor.preview_path) return

    let active = true
    let objectUrl: string | null = null
    void courseApi.getEvidencePreviewBlob(courseId, anchor.anchor_id)
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
  }, [anchor.anchor_id, anchor.preview_path, courseId, isPdf])

  async function loadSource(): Promise<string | null> {
    setSourcePending(true)
    setSourceFailed(false)
    try {
      const blob = await courseApi.getEvidenceSourceBlob(courseId, anchor.anchor_id)
      return URL.createObjectURL(blob)
    } catch {
      setSourceFailed(true)
      return null
    } finally {
      setSourcePending(false)
    }
  }

  async function openPdfPage() {
    const objectUrl = await loadSource()
    if (!objectUrl) return
    window.open(
      `${objectUrl}#page=${anchor.locator.index}`,
      '_blank',
      'noopener,noreferrer',
    )
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  }

  async function downloadPptx() {
    const objectUrl = await loadSource()
    if (!objectUrl) return
    const download = document.createElement('a')
    download.href = objectUrl
    download.download = `course-source-slide-${anchor.locator.index}.pptx`
    download.rel = 'noreferrer'
    download.click()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  }

  return (
    <article className="space-y-3 rounded-md border bg-background p-3 text-sm">
      <div className="flex items-start gap-3">
        <Checkbox
          id={checkboxId}
          checked={checked}
          aria-label={`${t('course.evidenceAnchors')}: ${anchor.locator.quote}`}
          onCheckedChange={(value) => onCheckedChange(value === true)}
        />
        <label htmlFor={checkboxId} className="min-w-0 flex-1 cursor-pointer">
          <span className="block font-medium">
            {sourceRoleLabel(t, anchor.source_role)} ·{' '}
            {locatorKindLabel(t, anchor.locator.kind)} {anchor.locator.index}
          </span>
          <span className="mt-1 block text-muted-foreground">
            {anchor.locator.quote}
          </span>
        </label>
      </div>

      {!isPdf && (
        <div className="space-y-2">
          {!anchor.preview_path ? (
            <p className="text-xs text-muted-foreground">
              {t('course.previewUnavailable')}
            </p>
          ) : previewFailed ? (
            <p className="text-xs text-destructive">
              {t('course.sourcePreviewFailed')}
            </p>
          ) : previewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={previewUrl}
              alt={t('course.slidePreview', { slide: anchor.locator.index })}
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
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-3 text-xs font-medium">
        {isPdf ? (
          <button
            type="button"
            disabled={sourcePending}
            onClick={() => void openPdfPage()}
            className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
          >
            <ExternalLink className="size-3.5" />
            {t('course.openSourcePage', { page: anchor.locator.index })}
          </button>
        ) : (
          <button
            type="button"
            disabled={sourcePending}
            onClick={() => void downloadPptx()}
            className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
          >
            <Download className="size-3.5" />
            {t('course.downloadOriginal')}
          </button>
        )}
      </div>
      {sourceFailed && (
        <p className="text-xs text-destructive">{t('course.operationFailed')}</p>
      )}
    </article>
  )
}

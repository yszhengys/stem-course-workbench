'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { AlertTriangle, ArrowLeft, BookOpen, CheckCircle2, FileText, Network } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { CommandJobPanel } from '@/components/course/CommandJobPanel'
import { CourseModelPicker } from '@/components/course/CourseModelPicker'
import { CoursePageError, CoursePageLoading, CoursePageNotFound } from '@/components/course/CoursePageState'
import { CourseSourcePicker } from '@/components/course/CourseSourcePicker'
import { OutlineApproval } from '@/components/course/OutlineApproval'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { submitEvidenceSource } from '@/lib/course/evidence-source'
import { selectableDefaultModel } from '@/lib/course/model-selection'
import { useCommandStatus } from '@/lib/hooks/use-command-status'
import {
  useApproveCourseOutline,
  useAssociateCourseSource,
  useBuildCourseEvidence,
  useCourse,
  useCourseAnchors,
  useCourseModelOptions,
  useCurrentCourseOutline,
  useEligibleCourseSources,
  useGenerateCourseOutline,
} from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { ModelSelection, SourceRole } from '@/lib/types/course'
import { isNotFoundError } from '@/lib/utils/error-handler'

export default function CourseOutlinePage() {
  const { t } = useTranslation()
  const params = useParams()
  const courseId = params?.courseId ? decodeURIComponent(params.courseId as string) : ''
  const course = useCourse(courseId)
  const sources = useEligibleCourseSources(courseId)
  const anchors = useCourseAnchors(courseId)
  const models = useCourseModelOptions()
  const outline = useCurrentCourseOutline(courseId, Boolean(course.data?.outline_version_id))
  const associateSource = useAssociateCourseSource(courseId)
  const buildEvidence = useBuildCourseEvidence(courseId)
  const generateOutline = useGenerateCourseOutline(courseId)
  const approveOutline = useApproveCourseOutline(courseId)

  const [sourceId, setSourceId] = useState('')
  const [sourceRole, setSourceRole] = useState<SourceRole>('PRIMARY')
  const [selectedAnchorIds, setSelectedAnchorIds] = useState<string[]>([])
  const [outlineModel, setOutlineModel] = useState<ModelSelection | null>(null)
  const [evidenceCommandId, setEvidenceCommandId] = useState<string>()
  const [outlineCommandId, setOutlineCommandId] = useState<string>()

  const evidenceStatus = useCommandStatus(evidenceCommandId, [
    QUERY_KEYS.course(courseId),
    QUERY_KEYS.courseSources(courseId),
    QUERY_KEYS.courseAnchors(courseId),
    QUERY_KEYS.courseOutline(courseId),
  ])
  const outlineStatus = useCommandStatus(outlineCommandId, [
    QUERY_KEYS.course(courseId),
    QUERY_KEYS.courseOutline(courseId),
  ])

  useEffect(() => {
    if (!models.data || outlineModel) return
    setOutlineModel(selectableDefaultModel(models.data.options, models.data.defaults.outline))
  }, [models.data, outlineModel])

  useEffect(() => {
    if (!anchors.data) return
    const current = new Set(anchors.data.map((anchor) => anchor.anchor_id))
    setSelectedAnchorIds((previous) => {
      const retained = previous.filter((anchorId) => current.has(anchorId))
      return retained.length ? retained : [...current]
    })
  }, [anchors.data])

  const currentOutline = outline.data
  const outlineArtifact = currentOutline?.outline_artifact
  const approved = Boolean(currentOutline?.approved_at)
  const anchorsById = useMemo(
    () => new Map((anchors.data ?? []).map((anchor) => [anchor.anchor_id, anchor])),
    [anchors.data]
  )

  const handleSourceChange = (nextSourceId: string) => {
    setSourceId(nextSourceId)
    const source = sources.data?.find((item) => item.source_id === nextSourceId)
    if (source?.role) setSourceRole(source.role)
  }

  const handleEvidenceBuild = async () => {
    if (!sourceId.trim()) return
    const job = await submitEvidenceSource({
      sourceId,
      role: sourceRole,
      sources: sources.data ?? [],
      associate: associateSource.mutateAsync,
      build: buildEvidence.mutateAsync,
    })
    setEvidenceCommandId(job.command_id)
  }

  const handleOutlineGenerate = async () => {
    if (!outlineModel || selectedAnchorIds.length === 0) return
    const job = await generateOutline.mutateAsync({
      anchor_ids: selectedAnchorIds,
      prompt_version: 'v1',
      model: outlineModel,
      force: false,
    })
    setOutlineCommandId(job.command_id)
  }

  if (course.isLoading) {
    return <AppShell><CoursePageLoading /></AppShell>
  }

  if (course.isError && isNotFoundError(course.error)) {
    return <AppShell><div className="flex-1 overflow-y-auto p-6"><CoursePageNotFound /></div></AppShell>
  }

  if (course.isError || !course.data) {
    return <AppShell><div className="flex-1 overflow-y-auto p-6"><CoursePageError onRetry={() => void course.refetch()} /></div></AppShell>
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl space-y-6 p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <Button asChild variant="ghost" size="sm" className="-ml-3 mb-2">
                <Link href="/courses"><ArrowLeft className="mr-2 size-4" />{t('course.backToCourses')}</Link>
              </Button>
              <h1 className="font-display text-2xl font-bold">{course.data.title}</h1>
              <p className="mt-1 text-sm text-muted-foreground">{t('course.outlineWorkspace')}</p>
            </div>
            <Badge variant="secondary">{course.data.status}</Badge>
          </div>

          {(course.data.status === 'failed' || course.data.error_message) && (
            <Alert variant="destructive">
              <AlertTriangle />
              <AlertTitle>{t('course.blocked')}</AlertTitle>
              <AlertDescription>{course.data.error_message || t('course.operationFailed')}</AlertDescription>
            </Alert>
          )}

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><FileText className="size-5 text-gold" />{t('course.sourceAndEvidence')}</CardTitle>
                <CardDescription>{t('course.sourceAndEvidenceDescription')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                {sources.isLoading ? <CoursePageLoading /> : sources.isError ? (
                  <CoursePageError onRetry={() => void sources.refetch()} />
                ) : (
                  <CourseSourcePicker
                    sources={sources.data ?? []}
                    sourceId={sourceId}
                    role={sourceRole}
                    onSourceIdChange={handleSourceChange}
                    onRoleChange={setSourceRole}
                    disabled={buildEvidence.isPending || evidenceStatus.isFetching}
                  />
                )}
                <Button
                  type="button"
                  onClick={() => void handleEvidenceBuild()}
                  disabled={!sourceId.trim() || buildEvidence.isPending || evidenceStatus.isFetching}
                >
                  {buildEvidence.isPending ? t('common.processing') : t('course.buildEvidence')}
                </Button>
                <CommandJobPanel
                  status={evidenceCommandId ? evidenceStatus.status : undefined}
                  errorMessage={evidenceStatus.errorMessage}
                  timedOut={evidenceStatus.isTimedOut}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Network className="size-5 text-teal" />{t('course.generateOutline')}</CardTitle>
                <CardDescription>{t('course.generateOutlineDescription')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <CourseModelPicker
                  options={models.data?.options ?? []}
                  value={outlineModel}
                  onChange={setOutlineModel}
                  disabled={models.isLoading || generateOutline.isPending || outlineStatus.isFetching}
                />
                <div className="space-y-2">
                  <p className="text-sm font-medium">{t('course.evidenceAnchors')}</p>
                  {anchors.isLoading ? <CoursePageLoading /> : anchors.data?.length ? (
                    <div className="max-h-56 space-y-2 overflow-y-auto rounded-md border p-3">
                      {anchors.data.map((anchor) => (
                        <label key={anchor.anchor_id} className="flex cursor-pointer items-start gap-3 text-sm">
                          <Checkbox
                            checked={selectedAnchorIds.includes(anchor.anchor_id)}
                            onCheckedChange={(checked) => setSelectedAnchorIds((previous) =>
                              checked
                                ? [...new Set([...previous, anchor.anchor_id])]
                                : previous.filter((item) => item !== anchor.anchor_id)
                            )}
                          />
                          <span>
                            <span className="font-medium">{anchor.source_role} · {anchor.locator.kind} {anchor.locator.index}</span>
                            <span className="mt-1 block text-muted-foreground">{anchor.locator.quote}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                  ) : <p className="text-sm text-muted-foreground">{t('course.noAnchors')}</p>}
                </div>
                <Button
                  type="button"
                  onClick={() => void handleOutlineGenerate()}
                  disabled={!outlineModel || selectedAnchorIds.length === 0 || generateOutline.isPending || outlineStatus.isFetching}
                >
                  {generateOutline.isPending ? t('common.processing') : t('course.generateOutline')}
                </Button>
                <CommandJobPanel
                  status={outlineCommandId ? outlineStatus.status : undefined}
                  errorMessage={outlineStatus.errorMessage}
                  timedOut={outlineStatus.isTimedOut}
                />
              </CardContent>
            </Card>
          </div>

          {outlineArtifact && currentOutline && (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <CardTitle>{outlineArtifact.title}</CardTitle>
                    <CardDescription>{t('course.outlineVersion', { version: currentOutline.version_no })}</CardDescription>
                  </div>
                  {approved && <Badge className="bg-fern"><CheckCircle2 className="mr-1 size-3" />{t('course.approved')}</Badge>}
                </div>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="space-y-3">
                  {outlineArtifact.chapters.map((chapter, index) => (
                    <div key={chapter.key} className="rounded-md border p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h3 className="font-display font-bold">{index + 1}. {chapter.title}</h3>
                          <p className="mt-1 text-sm text-muted-foreground">{chapter.purpose}</p>
                        </div>
                        {approved && (
                          <Button asChild size="sm" variant="outline">
                            <Link href={`/courses/${encodeURIComponent(courseId)}/chapters/${encodeURIComponent(chapter.key)}`}>
                              <BookOpen className="mr-2 size-4" />{t('course.openChapter')}
                            </Link>
                          </Button>
                        )}
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {chapter.anchor_ids.map((anchorId) => {
                          const anchor = anchorsById.get(anchorId)
                          return (
                            <Badge key={anchorId} variant="outline" title={anchor?.locator.quote}>
                              {anchor?.source_role ?? t('course.citation')} · {anchor?.locator.index ?? anchorId}
                            </Badge>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>

                {outlineArtifact.dependency_edges.length > 0 && (
                  <div>
                    <h3 className="mb-3 font-display font-bold">{t('course.dependencyGraph')}</h3>
                    <div className="flex flex-wrap gap-2">
                      {outlineArtifact.dependency_edges.map((edge, index) => (
                        <Badge key={`${edge.from_key}-${edge.to_key}-${index}`} variant="secondary">
                          {edge.from_key} → {edge.to_key}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

                {!approved && (
                  <OutlineApproval
                    disabled={course.data.status !== 'outline_ready' || approveOutline.isPending}
                    onApprove={(confirmation) => approveOutline.mutate({
                      version_id: currentOutline.id,
                      confirmation,
                    })}
                  />
                )}
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </AppShell>
  )
}

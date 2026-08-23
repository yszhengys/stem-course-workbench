'use client'

import { useEffect, useRef } from 'react'
import { BookMarked, FlaskConical } from 'lucide-react'

import { LabRenderer } from '@/components/course/LabRenderer'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { CourseLab, CourseLearnerChapterArtifact } from '@/lib/types/course'

export function ChapterReader({
  artifact,
  labs,
  onPosition,
}: {
  artifact: CourseLearnerChapterArtifact
  labs: CourseLab[]
  onPosition?: (blockKey: string) => void
}) {
  const { t } = useTranslation()
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!onPosition || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting || entry.intersectionRatio < 0.6) continue
        const blockKey = (entry.target as HTMLElement).dataset.learningBlock
        if (blockKey) onPosition(blockKey)
      }
    }, { threshold: 0.6 })
    const sections = contentRef.current?.querySelectorAll<HTMLElement>('[data-learning-block]')
    sections?.forEach((section) => observer.observe(section))
    return () => observer.disconnect()
  }, [artifact.sections, onPosition])

  return (
    <div ref={contentRef} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t('course.chapterPurpose')}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p>{artifact.purpose}</p>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="font-semibold">{t('course.prerequisites')}</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                {artifact.prerequisites.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
            <div>
              <h3 className="font-semibold">{t('course.learningObjectives')}</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                {artifact.objectives.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>

      {artifact.sections.map((section) => (
        <section
          id={`learn-block-${section.block_key}`}
          key={section.block_key}
          data-learning-block={section.block_key}
          tabIndex={0}
          onFocus={() => onPosition?.(section.block_key)}
          className="scroll-mt-6 rounded-lg border bg-card p-6 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-labelledby={`learn-block-title-${section.block_key}`}
        >
          <h2 id={`learn-block-title-${section.block_key}`} className="font-display text-xl font-bold">
            {section.title}
          </h2>
          <div className="mt-4">
            <MarkdownRenderer>{section.markdown}</MarkdownRenderer>
          </div>
        </section>
      ))}

      {artifact.formulas.length > 0 && (
        <Card>
          <CardHeader><CardTitle>{t('course.formulas')}</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {artifact.formulas.map((formula) => (
              <div key={formula.key} className="rounded-md border p-4">
                <MarkdownRenderer>{`$$${formula.latex}$$`}</MarkdownRenderer>
                <p className="text-sm text-muted-foreground">{formula.meaning}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {artifact.worked_examples.length > 0 && (
        <Card>
          <CardHeader><CardTitle>{t('course.workedExamples')}</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {artifact.worked_examples.map((example) => (
              <article key={example.key} className="space-y-3 rounded-md border p-4">
                <p className="font-medium">{example.prompt}</p>
                <ol className="list-decimal space-y-1 pl-5 text-sm">
                  {example.steps.map((step) => <li key={step}>{step}</li>)}
                </ol>
                <p><strong>{t('course.answer')}:</strong> {example.answer}</p>
              </article>
            ))}
          </CardContent>
        </Card>
      )}

      {labs.length > 0 && (
        <section aria-labelledby="learn-labs-title" className="space-y-4">
          <div className="flex items-center gap-2">
            <FlaskConical className="size-5" aria-hidden="true" />
            <h2 id="learn-labs-title" className="font-display text-xl font-bold">
              {t('course.interactiveLabs')}
            </h2>
          </div>
          {labs.map((lab) => <LabRenderer key={lab.lab_key} spec={lab.spec} />)}
        </section>
      )}

      {(artifact.misconceptions.length > 0 || artifact.quick_reference.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BookMarked className="size-5" aria-hidden="true" />
              {t('course.quickReference')}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5 md:grid-cols-2">
            <div>
              <h3 className="font-semibold">{t('course.misconceptions')}</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                {artifact.misconceptions.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
            <div>
              <h3 className="font-semibold">{t('course.quickReference')}</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                {artifact.quick_reference.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

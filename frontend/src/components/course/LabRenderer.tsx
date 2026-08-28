'use client'

import { useId, useMemo, useState } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Label } from '@/components/ui/label'
import { useTranslation } from '@/lib/hooks/use-translation'
import { sampleLab, validateLabSpec } from '@/lib/course/safe-lab'

function evenlySample<T>(items: T[], limit = 20): T[] {
  if (items.length <= limit) return items
  return Array.from({ length: limit }, (_, index) => items[
    Math.round((index * (items.length - 1)) / (limit - 1))
  ])
}

export function LabRenderer({ spec: rawSpec }: { spec: unknown }) {
  const { t } = useTranslation()
  const instanceId = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const [controlValues, setControlValues] = useState<Record<string, number>>({})
  const result = useMemo(() => {
    try {
      const spec = validateLabSpec(rawSpec)
      return { spec, sampled: sampleLab(spec, controlValues), error: null }
    } catch {
      return { spec: null, sampled: null, error: true }
    }
  }, [controlValues, rawSpec])

  if (result.error || !result.spec || !result.sampled) {
    return (
      <Alert variant="destructive">
        <AlertTitle>{t('course.invalidLab')}</AlertTitle>
        <AlertDescription>{t('course.invalidLabDescription')}</AlertDescription>
      </Alert>
    )
  }

  const allPoints = [...result.sampled.points, ...result.sampled.paths.flat()]
  const xValues = allPoints.map((point) => point.x)
  const yValues = allPoints.map((point) => point.y)
  const xMin = Math.min(...xValues, -1)
  const xMax = Math.max(...xValues, 1)
  const yMin = Math.min(...yValues, -1)
  const yMax = Math.max(...yValues, 1)
  const toX = (value: number) => 20 + ((value - xMin) / Math.max(1e-9, xMax - xMin)) * 560
  const toY = (value: number) => 300 - ((value - yMin) / Math.max(1e-9, yMax - yMin)) * 280
  const pointRows = [
    ...result.sampled.paths.flatMap((path, pathIndex) => path.map((point, pointIndex) => ({
      series: pathIndex + 1,
      point: pointIndex + 1,
      x: point.x,
      y: point.y,
    }))),
    ...result.sampled.points.map((point, pointIndex) => ({
      series: null,
      point: pointIndex + 1,
      x: point.x,
      y: point.y,
    })),
  ]
  const alternativePoints = evenlySample(pointRows)
  const alternativeVectors = evenlySample(result.sampled.vectors)
  const vectorField = result.spec.kind === 'vector_field'
  const displayedSamples = vectorField ? alternativeVectors.length : alternativePoints.length
  const alternativeId = `lab-alternative-${instanceId}-${result.spec.key}`

  return (
    <div className="space-y-4 rounded-md border p-4">
      <div>
        <h4 className="font-display font-bold">{result.spec.title}</h4>
        <p className="text-xs text-muted-foreground">{t('course.safeLabNotice')}</p>
      </div>

      {result.spec.pedagogy && (
        <div className="grid gap-4 rounded-md border bg-muted/20 p-4 text-sm">
          <section className="space-y-1">
            <h5 className="font-semibold">{t('course.labLearningObjectives')}</h5>
            <ul className="list-disc space-y-1 pl-5">
              {result.spec.pedagogy.learning_objectives.map((objective, index) => (
                <li key={`${index}:${objective}`}>{objective}</li>
              ))}
            </ul>
          </section>
          {result.spec.pedagogy.prerequisite_concepts.length > 0 && (
            <section className="space-y-1">
              <h5 className="font-semibold">{t('course.labPrerequisiteConcepts')}</h5>
              <ul className="list-disc space-y-1 pl-5">
                {result.spec.pedagogy.prerequisite_concepts.map((concept, index) => (
                  <li key={`${index}:${concept}`}>{concept}</li>
                ))}
              </ul>
            </section>
          )}
          {result.spec.pedagogy.variables.length > 0 && (
            <section className="space-y-1">
              <h5 className="font-semibold">{t('course.labVariables')}</h5>
              <ul className="list-disc space-y-1 pl-5">
                {result.spec.pedagogy.variables.map((variable) => (
                  <li key={variable.key}>
                    {variable.label} ({variable.key}): [{variable.range[0]}, {variable.range[1]}]
                    {variable.unit ? ` ${variable.unit}` : ''}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section className="space-y-1">
            <h5 className="font-semibold">{t('course.labPrediction')}</h5>
            <p>{result.spec.pedagogy.prediction_prompt}</p>
          </section>
          <section className="space-y-1">
            <h5 className="font-semibold">{t('course.labProcedure')}</h5>
            <ol className="list-decimal space-y-1 pl-5">
              {result.spec.pedagogy.steps.map((step, index) => (
                <li key={`${index}:${step}`}>{step}</li>
              ))}
            </ol>
          </section>
          <section className="space-y-1">
            <h5 className="font-semibold">{t('course.labExpectedObservations')}</h5>
            <ul className="list-disc space-y-1 pl-5">
              {result.spec.pedagogy.expected_observations.map((observation, index) => (
                <li key={`${index}:${observation}`}>{observation}</li>
              ))}
            </ul>
          </section>
          <section className="space-y-1">
            <h5 className="font-semibold">{t('course.labStudentSubmission')}</h5>
            <p>{result.spec.pedagogy.student_submission}</p>
          </section>
          <section className="space-y-1">
            <h5 className="font-semibold">{t('course.labRubric')}</h5>
            <ul className="list-disc space-y-1 pl-5">
              {result.spec.pedagogy.rubric.map((criterion, index) => (
                <li key={`${index}:${criterion}`}>{criterion}</li>
              ))}
            </ul>
          </section>
          <section className="space-y-1">
            <h5 className="font-semibold">{t('course.labErrorBoundaries')}</h5>
            <ul className="list-disc space-y-1 pl-5">
              {result.spec.pedagogy.error_boundaries.map((boundary, index) => (
                <li key={`${index}:${boundary}`}>{boundary}</li>
              ))}
            </ul>
          </section>
          <section className="space-y-1">
            <h5 className="font-semibold">{t('course.labAccessibleAlternative')}</h5>
            <p>{result.spec.pedagogy.accessible_alternative}</p>
          </section>
        </div>
      )}

      {result.spec.controls.map((control) => {
        const value = controlValues[control.key] ?? control.value
        const controlId = `lab-control-${instanceId}-${result.spec.key}-${control.key}`
          .replace(/[^a-zA-Z0-9_-]/g, '-')
        return (
          <div key={control.key} className="space-y-2">
            <div className="flex justify-between gap-3 text-sm">
              <Label htmlFor={controlId}>{control.label ?? control.key}</Label>
              <span className="font-mono">{value}</span>
            </div>
            <input
              id={controlId}
              type="range"
              min={control.min}
              max={control.max}
              step={control.step ?? (control.max - control.min) / 100}
              value={value}
              onChange={(event) => setControlValues((current) => ({
                ...current,
                [control.key]: Number(event.target.value),
              }))}
              className="w-full accent-[var(--fern)]"
            />
          </div>
        )
      })}

      <svg
        data-testid="safe-lab-canvas"
        viewBox="0 0 600 320"
        role="img"
        aria-label={result.spec.title}
        aria-describedby={alternativeId}
        className="w-full rounded-md bg-card"
      >
        <line x1="20" y1={toY(0)} x2="580" y2={toY(0)} stroke="currentColor" opacity="0.2" />
        <line x1={toX(0)} y1="20" x2={toX(0)} y2="300" stroke="currentColor" opacity="0.2" />
        {result.sampled.paths.map((path, index) => (
          <polyline
            key={index}
            points={path.map((point) => `${toX(point.x)},${toY(point.y)}`).join(' ')}
            fill="none"
            stroke={index % 2 === 0 ? 'var(--teal)' : 'var(--gold)'}
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}
        {result.sampled.points.map((point, index) => (
          <circle key={index} cx={toX(point.x)} cy={toY(point.y)} r="4" fill="var(--fern)" />
        ))}
      </svg>

      <div id={alternativeId} className="space-y-3 rounded-md border bg-muted/20 p-3">
        <p className="text-sm font-medium">{t('course.labTextAlternative')}</p>
        <p className="text-xs text-muted-foreground">
          {t('course.labSampleSummary', {
            paths: result.sampled.paths.length,
            displayed: displayedSamples,
            total: result.sampled.totalSamples,
          })}
        </p>
        <div
          className="max-h-64 overflow-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          role="region"
          aria-label={t('course.labScrollableData')}
          tabIndex={0}
        >
          <table
            aria-label={t('course.labDataAlternative')}
            className="w-full border-collapse text-left text-xs"
          >
            <caption className="sr-only">{t('course.labDataAlternative')}</caption>
            <thead>
              {vectorField ? (
                <tr className="border-b">
                  <th scope="col" className="p-2">{t('course.labVectorIndex')}</th>
                  <th scope="col" className="p-2">{t('course.labCoordinateX')}</th>
                  <th scope="col" className="p-2">{t('course.labCoordinateY')}</th>
                  <th scope="col" className="p-2">{t('course.labVectorU')}</th>
                  <th scope="col" className="p-2">{t('course.labVectorV')}</th>
                </tr>
              ) : (
                <tr className="border-b">
                  <th scope="col" className="p-2">{t('course.labSeries')}</th>
                  <th scope="col" className="p-2">{t('course.labPointIndex')}</th>
                  <th scope="col" className="p-2">{t('course.labCoordinateX')}</th>
                  <th scope="col" className="p-2">{t('course.labCoordinateY')}</th>
                </tr>
              )}
            </thead>
            <tbody>
              {vectorField ? alternativeVectors.map((vector, index) => (
                <tr key={`${index}:${vector.x}:${vector.y}`} className="border-b last:border-0">
                  <th scope="row" className="p-2 font-medium">{index + 1}</th>
                  <td className="p-2 font-mono">{vector.x.toPrecision(6)}</td>
                  <td className="p-2 font-mono">{vector.y.toPrecision(6)}</td>
                  <td className="p-2 font-mono">{vector.u.toPrecision(6)}</td>
                  <td className="p-2 font-mono">{vector.v.toPrecision(6)}</td>
                </tr>
              )) : alternativePoints.map((point) => (
                <tr
                  key={`${point.series ?? 'point'}:${point.point}:${point.x}:${point.y}`}
                  className="border-b last:border-0"
                >
                  <th
                    scope="row"
                    className="p-2 font-medium"
                    data-testid={point.series === null
                      ? `lab-standalone-${point.point}`
                      : `lab-series-${point.series}-${point.point}`}
                  >
                    {point.series ?? t('course.labStandalonePoint')}
                  </th>
                  <td className="p-2 font-mono">{point.point}</td>
                  <td className="p-2 font-mono">{point.x.toPrecision(6)}</td>
                  <td className="p-2 font-mono">{point.y.toPrecision(6)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

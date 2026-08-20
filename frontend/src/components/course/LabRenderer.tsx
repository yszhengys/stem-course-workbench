'use client'

import { useMemo, useState } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Label } from '@/components/ui/label'
import { useTranslation } from '@/lib/hooks/use-translation'
import { sampleLab, validateLabSpec } from '@/lib/course/safe-lab'

export function LabRenderer({ spec: rawSpec }: { spec: unknown }) {
  const { t } = useTranslation()
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

  return (
    <div className="space-y-4 rounded-md border p-4">
      <div>
        <h4 className="font-display font-bold">{result.spec.title}</h4>
        <p className="text-xs text-muted-foreground">{t('course.safeLabNotice')}</p>
      </div>

      {result.spec.controls.map((control) => {
        const value = controlValues[control.key] ?? control.value
        return (
          <div key={control.key} className="space-y-2">
            <div className="flex justify-between gap-3 text-sm">
              <Label htmlFor={`lab-control-${control.key}`}>{control.label ?? control.key}</Label>
              <span className="font-mono">{value}</span>
            </div>
            <input
              id={`lab-control-${control.key}`}
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
    </div>
  )
}

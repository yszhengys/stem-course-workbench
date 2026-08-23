'use client'

import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { CourseAnswerFormat } from '@/lib/types/course'

type UnitAnswer = { value: string; unit: string }
type VectorAnswer = { components: string[]; unit?: string }
type SetAnswer = { items: string[] }
type MultipartAnswer = { parts: unknown[] }

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

export function emptyLearnerAnswer(format: CourseAnswerFormat): unknown {
  switch (format.kind) {
    case 'unit':
      return { value: '', unit: '' } satisfies UnitAnswer
    case 'vector':
      return {
        components: Array.from({ length: format.component_count ?? 0 }, () => ''),
        ...(format.unit_required ? { unit: '' } : {}),
      } satisfies VectorAnswer
    case 'set':
      return { items: [] } satisfies SetAnswer
    case 'multipart':
      return { parts: format.parts.map(emptyLearnerAnswer) } satisfies MultipartAnswer
    default:
      return ''
  }
}

export function isLearnerAnswerComplete(
  format: CourseAnswerFormat,
  value: unknown,
): boolean {
  const record = recordValue(value)
  switch (format.kind) {
    case 'unit':
      return typeof record.value === 'string'
        && record.value.trim().length > 0
        && typeof record.unit === 'string'
        && record.unit.trim().length > 0
    case 'vector': {
      const components = Array.isArray(record.components) ? record.components : []
      return components.length === format.component_count
        && components.every((part) => typeof part === 'string' && part.trim().length > 0)
        && (!format.unit_required
          || (typeof record.unit === 'string' && record.unit.trim().length > 0))
    }
    case 'set':
      return Array.isArray(record.items) && record.items.length > 0
    case 'multipart': {
      const parts = Array.isArray(record.parts) ? record.parts : []
      return parts.length === format.parts.length
        && format.parts.every((partFormat, index) => (
          isLearnerAnswerComplete(partFormat, parts[index])
        ))
    }
    default:
      return typeof value === 'string' && value.trim().length > 0
  }
}

export function AnswerEditor({
  format,
  value,
  onChange,
  disabled = false,
}: {
  format: CourseAnswerFormat
  value: unknown
  onChange: (value: unknown) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const record = recordValue(value)

  if (format.kind === 'unit') {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        <Input
          aria-label={t('course.answerValue')}
          value={typeof record.value === 'string' ? record.value : ''}
          onChange={(event) => onChange({
            value: event.target.value,
            unit: typeof record.unit === 'string' ? record.unit : '',
          })}
          disabled={disabled}
          autoComplete="off"
        />
        <Input
          aria-label={t('course.answerUnit')}
          value={typeof record.unit === 'string' ? record.unit : ''}
          onChange={(event) => onChange({
            value: typeof record.value === 'string' ? record.value : '',
            unit: event.target.value,
          })}
          disabled={disabled}
          autoComplete="off"
        />
      </div>
    )
  }

  if (format.kind === 'vector') {
    const components = Array.isArray(record.components)
      ? record.components.map((part) => typeof part === 'string' ? part : '')
      : Array.from({ length: format.component_count ?? 0 }, () => '')
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        {components.map((component, index) => (
          <Input
            key={index}
            aria-label={t('course.vectorComponent', { index: index + 1 })}
            value={component}
            onChange={(event) => {
              const next = [...components]
              next[index] = event.target.value
              onChange({
                components: next,
                ...(format.unit_required
                  ? { unit: typeof record.unit === 'string' ? record.unit : '' }
                  : {}),
              })
            }}
            disabled={disabled}
            autoComplete="off"
          />
        ))}
        {format.unit_required && (
          <Input
            aria-label={t('course.answerUnit')}
            value={typeof record.unit === 'string' ? record.unit : ''}
            onChange={(event) => onChange({ components, unit: event.target.value })}
            disabled={disabled}
            autoComplete="off"
          />
        )}
      </div>
    )
  }

  if (format.kind === 'set') {
    const items = Array.isArray(record.items)
      ? record.items.filter((item): item is string => typeof item === 'string')
      : []
    return (
      <Textarea
        aria-label={t('course.setItems')}
        value={items.join(', ')}
        onChange={(event) => onChange({
          items: event.target.value
            .split(/[\n,]+/)
            .map((item) => item.trim())
            .filter(Boolean),
        })}
        disabled={disabled}
      />
    )
  }

  if (format.kind === 'multipart') {
    const parts = Array.isArray(record.parts)
      ? record.parts
      : format.parts.map(emptyLearnerAnswer)
    return (
      <div className="space-y-4">
        {format.parts.map((partFormat, index) => (
          <fieldset key={index} className="space-y-2 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">
              {t('course.answerPart', { index: index + 1 })}
            </legend>
            <AnswerEditor
              format={partFormat}
              value={parts[index]}
              onChange={(part) => {
                const next = [...parts]
                next[index] = part
                onChange({ parts: next })
              }}
              disabled={disabled}
            />
          </fieldset>
        ))}
      </div>
    )
  }

  const inputValue = typeof value === 'string' ? value : ''
  if (format.kind === 'proof' || format.kind === 'explanation') {
    return (
      <Textarea
        aria-label={t('course.exerciseAnswer')}
        value={inputValue}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      />
    )
  }
  return (
    <Input
      aria-label={t('course.exerciseAnswer')}
      value={inputValue}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      autoComplete="off"
    />
  )
}

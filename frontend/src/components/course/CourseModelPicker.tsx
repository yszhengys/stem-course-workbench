'use client'

import { Label } from '@/components/ui/label'
import { reasoningEffortLabel } from '@/lib/course/course-labels'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  CourseModelOption,
  isSelectableModel,
  ModelSelection,
  reasoningEffortSchema,
} from '@/lib/types/course'

interface CourseModelPickerProps {
  options: CourseModelOption[]
  value: ModelSelection | null
  onChange: (selection: ModelSelection | null) => void
  disabled?: boolean
  idPrefix?: string
  accessibleLabel?: string
}

function optionKey(option: CourseModelOption) {
  return option.model ? `${option.adapter}|${option.model}` : `unavailable|${option.display_name ?? 'model'}`
}

function optionName(option: CourseModelOption) {
  return option.name ?? option.display_name ?? option.model ?? '—'
}

export function CourseModelPicker({
  options,
  value,
  onChange,
  disabled = false,
  idPrefix = 'course',
  accessibleLabel,
}: CourseModelPickerProps) {
  const { t } = useTranslation()
  const modelId = `${idPrefix}-model`
  const reasoningId = `${idPrefix}-reasoning`
  const modelLabel = accessibleLabel
    ? `${accessibleLabel} — ${t('course.modelLabel')}`
    : t('course.modelLabel')
  const reasoningLabel = accessibleLabel
    ? `${accessibleLabel} — ${t('course.reasoningEffort')}`
    : t('course.reasoningEffort')
  const current = value ? `${value.adapter}|${value.model}` : ''
  const hasSelectableModel = options.some(isSelectableModel)

  const handleModelChange = (key: string) => {
    const option = options.find((candidate) => optionKey(candidate) === key)
    if (!option || !isSelectableModel(option)) {
      onChange(null)
      return
    }
    onChange({
      adapter: option.adapter,
      model: option.model,
      reasoning_effort: option.adapter === 'codex_cli'
        ? (option.reasoning_effort ?? 'max')
        : null,
    })
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-2">
        <Label htmlFor={modelId}>{modelLabel}</Label>
        <select
          id={modelId}
          aria-label={modelLabel}
          value={current}
          onChange={(event) => handleModelChange(event.target.value)}
          disabled={disabled}
          className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
        >
          <option value="">{t('course.selectModel')}</option>
          {options.map((option) => (
            <option
              key={optionKey(option)}
              value={optionKey(option)}
              disabled={!isSelectableModel(option)}
            >
              {optionName(option)}{!isSelectableModel(option) ? ` — ${t('course.notConfigured')}` : ''}
            </option>
          ))}
        </select>
        {!hasSelectableModel && (
          <p className="text-xs text-muted-foreground">{t('course.noSelectableModels')}</p>
        )}
      </div>

      {value?.adapter === 'codex_cli' && (
        <div className="space-y-2">
          <Label htmlFor={reasoningId}>{reasoningLabel}</Label>
          <select
            id={reasoningId}
            value={value.reasoning_effort ?? 'max'}
            onChange={(event) => onChange({
              ...value,
              reasoning_effort: reasoningEffortSchema.parse(event.target.value),
            })}
            disabled={disabled}
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
          >
            {['low', 'medium', 'high', 'xhigh', 'max'].map((effort) => (
              <option key={effort} value={effort}>{reasoningEffortLabel(t, effort)}</option>
            ))}
          </select>
        </div>
      )}
    </div>
  )
}

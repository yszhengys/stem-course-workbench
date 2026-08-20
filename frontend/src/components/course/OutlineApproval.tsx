'use client'

import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useTranslation } from '@/lib/hooks/use-translation'

export const OUTLINE_CONFIRMATION = '确认大纲'

export function OutlineApproval({
  disabled,
  onApprove,
}: {
  disabled: boolean
  onApprove: (confirmation: string) => void
}) {
  const { t } = useTranslation()
  const [confirmation, setConfirmation] = useState('')
  const exact = confirmation === OUTLINE_CONFIRMATION

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label htmlFor="outline-confirmation">{t('course.approvalLabel')}</Label>
        <Input
          id="outline-confirmation"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          placeholder={OUTLINE_CONFIRMATION}
          autoComplete="off"
        />
        <p className="text-xs text-muted-foreground">
          {t('course.approvalHint', { confirmation: OUTLINE_CONFIRMATION })}
        </p>
      </div>
      <Button
        type="button"
        onClick={() => onApprove(confirmation)}
        disabled={disabled || !exact}
      >
        {t('course.approveOutline')}
      </Button>
    </div>
  )
}

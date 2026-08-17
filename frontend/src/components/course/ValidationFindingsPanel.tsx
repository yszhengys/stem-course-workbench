'use client'

import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { CourseFinding } from '@/lib/types/course'

export function ValidationFindingsPanel({
  findings,
  disabled,
  onUpdate,
}: {
  findings: CourseFinding[]
  disabled: boolean
  onUpdate: (findingId: string, status: 'resolved' | 'acknowledged', reason: string) => void
}) {
  const { t } = useTranslation()
  const [reasons, setReasons] = useState<Record<string, string>>({})

  if (findings.length === 0) {
    return <p className="text-sm text-muted-foreground">{t('course.noFindings')}</p>
  }

  return (
    <div className="space-y-3">
      {findings.map((record) => {
        const finding = record.finding
        const reason = reasons[record.id] ?? finding.resolution_reason ?? ''
        const terminal = finding.status === 'resolved' || finding.status === 'acknowledged'
        return (
          <div key={record.id} className="space-y-3 rounded-md border p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={finding.severity === 'error' || finding.severity === 'high' ? 'destructive' : 'secondary'}>
                {finding.severity}
              </Badge>
              <Badge variant="outline">{finding.kind}</Badge>
              <Badge variant="outline">{finding.status}</Badge>
              <span className="font-mono text-xs text-muted-foreground">{finding.item_key}</span>
            </div>
            <p className="text-sm">{finding.message}</p>
            {terminal ? (
              <p className="text-xs text-muted-foreground">{finding.resolution_reason}</p>
            ) : (
              <>
                <Textarea
                  aria-label={t('course.resolutionReason')}
                  value={reason}
                  onChange={(event) => setReasons((current) => ({
                    ...current,
                    [record.id]: event.target.value,
                  }))}
                  placeholder={t('course.resolutionReason')}
                  rows={2}
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    onClick={() => onUpdate(record.id, 'resolved', reason.trim())}
                    disabled={disabled || !reason.trim()}
                  >
                    {t('course.markResolved')}
                  </Button>
                  {(finding.severity === 'warning' || finding.severity === 'high') && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onUpdate(record.id, 'acknowledged', reason.trim())}
                      disabled={disabled || !reason.trim()}
                    >
                      {t('course.acknowledge')}
                    </Button>
                  )}
                </div>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

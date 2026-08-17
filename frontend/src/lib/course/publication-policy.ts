export function isFindingBlockingPublication(finding: {
  severity: string
  status: string
}): boolean {
  if (finding.status === 'manual_check' || finding.status === 'uncertain') return true
  if (finding.severity === 'error') return finding.status !== 'resolved'
  if (finding.severity === 'high' || finding.severity === 'warning') {
    return finding.status !== 'resolved' && finding.status !== 'acknowledged'
  }
  return false
}

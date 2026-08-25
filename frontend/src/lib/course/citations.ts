export function courseCitationTarget(anchorId: string): string {
  return `course-source-${encodeURIComponent(anchorId).replaceAll('%', '-')}`
}

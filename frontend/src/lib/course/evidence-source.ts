import type {
  BuildEvidenceRequest,
  EligibleCourseSource,
  SourceRole,
} from '@/lib/types/course'

interface EvidenceJob {
  command_id: string
}

export class IneligibleEvidenceSourceError extends Error {
  constructor() {
    super('The Source ID is not in this course Notebook eligible-source list')
    this.name = 'IneligibleEvidenceSourceError'
  }
}

export async function submitEvidenceSource({
  sourceId,
  role,
  sources,
  associate,
  build,
}: {
  sourceId: string
  role: SourceRole
  sources: EligibleCourseSource[]
  associate: (request: { source_id: string; role: SourceRole }) => Promise<unknown>
  build: (request: BuildEvidenceRequest) => Promise<EvidenceJob>
}): Promise<EvidenceJob> {
  const normalizedSourceId = sourceId.trim()
  if (!normalizedSourceId) throw new Error('A Source ID is required')

  const listed = sources.find((source) => source.source_id === normalizedSourceId)
  if (!listed) throw new IneligibleEvidenceSourceError()

  if (!listed.associated || listed.role !== role) {
    await associate({ source_id: normalizedSourceId, role })
  }

  return build({ source_id: normalizedSourceId, role, force: false })
}

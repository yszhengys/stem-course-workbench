import { z } from 'zod'

import { apiClient } from '@/lib/api/client'

export const commandJobStateSchema = z.enum([
  'new',
  'running',
  'completed',
  'failed',
  'canceled',
  'queued',
  'succeeded',
  'cancelled',
])
export type CommandJobState = z.infer<typeof commandJobStateSchema>

export const commandJobStatusSchema = z.object({
  job_id: z.string().min(1),
  status: commandJobStateSchema,
  result: z.record(z.string(), z.unknown()).nullable().optional(),
  error_message: z.string().nullable().optional(),
  created: z.string().nullable().optional(),
  updated: z.string().nullable().optional(),
  progress: z.record(z.string(), z.unknown()).nullable().optional(),
}).strict()

export type CommandJobStatus = z.infer<typeof commandJobStatusSchema>

export const commandsApi = {
  async getStatus(jobId: string): Promise<CommandJobStatus> {
    const response = await apiClient.get(`/commands/jobs/${encodeURIComponent(jobId)}`)
    return commandJobStatusSchema.parse(response.data)
  },
}

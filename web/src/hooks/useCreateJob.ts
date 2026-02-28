import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { CreateJobRequest } from '@/api/types'

export function useCreateJob() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: CreateJobRequest) => apiClient.createJob(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

import { useInfiniteQuery } from '@tanstack/react-query'
import { apiClient, type JobListParams } from '@/api/client'
import { POLL_INTERVAL_STANDARD_MS } from '@/lib/queryTimings'
import type { ConsoleJobListResponse } from '@/api/types'

type JobsFilters = Omit<JobListParams, 'cursor'>

export function useJobs(params: JobsFilters = {}) {
  return useInfiniteQuery<ConsoleJobListResponse>({
    queryKey: ['jobs', params],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      apiClient.getJobs({
        ...params,
        cursor: typeof pageParam === 'string' ? pageParam : undefined,
      }),
    getNextPageParam: (lastPage) => (lastPage.has_more ? (lastPage.cursor ?? undefined) : undefined),
    refetchInterval: POLL_INTERVAL_STANDARD_MS,
  })
}

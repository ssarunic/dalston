import { useInfiniteQuery } from '@tanstack/react-query'
import { apiClient, type JobListParams } from '@/api/client'

type JobsFilters = Omit<JobListParams, 'cursor'>

export function useJobs(params: JobsFilters = {}) {
  return useInfiniteQuery({
    queryKey: ['jobs', params],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      apiClient.getJobs({
        ...params,
        cursor: typeof pageParam === 'string' ? pageParam : undefined,
      }),
    getNextPageParam: (lastPage) => (lastPage.has_more ? (lastPage.cursor ?? undefined) : undefined),
    refetchInterval: 5000,
  })
}

import { useSearchParams } from 'react-router-dom'

type SearchParamValue = string | number | null | undefined

export interface SharedTableStateConfig {
  defaultStatus?: string
  statusOptions?: readonly string[]
  defaultSort: string
  sortOptions: readonly string[]
  defaultLimit: number
  limitOptions: readonly number[]
}

export interface SharedTableState {
  searchParams: URLSearchParams
  setSearchParams: ReturnType<typeof useSearchParams>[1]
  status: string
  sort: string
  limit: number
  setStatus: (value: string) => void
  setSort: (value: string) => void
  setLimit: (value: number) => void
  updateParams: (updates: Record<string, SearchParamValue>) => void
}

function readEnum(value: string | null, options: readonly string[], fallback: string): string {
  if (!value) return fallback
  return options.includes(value) ? value : fallback
}

function readLimit(
  value: string | null,
  options: readonly number[],
  fallback: number
): number {
  if (!value) return fallback
  const parsed = Number(value)
  return Number.isFinite(parsed) && options.includes(parsed) ? parsed : fallback
}

export function useSharedTableState(config: SharedTableStateConfig): SharedTableState {
  const [searchParams, setSearchParams] = useSearchParams()

  const statusOptions = config.statusOptions ?? [config.defaultStatus ?? 'all']
  const defaultStatus = config.defaultStatus ?? statusOptions[0] ?? 'all'

  const status = readEnum(searchParams.get('status'), statusOptions, defaultStatus)
  const sort = readEnum(searchParams.get('sort'), config.sortOptions, config.defaultSort)
  const limit = readLimit(searchParams.get('limit'), config.limitOptions, config.defaultLimit)

  const updateParams = (updates: Record<string, SearchParamValue>) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(updates)) {
          if (value === null || value === undefined || value === '') {
            next.delete(key)
          } else {
            next.set(key, String(value))
          }
        }
        return next
      },
      { replace: true }
    )
  }

  const setStatus = (value: string) => {
    updateParams({ status: value === defaultStatus ? null : value })
  }

  const setSort = (value: string) => {
    updateParams({ sort: value === config.defaultSort ? null : value })
  }

  const setLimit = (value: number) => {
    updateParams({ limit: value === config.defaultLimit ? null : value })
  }

  return {
    searchParams,
    setSearchParams,
    status,
    sort,
    limit,
    setStatus,
    setSort,
    setLimit,
    updateParams,
  }
}

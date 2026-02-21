import { useSearchParams } from 'react-router-dom'

type SearchParamValue = string | number | null | undefined

export interface SharedTableStateConfig<
  TStatus extends string = string,
  TSort extends string = string,
  TLimit extends number = number,
> {
  defaultStatus: TStatus
  statusOptions: readonly TStatus[]
  defaultSort: TSort
  sortOptions: readonly TSort[]
  defaultLimit: TLimit
  limitOptions: readonly TLimit[]
}

export interface SharedTableState<
  TStatus extends string = string,
  TSort extends string = string,
  TLimit extends number = number,
> {
  searchParams: URLSearchParams
  setSearchParams: ReturnType<typeof useSearchParams>[1]
  status: TStatus
  sort: TSort
  limit: TLimit
  setStatus: (value: string) => void
  setSort: (value: string) => void
  setLimit: (value: number) => void
  updateParams: (updates: Record<string, SearchParamValue>) => void
  resetAll: (extraUpdates?: Record<string, SearchParamValue>) => void
}

function readEnum<T extends string>(
  value: string | null,
  options: readonly T[],
  fallback: T
): T {
  if (!value) return fallback
  return (options as readonly string[]).includes(value) ? (value as T) : fallback
}

function readLimit<T extends number>(
  value: string | null,
  options: readonly T[],
  fallback: T
): T {
  if (!value) return fallback
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return (options as readonly number[]).includes(parsed) ? (parsed as T) : fallback
}

export function useSharedTableState<
  TStatus extends string,
  TSort extends string,
  TLimit extends number,
>(config: SharedTableStateConfig<TStatus, TSort, TLimit>): SharedTableState<TStatus, TSort, TLimit> {
  const [searchParams, setSearchParams] = useSearchParams()

  const status = readEnum(searchParams.get('status'), config.statusOptions, config.defaultStatus)
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
    const normalized = readEnum(value, config.statusOptions, config.defaultStatus)
    updateParams({ status: normalized === config.defaultStatus ? null : normalized })
  }

  const setSort = (value: string) => {
    const normalized = readEnum(value, config.sortOptions, config.defaultSort)
    updateParams({ sort: normalized === config.defaultSort ? null : normalized })
  }

  const setLimit = (value: number) => {
    const normalized = readLimit(String(value), config.limitOptions, config.defaultLimit)
    updateParams({ limit: normalized === config.defaultLimit ? null : normalized })
  }

  const resetAll = (extraUpdates: Record<string, SearchParamValue> = {}) => {
    updateParams({
      status: null,
      sort: null,
      limit: null,
      ...extraUpdates,
    })
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
    resetAll,
  }
}

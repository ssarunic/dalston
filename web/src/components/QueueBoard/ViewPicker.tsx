import { Columns3, Rows3, Table2 } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * The three supported queue board layouts (M86).
 *
 * - `grid` — table with rows=jobs, columns=stages
 * - `stage-board` — kanban with columns=stages only
 * - `job-strips` — one horizontal strip per job
 */
export type BoardView = 'grid' | 'stage-board' | 'job-strips'

interface ViewPickerProps {
  value: BoardView
  onChange: (next: BoardView) => void
  className?: string
}

interface Option {
  value: BoardView
  label: string
  description: string
  Icon: typeof Table2
}

const OPTIONS: readonly Option[] = [
  {
    value: 'grid',
    label: 'Grid',
    description: 'Rows per job, columns per stage',
    Icon: Table2,
  },
  {
    value: 'stage-board',
    label: 'Stage Board',
    description: 'Kanban with columns per stage',
    Icon: Columns3,
  },
  {
    value: 'job-strips',
    label: 'Job Strips',
    description: 'Horizontal strip per job',
    Icon: Rows3,
  },
]

export function ViewPicker({ value, onChange, className }: ViewPickerProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Queue board layout"
      className={cn(
        'inline-flex items-center gap-1 rounded-md border border-border bg-card p-1',
        className,
      )}
    >
      {OPTIONS.map((opt) => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={opt.description}
            onClick={() => onChange(opt.value)}
            className={cn(
              'flex items-center gap-2 rounded-sm px-3 py-1.5 text-xs font-medium transition-colors',
              active
                ? 'bg-accent text-accent-foreground'
                : 'text-muted-foreground hover:bg-slate-800/70 hover:text-foreground',
            )}
          >
            <opt.Icon className="h-4 w-4" />
            <span>{opt.label}</span>
          </button>
        )
      })}
    </div>
  )
}

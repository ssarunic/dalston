import type { QueueBoardStageHealth } from '@/api/types'

/** Identify the bottleneck stage (highest queue_depth) if any stage has load. */
export function findBottleneckStage(
  health: QueueBoardStageHealth[],
): string | null {
  let max = 0
  let winner: string | null = null
  for (const h of health) {
    if (h.queue_depth > max) {
      max = h.queue_depth
      winner = h.stage
    }
  }
  return winner
}

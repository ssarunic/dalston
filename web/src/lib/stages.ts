/**
 * Shared pipeline stage constants used across multiple pages.
 *
 * All three maps share the same key set. When adding a new stage,
 * update all three here — both Engines and Infrastructure pages
 * will pick up the change automatically.
 */

/** Sort-order hint for known stages — unknown stages appear at the end. */
export const STAGE_ORDER: Record<string, number> = {
  prepare: 0,
  transcribe: 1,
  align: 2,
  diarize: 3,
  pii_detect: 4,
  audio_redact: 5,
  merge: 6,
}

export const STAGE_LABELS: Record<string, { label: string; description: string }> = {
  prepare: { label: 'Prepare', description: 'Audio preprocessing' },
  transcribe: { label: 'Transcribe', description: 'Speech-to-text' },
  align: { label: 'Align', description: 'Word-level timestamps' },
  diarize: { label: 'Diarize', description: 'Speaker identification' },
  pii_detect: { label: 'PII Detect', description: 'Sensitive data detection' },
  audio_redact: { label: 'Audio Redact', description: 'PII audio masking' },
  merge: { label: 'Merge', description: 'Final assembly' },
}

/** Tailwind badge classes per stage — used for stage pills in cards. */
export const STAGE_COLORS: Record<string, string> = {
  prepare: 'bg-slate-500/20 text-slate-300',
  transcribe: 'bg-blue-500/20 text-blue-300',
  align: 'bg-purple-500/20 text-purple-300',
  diarize: 'bg-amber-500/20 text-amber-300',
  pii_detect: 'bg-red-500/20 text-red-300',
  audio_redact: 'bg-rose-500/20 text-rose-300',
  merge: 'bg-green-500/20 text-green-300',
}

/** Return the stage pill Tailwind classes, falling back to neutral zinc. */
export function stagePillClass(stage: string): string {
  return STAGE_COLORS[stage] ?? 'bg-zinc-500/20 text-zinc-300'
}

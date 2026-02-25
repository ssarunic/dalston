interface AudioLevelMeterProps {
  level: number // 0-1 RMS value
  isSpeaking: boolean
  isActive: boolean
}

export function AudioLevelMeter({ level, isSpeaking, isActive }: AudioLevelMeterProps) {
  // Map RMS (typically 0-0.3 for speech) to visual percentage
  const visualLevel = Math.min(1, level * 4) * 100

  if (!isActive) {
    return (
      <div className="w-full max-w-xs mx-auto">
        <div className="h-2 bg-muted rounded-full overflow-hidden">
          <div className="h-full w-0 rounded-full" />
        </div>
      </div>
    )
  }

  return (
    <div className="w-full max-w-xs mx-auto">
      <div className="h-2 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-75 ${
            isSpeaking ? 'bg-green-500' : 'bg-muted-foreground/40'
          }`}
          style={{ width: `${visualLevel}%` }}
        />
      </div>
    </div>
  )
}

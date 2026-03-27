import { useState, useEffect, useCallback } from 'react'

export interface AudioDevice {
  deviceId: string
  label: string
}

/**
 * Enumerates available audio input devices.
 * Returns an empty list if mediaDevices API is unavailable (e.g. insecure context).
 */
export function useAudioDevices() {
  const [devices, setDevices] = useState<AudioDevice[]>([])
  const [error, setError] = useState<string | null>(null)

  const enumerate = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setError(
        'Media devices are not available. Ensure this page is served over HTTPS.'
      )
      return
    }

    try {
      // Request a brief permission grant so labels are populated
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach((t) => t.stop())

      const allDevices = await navigator.mediaDevices.enumerateDevices()
      const audioInputs = allDevices
        .filter((d) => d.kind === 'audioinput')
        .map((d, i) => ({
          deviceId: d.deviceId,
          label: d.label || `Microphone ${i + 1}`,
        }))
      setDevices(audioInputs)
      setError(null)
    } catch (err) {
      if (err instanceof DOMException && err.name === 'NotAllowedError') {
        setError('Microphone access denied. Please allow microphone access to see available devices.')
      } else {
        setError('Could not enumerate audio devices.')
      }
    }
  }, [])

  useEffect(() => {
    // Use flag to avoid updating state after unmount
    let cancelled = false

    async function init() {
      await enumerate()
      if (cancelled) return
    }
    init()

    // Re-enumerate when devices change (plug/unplug)
    if (navigator.mediaDevices?.addEventListener) {
      navigator.mediaDevices.addEventListener('devicechange', enumerate)
      return () => {
        cancelled = true
        navigator.mediaDevices.removeEventListener('devicechange', enumerate)
      }
    }
    return () => {
      cancelled = true
    }
  }, [enumerate])

  return { devices, error, refresh: enumerate }
}

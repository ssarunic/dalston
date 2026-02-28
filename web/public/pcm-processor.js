/**
 * AudioWorklet processor that converts float32 audio samples to PCM s16le
 * and computes RMS audio level for visualization.
 *
 * Runs in the audio rendering thread — keep this code minimal and allocation-free
 * in the hot path.
 */
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    // Buffer to accumulate samples before sending (~100ms chunks at 16kHz = 1600 samples)
    this._buffer = new Float32Array(1600)
    this._bufferOffset = 0
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0) return true

    const channelData = input[0] // mono
    if (!channelData || channelData.length === 0) return true

    // Accumulate samples into the buffer
    for (let i = 0; i < channelData.length; i++) {
      this._buffer[this._bufferOffset++] = channelData[i]

      if (this._bufferOffset >= this._buffer.length) {
        this._flush()
      }
    }

    return true
  }

  _flush() {
    const samples = this._buffer.subarray(0, this._bufferOffset)

    // Compute RMS audio level
    let sumSquares = 0
    for (let i = 0; i < samples.length; i++) {
      sumSquares += samples[i] * samples[i]
    }
    const rms = Math.sqrt(sumSquares / samples.length)

    // Convert float32 [-1, 1] to PCM s16le
    const pcm = new Int16Array(samples.length)
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]))
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff
    }

    this.port.postMessage(
      { pcm: pcm.buffer, audioLevel: rms },
      [pcm.buffer] // Transfer ownership for zero-copy
    )

    this._bufferOffset = 0
  }
}

registerProcessor('pcm-processor', PCMProcessor)

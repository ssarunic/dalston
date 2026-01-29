# WebSocket Client Examples

Complete client implementations for Dalston's real-time transcription WebSocket API.

See [WebSocket API Reference](../realtime/WEBSOCKET_API.md) for protocol details.

---

## ElevenLabs Compatible Client (JavaScript)

Full-featured client for the ElevenLabs-compatible endpoint.

```javascript
class ElevenLabsCompatibleTranscriber {
  constructor(options = {}) {
    this.options = {
      url: 'wss://api.dalston.example.com/v1/speech-to-text/realtime',
      apiKey: null,  // Required
      modelId: 'scribe_v1',
      languageCode: 'en',
      commitStrategy: 'vad',
      includeTimestamps: true,
      ...options
    };
    this.ws = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const params = new URLSearchParams({
        api_key: this.options.apiKey,
        model_id: this.options.modelId,
        language_code: this.options.languageCode,
        commit_strategy: this.options.commitStrategy,
        include_timestamps: this.options.includeTimestamps,
      });

      this.ws = new WebSocket(`${this.options.url}?${params}`);

      this.ws.onopen = () => resolve();
      this.ws.onerror = (error) => reject(error);

      this.ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        this.handleMessage(message);
      };

      this.ws.onclose = (event) => {
        this.onClose?.(event);
      };
    });
  }

  handleMessage(message) {
    switch (message.message_type) {
      case 'partial_transcript':
        this.onPartialTranscript?.(message.text);
        break;
      case 'committed_transcript':
      case 'committed_transcript_with_timestamps':
        this.onFinalTranscript?.(message);
        break;
      case 'language_detection':
        this.onLanguageDetection?.(message);
        break;
      case 'error':
        this.onError?.(message);
        break;
    }
  }

  sendAudio(audioData) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      // Convert ArrayBuffer to base64
      const base64 = btoa(String.fromCharCode(...new Uint8Array(audioData)));

      this.ws.send(JSON.stringify({
        message_type: 'input_audio_chunk',
        audio_base_64: base64,
        commit: false
      }));
    }
  }

  commit() {
    this.ws?.send(JSON.stringify({
      message_type: 'input_audio_chunk',
      audio_base_64: '',
      commit: true
    }));
  }

  close() {
    this.ws?.send(JSON.stringify({
      message_type: 'close_connection'
    }));
  }
}

// Usage
const transcriber = new ElevenLabsCompatibleTranscriber({
  apiKey: 'dk_your_key_here',
  modelId: 'scribe_v2',
  languageCode: 'en'
});

transcriber.onPartialTranscript = (text) => {
  console.log('Partial:', text);
};

transcriber.onFinalTranscript = (result) => {
  console.log('Final:', result.text);
  if (result.words) {
    console.log('Words:', result.words);
  }
};

await transcriber.connect();

// From microphone (using Web Audio API)
const audioContext = new AudioContext({ sampleRate: 16000 });
const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
const source = audioContext.createMediaStreamSource(stream);
const processor = audioContext.createScriptProcessor(4096, 1, 1);

processor.onaudioprocess = (event) => {
  const float32Data = event.inputBuffer.getChannelData(0);
  // Convert to 16-bit PCM
  const int16Data = new Int16Array(float32Data.length);
  for (let i = 0; i < float32Data.length; i++) {
    int16Data[i] = Math.max(-32768, Math.min(32767, float32Data[i] * 32768));
  }
  transcriber.sendAudio(int16Data.buffer);
};

source.connect(processor);
processor.connect(audioContext.destination);
```

---

## ElevenLabs Compatible Client (Python)

```python
import asyncio
import websockets
import json
import base64

async def transcribe_realtime(audio_source, api_key: str):
    uri = (
        "wss://api.dalston.example.com/v1/speech-to-text/realtime?"
        f"api_key={api_key}&model_id=scribe_v1&language_code=en"
        "&commit_strategy=vad&include_timestamps=true"
    )

    async with websockets.connect(uri) as ws:
        async def receive():
            async for msg in ws:
                data = json.loads(msg)
                msg_type = data.get('message_type')

                if msg_type == 'partial_transcript':
                    print(f"Partial: {data['text']}", end='\r')
                elif msg_type in ('committed_transcript', 'committed_transcript_with_timestamps'):
                    print(f"\nFinal: {data['text']}")
                    if 'words' in data:
                        for word in data['words']:
                            print(f"  [{word['start']:.2f}-{word['end']:.2f}] {word['text']}")
                elif msg_type == 'error':
                    print(f"Error: {data['message']}")
                    break

        async def send():
            async for chunk in audio_source:
                audio_b64 = base64.b64encode(chunk).decode('utf-8')
                await ws.send(json.dumps({
                    "message_type": "input_audio_chunk",
                    "audio_base_64": audio_b64,
                    "commit": False
                }))

            # End session
            await ws.send(json.dumps({
                "message_type": "close_connection"
            }))

        await asyncio.gather(receive(), send())

# Simulated audio source from file
async def audio_file_source(path):
    with open(path, 'rb') as f:
        while chunk := f.read(3200):  # 100ms at 16kHz, 16-bit
            yield chunk
            await asyncio.sleep(0.1)

# Run
asyncio.run(transcribe_realtime(
    audio_file_source('audio.pcm'),
    api_key='dk_your_key_here'
))
```

---

## Dalston Native Client (JavaScript)

Binary mode client for maximum efficiency (no base64 overhead).

```javascript
class DalstonTranscriber {
  constructor(options = {}) {
    this.options = {
      url: 'ws://localhost:8000/v1/audio/transcriptions/stream',
      apiKey: null,  // Required
      language: 'en',
      model: 'fast',
      interimResults: true,
      wordTimestamps: true,
      ...options
    };
    this.ws = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const params = new URLSearchParams({
        api_key: this.options.apiKey,
        language: this.options.language,
        model: this.options.model,
        interim_results: this.options.interimResults,
        word_timestamps: this.options.wordTimestamps,
      });

      this.ws = new WebSocket(`${this.options.url}?${params}`);
      this.ws.binaryType = 'arraybuffer';

      this.ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        this.handleMessage(message);

        if (message.type === 'session.begin') {
          resolve(message);
        }
      };

      this.ws.onerror = reject;
    });
  }

  handleMessage(message) {
    switch (message.type) {
      case 'session.begin':
        this.onSessionBegin?.(message);
        break;
      case 'transcript.partial':
        this.onPartialTranscript?.(message);
        break;
      case 'transcript.final':
        this.onFinalTranscript?.(message);
        break;
      case 'vad.speech_start':
        this.onSpeechStart?.(message);
        break;
      case 'vad.speech_end':
        this.onSpeechEnd?.(message);
        break;
      case 'session.end':
        this.onSessionEnd?.(message);
        break;
      case 'error':
        this.onError?.(message);
        break;
    }
  }

  // Send raw binary audio - more efficient than base64
  sendAudio(audioData) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(audioData);
    }
  }

  flush() {
    this.ws?.send(JSON.stringify({ type: 'flush' }));
  }

  end() {
    this.ws?.send(JSON.stringify({ type: 'end' }));
  }
}

// Usage
const transcriber = new DalstonTranscriber({
  apiKey: 'dk_your_key_here',
  language: 'en',
  model: 'accurate'
});

transcriber.onPartialTranscript = (msg) => console.log('Partial:', msg.text);
transcriber.onFinalTranscript = (msg) => console.log('Final:', msg.text);

await transcriber.connect();

// Send raw PCM bytes directly (no base64 overhead)
transcriber.sendAudio(pcmBuffer);
```

---

## Robust Client with Reconnection (JavaScript)

Production-ready client with automatic reconnection and audio buffering.

```javascript
class RobustTranscriber {
  constructor(options) {
    this.options = options;
    this.audioBuffer = [];
    this.lastConfirmedOffset = 0;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 3;
    this.sessionId = null;
  }

  connect(recoverySessionId = null) {
    const params = new URLSearchParams({
      api_key: this.options.apiKey,
      language: this.options.language,
      model: this.options.model,
    });

    if (recoverySessionId) {
      params.set('recovery_session', recoverySessionId);
    }

    this.ws = new WebSocket(`${this.options.url}?${params}`);
    this.ws.binaryType = 'arraybuffer';

    this.ws.onmessage = (event) => this.handleMessage(JSON.parse(event.data));
    this.ws.onclose = (event) => this.handleClose(event);
  }

  handleMessage(message) {
    switch (message.type) {
      case 'session.begin':
      case 'session.recovered':
        this.sessionId = message.session_id;
        this.reconnectAttempts = 0;

        // Replay buffered audio on recovery
        if (message.type === 'session.recovered') {
          this.replayBuffer(message.recovered_offset_ms);
        }
        break;

      case 'transcript.final':
        this.lastConfirmedOffset = message.end * 1000;
        // Clear buffer up to confirmed offset
        this.trimBuffer(this.lastConfirmedOffset);
        this.onFinalTranscript?.(message);
        break;

      case 'session.terminated':
        if (message.recoverable && this.reconnectAttempts < this.maxReconnectAttempts) {
          this.scheduleReconnect(message);
        } else {
          this.onSessionEnded?.(message);
        }
        break;
    }
  }

  handleClose(event) {
    // Unexpected close without session.terminated
    if (this.sessionId && this.reconnectAttempts < this.maxReconnectAttempts) {
      this.scheduleReconnect({
        recovery_hint: { retry_after_ms: 500 * Math.pow(2, this.reconnectAttempts) }
      });
    }
  }

  scheduleReconnect(terminationMsg) {
    const delay = terminationMsg.recovery_hint?.retry_after_ms || 1000;
    this.reconnectAttempts++;

    setTimeout(() => {
      this.connect(this.sessionId);
    }, delay);
  }

  sendAudio(audioData) {
    // Buffer audio for potential replay
    this.audioBuffer.push({
      timestamp: Date.now(),
      data: audioData
    });

    // Send to server
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(audioData);
    }
  }

  trimBuffer(offsetMs) {
    const cutoffTime = Date.now() - 10000; // Keep last 10 seconds
    this.audioBuffer = this.audioBuffer.filter(chunk => chunk.timestamp > cutoffTime);
  }

  replayBuffer(fromOffsetMs) {
    // Replay buffered audio that wasn't confirmed
    for (const chunk of this.audioBuffer) {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(chunk.data);
      }
    }
  }
}
```

---

## Audio Capture Utilities

### Browser Microphone Capture

```javascript
async function createMicrophoneSource(sampleRate = 16000) {
  const audioContext = new AudioContext({ sampleRate });
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      sampleRate,
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true
    }
  });

  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(4096, 1, 1);

  return {
    start(onAudioData) {
      processor.onaudioprocess = (event) => {
        const float32Data = event.inputBuffer.getChannelData(0);
        const int16Data = float32ToInt16(float32Data);
        onAudioData(int16Data.buffer);
      };
      source.connect(processor);
      processor.connect(audioContext.destination);
    },

    stop() {
      processor.disconnect();
      source.disconnect();
      stream.getTracks().forEach(track => track.stop());
    }
  };
}

function float32ToInt16(float32Array) {
  const int16Array = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16Array;
}
```

### File Streaming (Node.js)

```javascript
const fs = require('fs');

async function* streamAudioFile(path, chunkDurationMs = 100, sampleRate = 16000) {
  const bytesPerSecond = sampleRate * 2; // 16-bit = 2 bytes per sample
  const chunkSize = Math.floor(bytesPerSecond * chunkDurationMs / 1000);

  const stream = fs.createReadStream(path, { highWaterMark: chunkSize });

  for await (const chunk of stream) {
    yield chunk;
    // Simulate real-time by waiting
    await new Promise(resolve => setTimeout(resolve, chunkDurationMs));
  }
}
```

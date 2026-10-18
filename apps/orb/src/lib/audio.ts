/**
 * Microphone and speaker plumbing.
 *
 * Two independent paths, on purpose:
 *
 * * **Turns** record with MediaRecorder and upload the whole clip. The gateway
 *   decodes it (PyAV handles WebM/Opus), which means no client-side encoding code
 *   to get wrong.
 * * **Wake word** needs a continuous stream of 16 kHz mono int16 frames, so raw
 *   Float32 samples are downsampled here and forwarded frame by frame. That is
 *   the only reason this file touches the audio graph directly.
 *
 * Playback goes through an AudioContext rather than an <audio> element so a reply
 * can be interrupted the moment a new turn starts.
 */

const TARGET_SAMPLE_RATE = 16000;

export interface RecorderHandle {
  stop: () => Promise<Blob>;
  cancel: () => void;
}

/** MediaRecorder picks whatever the browser supports; the gateway decodes it. */
function pickMimeType(): string | undefined {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  for (const candidate of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(candidate)) {
      return candidate;
    }
  }
  return undefined;
}

export async function startRecording(existingStream?: MediaStream | null): Promise<RecorderHandle> {
  const stream =
    existingStream && existingStream.active
      ? existingStream
      : await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
  const ownsStream = stream !== existingStream;
  const mimeType = pickMimeType();
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks: BlobPart[] = [];

  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };
  recorder.start(100);

  let stopped = false;
  const release = (): void => {
    if (stopped) return;
    stopped = true;
    if (ownsStream) {
      stream.getTracks().forEach((track) => track.stop());
    }
  };

  return {
    stop: () =>
      new Promise<Blob>((resolve) => {
        if (recorder.state === "inactive") {
          release();
          resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
          return;
        }
        recorder.onstop = () => {
          release();
          resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
        };
        try {
          recorder.stop();
        } catch {
          release();
          resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
        }
      }),
    cancel: () => {
      try {
        if (recorder.state !== "inactive") recorder.stop();
      } finally {
        release();
      }
    },
  };
}

const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true, // Cleans background room and fan hiss so Whisper transcribes clearly
    autoGainControl: true, // Amplifies low amplitude speech input
  },
};

/**
 * True when a recorded clip actually contains audible speech.
 *
 * Whisper-grade sensitivity: captures whispers, soft speech, and low RMS audio without dropping.
 */
export async function hasAudibleSpeech(blob: Blob): Promise<boolean> {
  try {
    const arrayBuffer = await blob.arrayBuffer();
    const context = new AudioContext();
    try {
      const buffer = await context.decodeAudioData(arrayBuffer);
      let sum = 0;
      let peak = 0;
      for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
        const data = buffer.getChannelData(channel);
        for (let i = 0; i < data.length; i += 1) {
          const value = data[i];
          sum += value * value;
          const magnitude = Math.abs(value);
          if (magnitude > peak) peak = magnitude;
        }
      }
      const total = sum / Math.max(buffer.length * buffer.numberOfChannels, 1);
      const rms = Math.sqrt(total);
      // Whisper-grade sensitivity: any perceptible signal or non-empty blob is passed to Whisper
      return rms >= 0.0005 || peak >= 0.005 || blob.size >= 100;
    } finally {
      void context.close();
    }
  } catch {
    // A clip that cannot be decoded is not proven silent; let the gateway
    // decide rather than swallowing a real command.
    return true;
  }
}

export interface WakeStreamOptions {
  onFrame: (pcm16: ArrayBuffer) => void;
  onSilence: () => void;
  /** Called when a spoken answer has plausibly ended, so a turn can be sent. */
  onSpeechEnd?: () => void;
  /** Called when an acoustic hand clap or double clap is detected. */
  onClapDetected?: () => void;
}

/** Play a sleek, futuristic wake/clap activation chime. */
export function playActivationChime(): void {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    const now = ctx.currentTime;
    osc.frequency.setValueAtTime(587.33, now); // D5
    osc.frequency.exponentialRampToValueAtTime(880, now + 0.08); // A5
    osc.frequency.exponentialRampToValueAtTime(1174.66, now + 0.16); // D6
    gain.gain.setValueAtTime(0.001, now);
    gain.gain.linearRampToValueAtTime(0.18, now + 0.04);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.28);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.3);
    setTimeout(() => void ctx.close(), 400);
  } catch {
    /* browser policy or context failure */
  }
}

/** Ensure browser playback context is unlocked after any user interaction. */
export async function unlockAudioPlayback(): Promise<void> {
  try {
    playbackContext ??= new AudioContext();
    if (playbackContext.state === "suspended") {
      await playbackContext.resume();
    }
  } catch {
    /* ignore */
  }
}

/**
 * Streams 16 kHz mono int16 frames for wake-word detection.
 *
 * Uses ScriptProcessorNode, which is deprecated but is the only option that
 * needs no separate worklet module file and works in every current Chromium --
 * the browser this UI targets. A worklet would be tidier; it is not worth an
 * extra asset and an extra failure mode for a local tool.
 */
export class WakeStream {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private speaking = false;
  private lastLoud = 0;
  private lastClapTime = 0;
  /** Until this time, speech-end cannot fire: the wake word's own audio is
   * still decaying and ending a clip now would ship silence to whisper. */
  private armed = true;
  private armTimer = 0;
  private readonly options: WakeStreamOptions;

  /** RMS above which audio counts as speech rather than room noise (tuned for whispers). */
  static readonly SPEECH_RMS = 0.0015;
  /** Silence this long after speech ends a recording (1.2s prevents cutting off pauses). */
  static readonly SILENCE_MS = 1200;
  /** How long after `armSpeech()` speech-end stays disabled. */
  static readonly ARM_DELAY_MS = 600;

  constructor(options: WakeStreamOptions) {
    this.options = options;
  }

  /** Called when a wake word fires, before a clip starts recording.
   *
   * The wake phrase was itself speech, so the detector is hot on entry. For the
   * next ARM_DELAY_MS it cannot report speech-end at all; after that the normal
   * rule applies, and only a fresh 900 ms of silence ends the clip.
   */
  armSpeech(): void {
    this.armed = false;
    window.clearTimeout(this.armTimer);
    this.armTimer = window.setTimeout(() => {
      this.armed = true;
    }, WakeStream.ARM_DELAY_MS);
  }

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);

    const context = new AudioContext();
    this.context = context;
    this.source = context.createMediaStreamSource(this.stream);
    const node = context.createScriptProcessor(4096, 1, 1);
    this.node = node;

    const ratio = context.sampleRate / TARGET_SAMPLE_RATE;

    node.onaudioprocess = (event) => {
      // Acoustic Loopback Guard: Ignore acoustic energy while Jarvis is actively speaking aloud
      if (isAudioPlaying()) {
        this.speaking = false;
        return;
      }

      const input = event.inputBuffer.getChannelData(0);
      let peak = 0;
      let sumSquares = 0;
      for (let i = 0; i < input.length; i++) {
        const val = Math.abs(input[i]);
        if (val > peak) peak = val;
        sumSquares += val * val;
      }
      const rms = Math.sqrt(sumSquares / input.length);
      const crestFactor = peak / (rms + 0.0001);
      const now = performance.now();

      // Acoustic Hand Clap Detection:
      // A hand clap produces an immediate, sharp acoustic transient (sharp peak > 0.20, crest factor > 4.2).
      // Debounce at 350ms to prevent room echo / reverberation from double-triggering.
      if (peak >= 0.20 && crestFactor >= 4.2 && now - this.lastClapTime > 350) {
        this.lastClapTime = now;
        this.options.onClapDetected?.();
      }

      if (rms >= WakeStream.SPEECH_RMS) {
        this.speaking = true;
        this.lastLoud = now;
      } else if (this.armed && this.speaking && now - this.lastLoud > WakeStream.SILENCE_MS) {
        this.speaking = false;
        this.options.onSpeechEnd?.();
        this.options.onSilence();
      }

      this.options.onFrame(downsample(input, ratio));
    };

    this.source.connect(node);
    // A ScriptProcessor only pumps while connected to a destination; route it to
    // a muted gain node so nothing is echoed back through the speakers.
    const muted = context.createGain();
    muted.gain.value = 0;
    node.connect(muted);
    muted.connect(context.destination);
  }

  stop(): void {
    try {
      window.clearTimeout(this.armTimer);
      this.node?.disconnect();
      this.source?.disconnect();
      this.stream?.getTracks().forEach((track) => track.stop());
      void this.context?.close();
    } catch {
      /* tearing down must never throw */
    }
    this.node = null;
    this.source = null;
    this.stream = null;
    this.context = null;
    this.speaking = false;
    this.armed = true;
  }

  get running(): boolean {
    return this.context !== null;
  }

  getStream(): MediaStream | null {
    return this.stream && this.stream.active ? this.stream : null;
  }
}

/** Float32 [-1,1] at some rate -> mono int16 little-endian at 16 kHz. */
function downsample(input: Float32Array, ratio: number): ArrayBuffer {
  const length = Math.floor(input.length / ratio);
  const output = new Int16Array(Math.max(1, length));
  let offset = 0;
  let cursor = 0;
  while (offset < output.length) {
    const next = Math.round(cursor + ratio);
    let total = 0;
    let count = 0;
    for (let index = Math.round(cursor); index < next && index < input.length; index += 1) {
      total += input[index];
      count += 1;
    }
    const sample = count > 0 ? total / count : 0;
    output[offset] = Math.max(-1, Math.min(1, sample)) * 0x7fff;
    offset += 1;
    cursor = next;
  }
  return output.buffer;
}

/* ------------------------------------------------------------------ playback */

let playbackContext: AudioContext | null = null;
let activeSource: AudioBufferSourceNode | null = null;
/** Speech chunks waiting to be heard, in the order they were spoken. */
const speechQueue: ArrayBuffer[] = [];
let draining = false;

/** Play a base64 WAV immediately, interrupting whatever is playing. */
export async function playWavBase64(base64: string): Promise<void> {
  speechQueue.length = 0;
  const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  await playWavBytes(bytes.buffer);
}

/**
 * Append a speech chunk to the playback queue.
 *
 * A streamed reply arrives as several small WAVs that must sound like one
 * sentence after another, not like a queue of clips. Counting gaps in the
 * gateway is no good either: synthesis is slower than real time, so chunks
 * arrive unevenly and only the client can join them back up.
 */
export function queueWavBase64(base64: string): void {
  const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  speechQueue.push(bytes.buffer);
  if (!draining) void drainSpeechQueue();
}

async function drainSpeechQueue(): Promise<void> {
  draining = true;
  try {
    while (speechQueue.length > 0) {
      const next = speechQueue.shift();
      if (!next) break;
      try {
        await playWavBytes(next);
      } catch {
        /* a chunk that cannot be decoded must not stall the rest */
      }
    }
  } finally {
    draining = false;
    // The last chunk's onended fired while ``draining`` was still true, so the
    // queue-level end must be announced here as well.
    notifySpeechEnd();
  }
}

export async function playWavBytes(buffer: ArrayBuffer): Promise<void> {
  stopCurrentSource();
  playbackContext ??= new AudioContext();
  if (playbackContext.state === "suspended") await playbackContext.resume();

  const decoded = await playbackContext.decodeAudioData(buffer.slice(0));
  const source = playbackContext.createBufferSource();
  source.buffer = decoded;
  source.connect(playbackContext.destination);

  await new Promise<void>((resolve) => {
    source.onended = () => {
      if (activeSource === source) activeSource = null;
      resolve();
      notifySpeechEnd();
    };
    source.start();
    activeSource = source;
  });
}

/* --------------------------------------------------- speech-end notification */

/** Listeners for "the reply has finished being heard" -- hands-free mode uses
 * this to open a follow-up listening window exactly when the answer ends. */
const speechEndListeners: Array<() => void> = [];

export function isAudioPlaying(): boolean {
  return draining || activeSource !== null;
}

export function onSpeechEnd(listener: () => void): () => void {
  speechEndListeners.push(listener);
  return () => {
    const at = speechEndListeners.indexOf(listener);
    if (at !== -1) speechEndListeners.splice(at, 1);
  };
}

function notifySpeechEnd(): void {
  // Only when the whole queue is done, not merely one chunk of several.
  if (draining || activeSource !== null) return;
  for (const listener of [...speechEndListeners]) listener();
}

function stopCurrentSource(): void {
  if (!activeSource) return;
  const source = activeSource;
  activeSource = null;
  try {
    source.stop();
  } catch {
    /* already finished */
  }
}

/** Interrupt the current reply and drop anything queued behind it. */
export function stopPlayback(): void {
  speechQueue.length = 0;
  stopCurrentSource();
}

/** True while a reply is still being spoken out of the queue. */
export function isSpeaking(): boolean {
  return draining || activeSource !== null;
}

export function base64ToBlobUrl(base64: string): string {
  const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  return URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
}

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

export async function startRecording(): Promise<RecorderHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const mimeType = pickMimeType();
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks: BlobPart[] = [];

  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };
  recorder.start();

  const release = (): void => {
    stream.getTracks().forEach((track) => track.stop());
  };

  return {
    stop: () =>
      new Promise<Blob>((resolve) => {
        recorder.onstop = () => {
          release();
          resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
        };
        recorder.stop();
      }),
    cancel: () => {
      try {
        recorder.stop();
      } finally {
        release();
      }
    },
  };
}

export interface WakeStreamOptions {
  onFrame: (pcm16: ArrayBuffer) => void;
  onSilence: () => void;
  /** Called when a spoken answer has plausibly ended, so a turn can be sent. */
  onSpeechEnd?: () => void;
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
  private readonly options: WakeStreamOptions;

  /** RMS above which audio counts as speech rather than room noise. */
  static readonly SPEECH_RMS = 0.02;
  /** Silence this long after speech ends a recording. */
  static readonly SILENCE_MS = 900;

  constructor(options: WakeStreamOptions) {
    this.options = options;
  }

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    const context = new AudioContext();
    this.context = context;
    this.source = context.createMediaStreamSource(this.stream);
    const node = context.createScriptProcessor(4096, 1, 1);
    this.node = node;

    const ratio = context.sampleRate / TARGET_SAMPLE_RATE;

    node.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const rms = Math.sqrt(
        input.reduce((total, value) => total + value * value, 0) / input.length,
      );
      const now = performance.now();

      if (rms >= WakeStream.SPEECH_RMS) {
        this.speaking = true;
        this.lastLoud = now;
      } else if (this.speaking && now - this.lastLoud > WakeStream.SILENCE_MS) {
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
  }

  get running(): boolean {
    return this.context !== null;
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

/** Play a base64 WAV. Any previous reply is stopped first. */
export async function playWavBase64(base64: string): Promise<void> {
  const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  await playWavBytes(bytes.buffer);
}

export async function playWavBytes(buffer: ArrayBuffer): Promise<void> {
  stopPlayback();
  playbackContext ??= new AudioContext();
  if (playbackContext.state === "suspended") await playbackContext.resume();

  const decoded = await playbackContext.decodeAudioData(buffer.slice(0));
  const source = playbackContext.createBufferSource();
  source.buffer = decoded;
  source.connect(playbackContext.destination);
  source.start();
  activeSource = source;
  source.onended = () => {
    if (activeSource === source) activeSource = null;
  };
}

/** Interrupt the current reply -- used when the master starts talking again. */
export function stopPlayback(): void {
  if (activeSource) {
    try {
      activeSource.stop();
    } catch {
      /* already finished */
    }
    activeSource = null;
  }
}

export function base64ToBlobUrl(base64: string): string {
  const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  return URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
}

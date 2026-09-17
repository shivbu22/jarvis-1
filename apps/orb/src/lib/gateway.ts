/**
 * Client for the Jarvis gateway.
 *
 * Everything conversational goes over one websocket, because a turn can be
 * *suspended*: when Jarvis asks permission it holds the call open, so the UI has
 * to keep receiving events and be able to answer while that turn is still
 * running. A request/response HTTP call could not express that.
 */

export const GATEWAY_URL =
  (import.meta.env.VITE_GATEWAY_URL as string | undefined) ?? "http://127.0.0.1:8000";

/** Derive the websocket URL from the gateway URL so the two cannot disagree. */
const wsBase = (): string => GATEWAY_URL.replace(/^http/, "ws");

export interface ToolAttempt {
  name: string;
  args: Record<string, unknown>;
  tier: string;
  decision: string;
  ok: boolean;
  duration_ms: number;
  summary: string;
}

export interface TurnResult {
  transcript: string;
  reply: string;
  audio_b64?: string;
  error?: string | null;
  tools: ToolAttempt[];
  awaiting: Record<string, unknown>[];
}

export interface ConfirmRequest {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  seconds_left: number;
}

export interface Health {
  status: string;
  version: string;
  mode: string;
  active_brain: string | null;
  tool_count: number;
  notes: string[];
  stt: { available: boolean; model: string; device: string; error: string | null };
  tts: { engine: string; available: boolean; voice?: string; error?: string | null };
  wake: { available: boolean; enabled: boolean; model: string; error: string | null };
  policy: { source: string; default_tier: string };
  memory: { memories: number; turns: number };
  confirmations_pending: ConfirmRequest[];
}

export async function fetchHealth(): Promise<Health> {
  const response = await fetch(`${GATEWAY_URL}/health`);
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return (await response.json()) as Health;
}

export async function sendTextTurn(text: string, sessionId = "orb"): Promise<TurnResult> {
  const response = await fetch(`${GATEWAY_URL}/v1/turn/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, session_id: sessionId, speak: true }),
  });
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return (await response.json()) as TurnResult;
}

export type ConnectionState = "connecting" | "open" | "closed";

export interface TurnSocketHandlers {
  onState: (state: ConnectionState) => void;
  onTranscript: (text: string) => void;
  onThinking: () => void;
  /** Streaming answer: called once per token chunk as the brain forms it. */
  onReplyDelta: (text: string) => void;
  onToolStart: (name: string, args: Record<string, unknown>) => void;
  onToolResult: (payload: Record<string, unknown>) => void;
  onConfirmRequest: (request: ConfirmRequest) => void;
  onConfirmResult: (payload: Record<string, unknown>) => void;
  onTurn: (result: TurnResult) => void;
  onError: (message: string) => void;
}

/** A websocket that reconnects itself, since the gateway may restart under it. */
export class TurnSocket {
  private socket: WebSocket | null = null;
  private closed = false;
  private retry = 0;
  private readonly handlers: TurnSocketHandlers;
  private readonly sessionId: string;

  constructor(handlers: TurnSocketHandlers, sessionId = "orb") {
    this.handlers = handlers;
    this.sessionId = sessionId;
  }

  connect(): void {
    this.closed = false;
    this.handlers.onState("connecting");
    const socket = new WebSocket(`${wsBase()}/ws/turn`);
    socket.binaryType = "arraybuffer";
    this.socket = socket;

    socket.onopen = () => {
      this.retry = 0;
      this.handlers.onState("open");
      this.send({ type: "session", session_id: this.sessionId });
    };

    socket.onmessage = (event) => this.dispatch(event);

    socket.onclose = () => {
      this.handlers.onState("closed");
      if (this.closed) return;
      // Back off so a stopped gateway is not hammered, but recover quickly.
      const delay = Math.min(500 * 2 ** this.retry++, 5000);
      window.setTimeout(() => this.connect(), delay);
    };

    socket.onerror = () => {
      /* onclose always follows; handled there. */
    };
  }

  private dispatch(event: MessageEvent): void {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(String(event.data)) as Record<string, unknown>;
    } catch {
      return;
    }
    const type = String(payload.type ?? "");
    switch (type) {
      case "transcript":
        this.handlers.onTranscript(String(payload.text ?? ""));
        break;
      case "thinking":
        this.handlers.onThinking();
        break;
      case "reply_delta":
        this.handlers.onReplyDelta(String(payload.text ?? ""));
        break;
      case "tool_start":
        this.handlers.onToolStart(
          String(payload.tool ?? ""),
          (payload.args as Record<string, unknown>) ?? {},
        );
        break;
      case "tool_result":
      case "speaking":
        this.handlers.onToolResult(payload);
        break;
      case "confirm_request":
        this.handlers.onConfirmRequest(payload as unknown as ConfirmRequest);
        break;
      case "confirm_result":
      case "confirmation_voice":
        this.handlers.onConfirmResult(payload);
        break;
      case "turn_result":
        this.handlers.onTurn(payload as unknown as TurnResult);
        break;
      case "error":
        this.handlers.onError(String(payload.message ?? "unknown error"));
        break;
      default:
        break;
    }
  }

  send(payload: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  sendAudio(blob: Blob): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(blob);
    }
  }

  answer(id: string, approved: boolean): void {
    this.send({ type: "confirm", id, approved });
  }

  speak(text: string): void {
    this.send({ type: "text", text });
  }

  close(): void {
    this.closed = true;
    this.socket?.close();
    this.socket = null;
  }
}

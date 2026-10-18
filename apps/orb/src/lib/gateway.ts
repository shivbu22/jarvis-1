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
  /** Speech arrived chunk by chunk while the reply was still being written. */
  speech_streamed?: boolean;
  /** The spoken chunks did not match the final reply text. */
  speech_incomplete?: boolean;
  /** Per-stage seconds: transcribe, think, first_sound, capture. */
  stages?: Record<string, number>;
}

export interface ConfirmRequest {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  seconds_left: number;
}

/** Jarvis is asking the master something and is holding the turn open for it. */
export interface QuestionRequest {
  id: string;
  question: string;
  options: string[];
  seconds_left: number;
}

export interface Health {
  status: string;
  version: string;
  uptime_seconds?: number;
  mode: string;
  active_brain: string | null;
  tool_count: number;
  notes: string[];
  stt: { available: boolean; model: string; device: string; error: string | null };
  tts: { engine: string; available: boolean; voice?: string; error?: string | null };
  wake: {
    available: boolean;
    /** Can run right now, with no one-time weight fetch first. */
    ready: boolean;
    enabled: boolean;
    model: string;
    weights_present: boolean;
    missing_weights: string[];
    error: string | null;
  };
  policy: { source: string; default_tier: string };
  memory: { memories: number; turns: number };
  hardware?: {
    cpu_percent: number;
    ram_percent: number;
    ram_gb: string;
    gpu: string;
    vram_mb: string;
    cuda?: boolean;
  };
  subagents?: {
    total: number;
    active: number;
    completed: number;
    failed: number;
  };
  obsidian_brain?: {
    total_notes: number;
    vault_path: string;
  };
  self_heal?: {
    watchdog_running: boolean;
    interval_seconds: number;
    total_runs: number;
    last_status: string;
    last_run_timestamp: string | null;
  };
  confirmations_pending: ConfirmRequest[];
  questions_pending: QuestionRequest[];
}

export interface SubagentRecord {
  id: string;
  role: string;
  goal: string;
  status: "running" | "completed" | "failed" | "timed_out";
  created_at: string;
  elapsed_seconds?: number;
  result?: { ok: boolean; summary: string };
}

export interface ObsidianNoteItem {
  path: string;
  title: string;
  category: string;
  size_bytes: number;
  words: number;
}

export interface MarketIndex {
  symbol: string;
  name: string;
  price: string;
  change: string;
  up: boolean;
}

export interface PortfolioPosition {
  symbol: string;
  quantity: number;
  avg_price: number;
}

export interface PortfolioState {
  cash_balance: number;
  cost_basis: number;
  total_portfolio_value: number;
  positions_count: number;
  positions: PortfolioPosition[];
  recent_trades: any[];
}

export interface VentureKPI {
  label: string;
  value: string;
  trend: string;
}

export interface VentureData {
  id: string;
  name: string;
  tagline: string;
  stage: string;
  target: string;
  kpis: VentureKPI[];
  status: string;
}

export interface OverviewData {
  health: Health;
  subagents: {
    total: number;
    active: number;
    completed: number;
    failed: number;
    recent: SubagentRecord[];
  };
  obsidian_brain: {
    total_notes: number;
    notes: ObsidianNoteItem[];
  };
  learning: {
    total_executions: number;
    success_rate_percent: number;
    avg_quality_rating: number;
    tool_effectiveness: Array<{ tool: string; calls: number; success_rate: number; avg_duration_ms: number }>;
    weekly_velocity: number;
  };
  market: {
    indices: MarketIndex[];
    portfolio: PortfolioState;
  };
  ventures: Record<string, VentureData>;
  scheduler?: {
    tasks: ScheduledTaskItem[];
    count: number;
  };
  profile?: UserProfileData;
  context_metrics?: {
    running_summary_tokens: number;
    total_turns_processed: number;
    total_tokens_saved: number;
    max_context_tokens: number;
    keep_recent: number;
    has_summary: boolean;
  };
  notifications?: Array<{
    id: string;
    title: string;
    message: string;
    urgency: string;
    timestamp: number;
    channel: string;
  }>;
  evolution?: {
    rules_count: number;
    rules: EvolutionRuleItem[];
  };
}

export interface EvolutionRuleItem {
  rule_id: string;
  category: string;
  trigger_pattern: string;
  rule_text: string;
  source: string;
  confidence: number;
  times_applied: number;
  created_at: number;
  last_applied_at?: number | null;
}

export interface ScheduledTaskItem {
  task_id: string;
  kind: "reminder" | "delayed" | "cron";
  created_at: number;
  fire_at?: number | null;
  fire_at_iso?: string;
  cron_expr?: string | null;
  message: string;
  tool_name?: string | null;
  tool_args?: Record<string, any>;
  repeat_count: number;
  max_repeats: number;
  enabled: boolean;
  label: string;
}

export interface UserProfileData {
  name?: string;
  preferred_name?: string;
  timezone?: string;
  language?: string;
  temperature_unit?: string;
  date_format?: string;
  currency?: string;
  communication_style?: string;
  verbosity?: string;
  wake_word?: string;
  favorite_apps?: string[];
  work_hours?: { start: string; end: string };
  interests?: string[];
  custom_preferences?: Record<string, any>;
  created_at?: number;
  updated_at?: number;
}

export async function fetchHealth(): Promise<Health> {
  const response = await fetch(`${GATEWAY_URL}/health`);
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return (await response.json()) as Health;
}

export async function fetchOverview(): Promise<OverviewData> {
  const response = await fetch(`${GATEWAY_URL}/api/overview`);
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return (await response.json()) as OverviewData;
}

export async function fetchNoteContent(path: string): Promise<{ path: string; content: string }> {
  const response = await fetch(`${GATEWAY_URL}/api/notes/${encodeURIComponent(path)}`);
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return (await response.json()) as { path: string; content: string };
}

export async function spawnSubagent(goal: string, role = "assistant", context = ""): Promise<any> {
  const response = await fetch(`${GATEWAY_URL}/api/subagents/spawn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, role, context }),
  });
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return await response.json();
}

export async function triggerSelfHealDiagnose(): Promise<any> {
  const response = await fetch(`${GATEWAY_URL}/api/self_heal/diagnose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return await response.json();
}

export async function executePaperTrade(symbol: string, side: string, quantity: number, price?: number): Promise<any> {
  const response = await fetch(`${GATEWAY_URL}/api/trade`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol, side, quantity, price }),
  });
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return await response.json();
}

export async function switchBrain(target?: string): Promise<{ ok: boolean; active_brain: string }> {
  const response = await fetch(`${GATEWAY_URL}/api/brain/switch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(target ? { target } : {}),
  });
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return (await response.json()) as { ok: boolean; active_brain: string };
}

export async function toggleUltimatePolicy(enabled?: boolean): Promise<{ ok: boolean; ultimate_permission: boolean }> {
  const response = await fetch(`${GATEWAY_URL}/api/policy/ultimate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(enabled === undefined ? {} : { enabled }),
  });
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return (await response.json()) as { ok: boolean; ultimate_permission: boolean };
}

export interface TrainingStatus {
  status: string;
  sft_samples: number;
  colab_notebook_ready: boolean;
  vault_notes: number;
  enrolled_speakers: number;
  speakers: Array<{ name: string; description?: string; sample_count?: number }>;
  voice_ref_present: boolean;
}

export async function fetchTrainingStatus(): Promise<TrainingStatus> {
  const response = await fetch(`${GATEWAY_URL}/api/training/status`);
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return (await response.json()) as TrainingStatus;
}

export async function triggerBuildDataset(): Promise<{ ok: boolean; output: string }> {
  const response = await fetch(`${GATEWAY_URL}/api/training/dataset`, { method: "POST" });
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return await response.json();
}

export async function triggerCompileModelfile(): Promise<{ ok: boolean; output: string }> {
  const response = await fetch(`${GATEWAY_URL}/api/training/modelfile`, { method: "POST" });
  if (!response.ok) throw new Error(`gateway returned ${response.status}`);
  return await response.json();
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
  /** Take back the streaming message: it turned out to be tool-call JSON. */
  onReplyRetract: () => void;
  onToolStart: (name: string, args: Record<string, unknown>) => void;
  onToolResult: (payload: Record<string, unknown>) => void;
  /** A chunk of spoken reply, to be played in arrival order. */
  onAudio: (base64: string, text: string) => void;
  onConfirmRequest: (request: ConfirmRequest) => void;
  onConfirmResult: (payload: Record<string, unknown>) => void;
  onQuestion: (request: QuestionRequest) => void;
  onQuestionResult: (payload: Record<string, unknown>) => void;
  onTurn: (result: TurnResult) => void;
  onError: (message: string) => void;
}

/** A websocket that reconnects itself, since the gateway may restart under it. */
export class TurnSocket {
  /** Outbound messages held while the socket is down. */
  static readonly MAX_PENDING = 20;
  private socket: WebSocket | null = null;
  private closed = false;
  private retry = 0;
  /** Words spoken while the gateway was restarting, waiting to be delivered. */
  private queue: Array<string | Blob> = [];
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
      this.flushPending();
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
      case "reply_retract":
        this.handlers.onReplyRetract();
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
      case "audio":
        this.handlers.onAudio(String(payload.b64 ?? ""), String(payload.text ?? ""));
        break;
      case "question":
        this.handlers.onQuestion(payload as unknown as QuestionRequest);
        break;
      case "question_result":
        this.handlers.onQuestionResult(payload);
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
    this.sendRaw(JSON.stringify(payload));
  }

  sendAudio(blob: Blob): void {
    this.sendRaw(blob);
  }

  /** Send now when the socket is open; hold and flush after reconnecting.
   *
   * Dropping a message because the socket happened to be between retries is
   * how a gateway restart under the orb made it look deaf: the words simply
   * vanished and nothing ever reached the gateway. A bounded queue keeps a
   * long outage from accumulating unplayed clips.
   */
  private sendRaw(payload: string | Blob): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(payload);
      return;
    }
    if (this.queue.length >= TurnSocket.MAX_PENDING) {
      this.queue.shift();
    }
    this.queue.push(payload);
  }

  private flushPending(): void {
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    while (this.queue.length > 0) {
      const payload = this.queue[0];
      try {
        socket.send(payload);
      } catch {
        return; // the socket died mid-flush; onclose schedules the next try
      }
      this.queue.shift();
    }
  }

  answer(id: string, approved: boolean): void {
    this.send({ type: "confirm", id, approved });
  }

  /** Answer a question Jarvis asked, resuming the turn it is holding open. */
  answerQuestion(id: string, text: string): void {
    this.send({ type: "answer", id, text });
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

export interface SkillItem {
  name: string;
  path: string;
  description: string;
}

export interface SkillDetails {
  name: string;
  path: string;
  instructions: string;
}

export async function fetchSkills(filter = ""): Promise<{ total: number; skills: SkillItem[] }> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/skills?filter=${encodeURIComponent(filter)}`);
    if (!res.ok) return { total: 0, skills: [] };
    return await res.json();
  } catch {
    return { total: 0, skills: [] };
  }
}

export async function fetchSkillDetails(name: string): Promise<SkillDetails | null> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/skills/${encodeURIComponent(name)}`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function triggerLatencyBenchmark(): Promise<{
  success: boolean;
  duration_ms: number;
  tokens_per_second: number;
  eval_count: number;
  reply: string;
}> {
  try {
    const start = performance.now();
    const res = await fetch(`${GATEWAY_URL}/v1/turn/text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: "Report diagnostic latency status and memory vitals in one crisp sentence.",
        speak: false,
      }),
    });
    const duration = Math.round(performance.now() - start);
    if (!res.ok) return { success: false, duration_ms: duration, tokens_per_second: 0, eval_count: 0, reply: "Error" };
    const data = await res.json();
    const turn = data.turn || {};
    return {
      success: true,
      duration_ms: turn.duration_ms || duration,
      tokens_per_second: turn.tokens_per_second || 0,
      eval_count: turn.eval_count || 0,
      reply: data.reply || "",
    };
  } catch (err: any) {
    return { success: false, duration_ms: 0, tokens_per_second: 0, eval_count: 0, reply: String(err) };
  }
}

export async function fetchScheduledTasks(): Promise<ScheduledTaskItem[]> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/scheduler/tasks`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.tasks || [];
  } catch {
    return [];
  }
}

export async function scheduleTask(
  message: string,
  timeExpression: string = "in 1 minute",
  cronExpression: string = ""
): Promise<boolean> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/scheduler/schedule`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        time_expression: timeExpression,
        cron_expression: cronExpression,
      }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function cancelScheduledTask(taskId: string): Promise<boolean> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/scheduler/tasks/${encodeURIComponent(taskId)}`, {
      method: "DELETE",
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function updateUserProfile(updates: Partial<UserProfileData>): Promise<boolean> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/profile`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchEvolutionRules(): Promise<EvolutionRuleItem[]> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/evolution/rules`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.rules || [];
  } catch {
    return [];
  }
}

export async function addEvolutionRule(rule: string, category: string = "general", triggerPattern: string = "*"): Promise<boolean> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/evolution/rules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rule, category, trigger_pattern: triggerPattern }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function deleteEvolutionRule(ruleId: string): Promise<boolean> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/evolution/rules/${encodeURIComponent(ruleId)}`, {
      method: "DELETE",
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function triggerExecutiveBriefing(deliverToast: boolean = true, speak: boolean = false): Promise<any> {
  try {
    const res = await fetch(`${GATEWAY_URL}/api/evolution/briefing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deliver_toast: deliverToast, speak }),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.briefing || null;
  } catch {
    return null;
  }
}

export interface UploadedFileResult {
  ok: boolean;
  filename: string;
  path: string;
  size_bytes: number;
  content_type: string;
  extracted_text: string;
  preview: string;
  is_image: boolean;
  is_document: boolean;
}

export async function uploadFile(file: File, prompt: string = ""): Promise<UploadedFileResult> {
  const formData = new FormData();
  formData.append("file", file);
  if (prompt) {
    formData.append("prompt", prompt);
  }
  const res = await fetch(`${GATEWAY_URL}/api/upload`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`Upload failed (${res.status}): ${errorText}`);
  }
  return (await res.json()) as UploadedFileResult;
}



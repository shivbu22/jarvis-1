import type { ConfirmRequest, ConnectionState, Health, ToolAttempt } from "../lib/gateway";

type Phase = "idle" | "listening" | "thinking" | "speaking";

/** The breathing orb. Pure CSS animation, driven by state rather than JS. */
export function Orb({ phase }: { phase: Phase }) {
  return (
    <div className={`orb orb--${phase}`} role="img" aria-label={`Jarvis is ${phase}`}>
      <div className="orb__core" />
      <div className="orb__ring" />
      <div className="orb__ring orb__ring--slow" />
    </div>
  );
}

export function StatusBar({
  health,
  connection,
}: {
  health: Health | null;
  connection: ConnectionState;
}) {
  const brain = health?.active_brain ?? "unknown";
  const local = health?.mode === "local";
  const voice = health?.tts.engine === "chatterbox" ? "cloned voice" : health?.tts.engine ?? "none";

  return (
    <div className="status">
      <span className={`pill pill--${connection === "open" ? "ok" : "bad"}`}>
        {connection === "open" ? "connected" : connection}
      </span>
      <span className="pill">{brain}</span>
      <span className={`pill ${local ? "pill--ok" : "pill--cloud"}`}>
        {local ? "local only" : "cloud brain"}
      </span>
      <span className={`pill ${health?.stt.available ? "pill--ok" : "pill--bad"}`}>
        ears: {health?.stt.available ? health.stt.device : "off"}
      </span>
      <span className="pill">voice: {voice}</span>
      {health?.wake.available && <span className="pill pill--ok">wake ready</span>}
    </div>
  );
}

export function Conversation({
  messages,
}: {
  messages: { id: number; role: "user" | "jarvis" | "system"; text: string }[];
}) {
  return (
    <section className="panel">
      <h2>Conversation</h2>
      {messages.length === 0 ? (
        <p className="muted">Nothing yet. Try "what's the weather in Mumbai".</p>
      ) : (
        <ol className="log">
          {messages.map((message) => (
            <li key={message.id} className={`log__row log__row--${message.role}`}>
              <span className="log__who">
                {message.role === "jarvis" ? "JARVIS" : message.role === "user" ? "YOU" : "·"}
              </span>
              <span className="log__text">{message.text}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** Tool decisions, including the ones that were refused -- the point of the log. */
export function ToolTrace({ tools }: { tools: ToolAttempt[] }) {
  return (
    <section className="panel">
      <h2>Actions</h2>
      {tools.length === 0 ? (
        <p className="muted">No actions yet. Anything that changes this machine appears here.</p>
      ) : (
        <ol className="trace">
          {tools.map((tool, index) => (
            <li key={`${tool.name}-${index}`} className={`trace__row trace__row--${verdict(tool)}`}>
              <span className="trace__name">{tool.name}</span>
              <span className="trace__verdict">{verdict(tool)}</span>
              <span className="trace__meta">
                {tool.tier}
                {tool.duration_ms ? ` · ${tool.duration_ms}ms` : ""}
              </span>
              {tool.summary && <p className="trace__summary">{tool.summary}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function verdict(tool: ToolAttempt): string {
  if (tool.decision === "denied") return "refused by policy";
  if (tool.decision === "declined") return "you declined";
  if (tool.decision === "awaiting") return "needs your permission";
  if (tool.decision === "running") return "running";
  if (!tool.ok) return "failed";
  return "done";
}

/** The permission prompt. Refusing is as easy as approving, and it expires. */
export function ConfirmBar({
  pending,
  onAnswer,
}: {
  pending: ConfirmRequest | null;
  onAnswer: (approved: boolean) => void;
}) {
  if (!pending) return null;
  return (
    <div className="confirm" role="alertdialog" aria-label="Permission required">
      <div className="confirm__text">
        <strong>Permission needed</strong>
        <p>
          {pending.summary || pending.tool}
          <span className="confirm__tool">{pending.tool}</span>
        </p>
        <p className="muted">
          Say yes or no, or use the buttons. Silence counts as no.
        </p>
      </div>
      <div className="confirm__buttons">
        <button type="button" className="approve" onClick={() => onAnswer(true)}>
          ALLOW
        </button>
        <button type="button" className="deny" onClick={() => onAnswer(false)}>
          REFUSE
        </button>
      </div>
    </div>
  );
}

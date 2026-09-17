import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ConfirmBar, Conversation, Orb, StatusBar, ToolTrace } from "./components/Panels";
import {
  playWavBase64,
  startRecording,
  stopPlayback,
  WakeStream,
  type RecorderHandle,
} from "./lib/audio";
import {
  fetchHealth,
  GATEWAY_URL,
  TurnSocket,
  type ConfirmRequest,
  type ConnectionState,
  type Health,
  type ToolAttempt,
} from "./lib/gateway";

type Phase = "idle" | "listening" | "thinking" | "speaking";

interface Message {
  id: number;
  role: "user" | "jarvis" | "system";
  text: string;
  /** True while reply_delta frames are still appending to this message. */
  streaming?: boolean;
}

let messageSeq = 0;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [phase, setPhase] = useState<Phase>("idle");
  const [messages, setMessages] = useState<Message[]>([]);
  const [tools, setTools] = useState<ToolAttempt[]>([]);
  const [pending, setPending] = useState<ConfirmRequest | null>(null);
  const [notice, setNotice] = useState<string>("");
  const [wakeOn, setWakeOn] = useState(false);
  const [draft, setDraft] = useState("");

  const socketRef = useRef<TurnSocket | null>(null);
  const recorderRef = useRef<RecorderHandle | null>(null);
  const wakeRef = useRef<WakeStream | null>(null);
  const wakeSocketRef = useRef<WebSocket | null>(null);
  const autoRecordRef = useRef(false);

  const say = useCallback((role: Message["role"], text: string) => {
    if (!text.trim()) return;
    setMessages((current) => [...current, { id: (messageSeq += 1), role, text }]);
  }, []);

  /* ---------------------------------------------------------------- socket */

  useEffect(() => {
    const socket = new TurnSocket({
      onState: setConnection,
      onTranscript: (text) => {
        // A new turn begins: seal any message still flagged streaming so the
        // first delta of this turn cannot append to a stale one.
        setMessages((current) =>
          current.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
        );
        // Typed turns are added locally by submitText and echoed back over the
        // bus; only add when it is not already the last message.
        setMessages((current) => {
          const last = current[current.length - 1];
          if (last && last.role === "user" && last.text === text) return current;
          return [...current, { id: (messageSeq += 1), role: "user", text }];
        });
        setPhase("thinking");
      },
      onThinking: () => setPhase("thinking"),
      onReplyDelta: (piece) => {
        setPhase("speaking");
        setMessages((current) => {
          const last = current[current.length - 1];
          if (last && last.role === "jarvis" && last.streaming) {
            return [
              ...current.slice(0, -1),
              { ...last, text: last.text + piece },
            ];
          }
          return [...current, { id: (messageSeq += 1), role: "jarvis", text: piece, streaming: true }];
        });
      },
      onToolStart: (name, args) =>
        setTools((current) => [
          ...current,
          {
            name,
            args,
            tier: "confirm",
            decision: "running",
            ok: true,
            duration_ms: 0,
            summary: "",
          },
        ]),
      onToolResult: () => undefined,
      onConfirmRequest: (request) => {
        setPending(request);
        setPhase("idle");
      },
      onConfirmResult: () => setPending(null),
      onTurn: (result) => {
        // Seal the streaming message (drop its flag) and append the final reply
        // only if streaming never delivered it.
        setMessages((current) => {
          const last = current[current.length - 1];
          const sealed =
            last && last.role === "jarvis" && last.streaming
              ? [...current.slice(0, -1), { ...last, streaming: false }]
              : current;
          if (result.reply) {
            const sealedLast = sealed[sealed.length - 1];
            const alreadyShown =
              sealedLast && sealedLast.role === "jarvis" && sealedLast.text === result.reply;
            if (!alreadyShown) {
              return [...sealed, { id: (messageSeq += 1), role: "jarvis", text: result.reply }];
            }
          }
          return sealed;
        });
        if (result.error && result.error !== "empty_transcript") {
          setNotice(result.error);
        }
        setTools(result.tools ?? []);
        setPending(null);
        if (result.audio_b64) {
          setPhase("speaking");
          void playWavBase64(result.audio_b64).finally(() => setPhase("idle"));
        } else {
          setPhase("idle");
        }
      },
      onError: (message) => {
        setNotice(message);
        setPhase("idle");
      },
    });
    socketRef.current = socket;
    socket.connect();
    return () => socket.close();
  }, [say]);

  /* ---------------------------------------------------------------- health */

  const refreshHealth = useCallback(async () => {
    try {
      const next = await fetchHealth();
      setHealth(next);
      if (next.confirmations_pending?.length) {
        setPending(next.confirmations_pending[0]);
      }
    } catch {
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
    const timer = window.setInterval(() => void refreshHealth(), 15000);
    return () => window.clearInterval(timer);
  }, [refreshHealth]);

  /* ------------------------------------------------------------- recording */

  const beginRecording = useCallback(async () => {
    if (recorderRef.current) return;
    stopPlayback(); // talking over a reply stops it
    try {
      recorderRef.current = await startRecording();
      setPhase("listening");
      setNotice("");
    } catch (error) {
      setNotice(`Microphone unavailable: ${(error as Error).message}`);
    }
  }, []);

  const finishRecording = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    recorderRef.current = null;
    try {
      const blob = await recorder.stop();
      if (blob.size < 1500) {
        setPhase("idle");
        return;
      }
      setPhase("thinking");
      socketRef.current?.sendAudio(blob);
    } catch (error) {
      setNotice(`Recording failed: ${(error as Error).message}`);
      setPhase("idle");
    }
  }, []);

  const cancelRecording = useCallback(() => {
    recorderRef.current?.cancel();
    recorderRef.current = null;
    setPhase("idle");
  }, []);

  /* ------------------------------------------------------------- wake word */

  const stopWake = useCallback(() => {
    wakeRef.current?.stop();
    wakeRef.current = null;
    wakeSocketRef.current?.close();
    wakeSocketRef.current = null;
    setWakeOn(false);
  }, []);

  const startWake = useCallback(async () => {
    if (!health?.wake.available) {
      setNotice(
        health?.wake.error ??
          "Wake word needs openwakeword. Install the voice extra, then restart the gateway.",
      );
      return;
    }
    const wakeSocket = new WebSocket(`${GATEWAY_URL.replace(/^http/, "ws")}/ws/wake`);
    wakeSocket.binaryType = "arraybuffer";
    wakeSocketRef.current = wakeSocket;

    wakeSocket.onmessage = (event) => {
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(String(event.data)) as Record<string, unknown>;
      } catch {
        return;
      }
      if (payload.type === "wake_unavailable") {
        setNotice(String(payload.message ?? "wake word unavailable"));
        stopWake();
        return;
      }
      if (payload.type === "wake_detected") {
        say("system", `Heard "Hey Jarvis" (${payload.score}).`);
        // A wake word starts a clip automatically; the silence detector ends it.
        autoRecordRef.current = true;
        void beginRecording();
      }
    };

    try {
      const stream = new WakeStream({
        onFrame: (frame) => {
          if (wakeSocket.readyState === WebSocket.OPEN) wakeSocket.send(frame);
        },
        onSilence: () => undefined,
        onSpeechEnd: () => {
          if (autoRecordRef.current && recorderRef.current) {
            autoRecordRef.current = false;
            void finishRecording();
          }
        },
      });
      await stream.start();
      wakeRef.current = stream;
      setWakeOn(true);
      setNotice('Listening for "Hey Jarvis".');
    } catch (error) {
      setNotice(`Wake word needs microphone access: ${(error as Error).message}`);
      stopWake();
    }
  }, [beginRecording, finishRecording, health, say, stopWake]);

  useEffect(() => () => stopWake(), [stopWake]);

  /* ------------------------------------------------------------------ text */

  const submitText = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      const text = draft.trim();
      if (!text) return;
      setDraft("");
      say("user", text);
      setPhase("thinking");
      socketRef.current?.speak(text);
    },
    [draft, say],
  );

  const answer = useCallback((approved: boolean) => {
    if (!pending) return;
    socketRef.current?.answer(pending.id, approved);
    say("system", approved ? "Approved." : "Refused.");
    setPending(null);
    setPhase("thinking");
  }, [pending, say]);

  /* ------------------------------------------------------------------ view */

  const sttReady = health?.stt.available ?? false;
  const hint = useMemo(() => {
    if (connection !== "open") return "Connecting to the gateway on port 8000...";
    if (!sttReady) return "Speech recognition is not installed, so type instead.";
    if (wakeOn) return 'Listening for "Hey Jarvis", or hold the button.';
    return "Hold the button and speak.";
  }, [connection, sttReady, wakeOn]);

  return (
    <div className="shell">
      <header className="masthead">
        <h1>
          JARVIS <span className="version">v{health?.version ?? "..."}</span>
        </h1>
        <StatusBar health={health} connection={connection} />
      </header>

      <main className="stage">
        <Orb phase={phase} />
        <p className="hint">{notice || hint}</p>

        <div className="controls">
          <button
            type="button"
            className={`talk ${phase === "listening" ? "talk--active" : ""}`}
            disabled={connection !== "open" || !sttReady}
            onPointerDown={(event) => {
              event.preventDefault();
              void beginRecording();
            }}
            onPointerUp={() => void finishRecording()}
            onPointerLeave={() => void finishRecording()}
            onKeyDown={(event) => event.key === " " && void beginRecording()}
            onKeyUp={(event) => event.key === " " && void finishRecording()}
          >
            {phase === "listening" ? "RELEASE TO SEND" : "HOLD TO TALK"}
          </button>

          <button
            type="button"
            className={`ghost ${wakeOn ? "ghost--on" : ""}`}
            onClick={() => (wakeOn ? stopWake() : void startWake())}
          >
            {wakeOn ? "WAKE WORD ON" : "ENABLE WAKE WORD"}
          </button>

          {phase === "listening" && (
            <button type="button" className="ghost" onClick={cancelRecording}>
              CANCEL
            </button>
          )}
        </div>

        <form className="typed" onSubmit={submitText}>
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={sttReady ? "or type a command..." : "type a command..."}
            aria-label="Type a command"
          />
          <button type="submit" disabled={connection !== "open" || !draft.trim()}>
            SEND
          </button>
        </form>
      </main>

      <ConfirmBar pending={pending} onAnswer={answer} />

      <section className="panels">
        <Conversation messages={messages} />
        <ToolTrace tools={tools} />
      </section>

      <Footer health={health} />
    </div>
  );
}

function Footer({ health }: { health: Health | null }) {
  if (!health) return null;
  return (
    <footer className="footer">
      <span>{health.policy.source}</span>
      <span>
        {health.memory.memories} note(s), {health.memory.turns} turn(s) stored
      </span>
      <span>{health.tool_count} tools</span>
    </footer>
  );
}

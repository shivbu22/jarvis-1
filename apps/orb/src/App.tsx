import { useCallback, useEffect, useRef, useState } from "react";
import { MinimalHeader } from "./components/MinimalHeader";
import { MinimalOrb, type OrbPhase } from "./components/MinimalOrb";
import { ConversationFeed, type MessageItem } from "./components/ConversationFeed";
import { MinimalControls } from "./components/MinimalControls";
import {
  isSpeaking,
  onSpeechEnd,
  playActivationChime,
  queueWavBase64,
  startRecording,
  stopPlayback,
  unlockAudioPlayback,
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
  type QuestionRequest,
  type TurnResult,
} from "./lib/gateway";

function nextMessageId(): number {
  const w = window as unknown as { __jarvisMessageSeq?: number };
  w.__jarvisMessageSeq = (w.__jarvisMessageSeq ?? 0) + 1;
  return w.__jarvisMessageSeq;
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [phase, setPhase] = useState<OrbPhase>("idle");
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [draft, setDraft] = useState<string>("");
  const [pendingConfirm, setPendingConfirm] = useState<ConfirmRequest | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<QuestionRequest | null>(null);
  const [wakeEnabled, setWakeEnabled] = useState<boolean>(false);
  const [isMuted, setIsMuted] = useState<boolean>(false);

  // References
  const socketRef = useRef<TurnSocket | null>(null);
  const recorderRef = useRef<RecorderHandle | null>(null);
  const wakeStreamRef = useRef<WakeStream | null>(null);
  const wakeWsRef = useRef<WebSocket | null>(null);
  const currentReplyIdRef = useRef<number | null>(null);

  // Poll gateway health
  const refreshHealth = useCallback(async () => {
    try {
      const h = await fetchHealth();
      setHealth(h);
    } catch {
      // Gateway might be reloading
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
    const interval = setInterval(refreshHealth, 5000);
    return () => clearInterval(interval);
  }, [refreshHealth]);

  // Voice playback monitoring
  useEffect(() => {
    onSpeechEnd(() => {
      setPhase((prev) => (prev === "speaking" ? "idle" : prev));
    });
  }, []);

  // Stop recording and send audio to Gateway
  const stopMic = useCallback(async () => {
    if (!recorderRef.current) return;
    const handle = recorderRef.current;
    recorderRef.current = null;
    setPhase("thinking");

    try {
      const blob = await handle.stop();
      if (blob && blob.size > 0 && socketRef.current) {
        socketRef.current.sendAudio(blob);
      } else {
        setPhase("idle");
      }
    } catch (err) {
      console.error("Audio recording stop failed:", err);
      setPhase("idle");
    }
  }, []);

  // Start recording
  const startMic = useCallback(async () => {
    await unlockAudioPlayback();
    stopPlayback();

    if (recorderRef.current) {
      await stopMic();
      return;
    }

    try {
      setPhase("listening");
      const handle = await startRecording();
      recorderRef.current = handle;
    } catch (err) {
      console.error("Microphone access failed:", err);
      setPhase("idle");
    }
  }, [stopMic]);

  // Initialize WebSocket turn connection
  useEffect(() => {
    const socket = new TurnSocket({
      onState: (state) => {
        setConnection(state);
      },
      onTranscript: (text) => {
        setPhase("thinking");
        if (text.trim()) {
          setMessages((prev) => [
            ...prev,
            { id: nextMessageId(), role: "user", text: text.trim() },
          ]);
        }
      },
      onThinking: () => {
        setPhase("thinking");
      },
      onReplyDelta: (chunk) => {
        setPhase("thinking");
        setMessages((prev) => {
          if (currentReplyIdRef.current === null) {
            const newId = nextMessageId();
            currentReplyIdRef.current = newId;
            return [
              ...prev,
              { id: newId, role: "jarvis", text: chunk, streaming: true },
            ];
          }
          return prev.map((m) =>
            m.id === currentReplyIdRef.current
              ? { ...m, text: m.text + chunk }
              : m
          );
        });
      },
      onReplyRetract: () => {
        if (currentReplyIdRef.current !== null) {
          setMessages((prev) =>
            prev.filter((m) => m.id !== currentReplyIdRef.current)
          );
          currentReplyIdRef.current = null;
        }
      },
      onToolStart: () => {
        setPhase("thinking");
      },
      onToolResult: () => {
        setPhase("thinking");
      },
      onAudio: (base64) => {
        if (!isMuted) {
          setPhase("speaking");
          queueWavBase64(base64);
        }
      },
      onConfirmRequest: (req) => {
        setPendingConfirm(req);
      },
      onConfirmResult: () => {
        setPendingConfirm(null);
      },
      onQuestion: (q) => {
        setPendingQuestion(q);
      },
      onQuestionResult: () => {
        setPendingQuestion(null);
      },
      onTurn: (res: TurnResult) => {
        currentReplyIdRef.current = null;
        setPendingConfirm(null);
        setPendingQuestion(null);

        if (res.reply) {
          setMessages((prev) => {
            const existingIdx = prev.findIndex(
              (m) => m.role === "jarvis" && m.streaming
            );
            if (existingIdx >= 0) {
              const updated = [...prev];
              updated[existingIdx] = {
                ...updated[existingIdx],
                text: res.reply,
                streaming: false,
                tools: res.tools,
              };
              return updated;
            }
            return [
              ...prev,
              {
                id: nextMessageId(),
                role: "jarvis",
                text: res.reply,
                tools: res.tools,
              },
            ];
          });
        }

        if (res.audio_b64 && !isMuted) {
          setPhase("speaking");
          queueWavBase64(res.audio_b64);
        } else if (!isSpeaking()) {
          setPhase("idle");
        }
      },
      onError: (err) => {
        setPhase("idle");
        setMessages((prev) => [
          ...prev,
          { id: nextMessageId(), role: "system", text: `Error: ${err}` },
        ]);
      },
    });

    socket.connect();
    socketRef.current = socket;

    return () => {
      // Keep socket persistent
    };
  }, [isMuted]);

  // Wake word toggle listener
  useEffect(() => {
    if (wakeEnabled) {
      const wsUrl = GATEWAY_URL.replace(/^http/, "ws") + "/ws/wake";
      const wakeWs = new WebSocket(wsUrl);
      wakeWs.binaryType = "arraybuffer";
      wakeWsRef.current = wakeWs;

      wakeWs.onmessage = (event) => {
        try {
          const data = JSON.parse(String(event.data));
          if (data.type === "detected" || data.detected) {
            playActivationChime();
            void startMic();
          }
        } catch {
          // ignore
        }
      };

      const stream = new WakeStream({
        onFrame: (pcm16) => {
          if (wakeWs.readyState === WebSocket.OPEN) {
            wakeWs.send(pcm16);
          }
        },
        onSilence: () => {},
        onClapDetected: () => {
          playActivationChime();
          void startMic();
        },
      });

      stream.start().catch((err) => {
        console.error("Wake word failed to initialize:", err);
        setWakeEnabled(false);
      });
      wakeStreamRef.current = stream;
    } else {
      if (wakeStreamRef.current) {
        wakeStreamRef.current.stop();
        wakeStreamRef.current = null;
      }
      if (wakeWsRef.current) {
        wakeWsRef.current.close();
        wakeWsRef.current = null;
      }
    }
  }, [wakeEnabled, startMic]);

  // Spacebar toggle to talk (when not typing in an input element)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isInput =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if (e.code === "Space" && !isInput && !e.repeat) {
        e.preventDefault();
        if (phase === "listening") {
          void stopMic();
        } else {
          void startMic();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [phase, startMic, stopMic]);

  // Send text turn
  const handleSendText = useCallback(
    (textToSend?: string) => {
      const text = (textToSend ?? draft).trim();
      if (!text || !socketRef.current) return;

      void unlockAudioPlayback();
      stopPlayback();
      setPhase("thinking");

      setMessages((prev) => [
        ...prev,
        { id: nextMessageId(), role: "user", text },
      ]);

      socketRef.current.speak(text);
      setDraft("");
    },
    [draft]
  );

  // Confirm / Question responses
  const handleAnswerConfirm = useCallback((id: string, approved: boolean) => {
    if (socketRef.current) {
      socketRef.current.answer(id, approved);
      setPendingConfirm(null);
    }
  }, []);

  const handleAnswerQuestion = useCallback((id: string, answer: string) => {
    if (socketRef.current) {
      socketRef.current.answerQuestion(id, answer);
      setPendingQuestion(null);
    }
  }, []);

  // Clear conversation history
  const handleClearHistory = useCallback(() => {
    setMessages([]);
    stopPlayback();
    setPhase("idle");
  }, []);

  return (
    <div className="jarvis-app-container">
      {/* Sleek Top Header */}
      <MinimalHeader
        connection={connection}
        health={health}
        wakeEnabled={wakeEnabled}
        onToggleWake={() => setWakeEnabled((prev) => !prev)}
        isMuted={isMuted}
        onToggleMute={() => {
          if (!isMuted) stopPlayback();
          setIsMuted((prev) => !prev);
        }}
        onClearHistory={handleClearHistory}
      />

      {/* Main Experience Body */}
      <main className="jarvis-main-body">
        {/* Central Responsive Glowing Orb */}
        <section className="orb-centerpiece-section">
          <MinimalOrb
            phase={phase}
            onClick={() => {
              if (phase === "listening") {
                void stopMic();
              } else {
                void startMic();
              }
            }}
          />
        </section>

        {/* Clean Conversation Stream */}
        <section className="feed-container-section">
          <ConversationFeed
            messages={messages}
            pendingConfirm={pendingConfirm}
            onAnswerConfirm={handleAnswerConfirm}
            pendingQuestion={pendingQuestion}
            onAnswerQuestion={handleAnswerQuestion}
            onSuggestionClick={(prompt) => handleSendText(prompt)}
          />
        </section>
      </main>

      {/* Bottom Text & Voice Control Bar */}
      <MinimalControls
        phase={phase}
        draft={draft}
        onDraftChange={setDraft}
        onSend={() => handleSendText()}
        onToggleMic={() => {
          if (phase === "listening") {
            void stopMic();
          } else {
            void startMic();
          }
        }}
        onStopSpeech={() => {
          stopPlayback();
          setPhase("idle");
        }}
      />
    </div>
  );
}

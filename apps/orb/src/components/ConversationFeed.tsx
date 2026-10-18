import React, { useEffect, useRef } from "react";
import type { ConfirmRequest, QuestionRequest, ToolAttempt } from "../lib/gateway";

export interface MessageItem {
  id: number;
  role: "user" | "jarvis" | "system";
  text: string;
  meta?: string;
  tools?: ToolAttempt[];
  streaming?: boolean;
}

interface ConversationFeedProps {
  messages: MessageItem[];
  pendingConfirm: ConfirmRequest | null;
  onAnswerConfirm: (id: string, approved: boolean) => void;
  pendingQuestion: QuestionRequest | null;
  onAnswerQuestion: (id: string, answer: string) => void;
  onSuggestionClick: (prompt: string) => void;
}

export const ConversationFeed: React.FC<ConversationFeedProps> = ({
  messages,
  pendingConfirm,
  onAnswerConfirm,
  pendingQuestion,
  onAnswerQuestion,
  onSuggestionClick,
}) => {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new message
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, pendingConfirm, pendingQuestion]);

  const defaultSuggestions = [
    "Open YouTube",
    "Play lofi music on YouTube",
    "Open Spotify",
    "What's the system status?",
    "Lock workstation",
    "List available tools",
  ];

  return (
    <div className="conversation-feed" ref={scrollRef}>
      {messages.length === 0 ? (
        <div className="feed-empty-state">
          <div className="empty-state-greeting">
            <h3>How can I assist you, sir?</h3>
            <p>Speak naturally, press Space to talk, or try one of the quick commands below:</p>
          </div>
          <div className="suggestion-chips">
            {defaultSuggestions.map((prompt) => (
              <button
                key={prompt}
                type="button"
                className="suggestion-chip"
                onClick={() => onSuggestionClick(prompt)}
              >
                <span className="material-symbols-outlined chip-icon">arrow_forward</span>
                {prompt}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="messages-list">
          {messages.map((msg) => (
            <div key={msg.id} className={`message-row role-${msg.role}`}>
              <div className="message-bubble">
                <div className="message-header">
                  <span className="sender-tag">
                    {msg.role === "user" ? "You" : msg.role === "jarvis" ? "J.A.R.V.I.S." : "System"}
                  </span>
                  {msg.meta && <span className="message-meta">{msg.meta}</span>}
                </div>

                <div className="message-body">
                  <p>{msg.text}</p>
                  {msg.streaming && <span className="streaming-cursor" />}
                </div>

                {/* Tool calls execution pills */}
                {msg.tools && msg.tools.length > 0 && (
                  <div className="tool-badges-list">
                    {msg.tools.map((t, idx) => (
                      <div
                        key={`${t.name}-${idx}`}
                        className={`tool-badge ${t.ok ? "tool-ok" : "tool-fail"}`}
                        title={JSON.stringify(t.args, null, 2)}
                      >
                        <span className="material-symbols-outlined badge-icon">
                          {t.ok ? "check_circle" : "error"}
                        </span>
                        <span className="tool-name">{t.name}</span>
                        {t.duration_ms > 0 && (
                          <span className="tool-duration">{t.duration_ms}ms</span>
                        )}
                        {t.summary && <span className="tool-summary">&mdash; {t.summary}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Pending Confirmation Modal / Banner */}
      {pendingConfirm && (
        <div className="approval-card">
          <div className="approval-header">
            <span className="material-symbols-outlined approval-icon">security</span>
            <div className="approval-title-box">
              <h4>Permission Required</h4>
              <span className="approval-countdown">
                Auto-rejecting in {pendingConfirm.seconds_left}s
              </span>
            </div>
          </div>

          <div className="approval-content">
            <p className="approval-summary">{pendingConfirm.summary}</p>
            <div className="approval-tool-info">
              <code>{pendingConfirm.tool}</code>
              <pre className="approval-args">
                {JSON.stringify(pendingConfirm.args, null, 2)}
              </pre>
            </div>
          </div>

          <div className="approval-actions">
            <button
              type="button"
              className="btn-approve"
              onClick={() => onAnswerConfirm(pendingConfirm.id, true)}
            >
              <span className="material-symbols-outlined">check</span>
              Approve Execution
            </button>
            <button
              type="button"
              className="btn-decline"
              onClick={() => onAnswerConfirm(pendingConfirm.id, false)}
            >
              <span className="material-symbols-outlined">close</span>
              Decline
            </button>
          </div>
        </div>
      )}

      {/* Pending Question Modal / Banner */}
      {pendingQuestion && (
        <div className="question-card">
          <div className="question-header">
            <span className="material-symbols-outlined question-icon">help</span>
            <h4>Question from J.A.R.V.I.S.</h4>
          </div>
          <p className="question-text">{pendingQuestion.question}</p>
          <div className="question-options">
            {pendingQuestion.options.map((opt) => (
              <button
                key={opt}
                type="button"
                className="question-opt-btn"
                onClick={() => onAnswerQuestion(pendingQuestion.id, opt)}
              >
                {opt}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

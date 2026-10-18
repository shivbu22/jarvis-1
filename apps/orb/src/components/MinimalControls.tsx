import React, { useRef } from "react";
import type { OrbPhase } from "./MinimalOrb";

interface MinimalControlsProps {
  phase: OrbPhase;
  draft: string;
  onDraftChange: (text: string) => void;
  onSend: () => void;
  onToggleMic: () => void;
  onStopSpeech: () => void;
  disabled?: boolean;
}

export const MinimalControls: React.FC<MinimalControlsProps> = ({
  phase,
  draft,
  onDraftChange,
  onSend,
  onToggleMic,
  onStopSpeech,
  disabled = false,
}) => {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim() || disabled) return;
    onSend();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <footer className="minimal-controls">
      <form className="controls-form" onSubmit={handleSubmit}>
        <button
          type="button"
          className={`mic-toggle-btn ${phase === "listening" ? "mic-active" : ""}`}
          onClick={onToggleMic}
          disabled={disabled}
          title={phase === "listening" ? "Stop listening" : "Start speaking"}
        >
          <span className="material-symbols-outlined">
            {phase === "listening" ? "mic" : "mic_none"}
          </span>
        </button>

        <input
          ref={inputRef}
          type="text"
          className="chat-text-input"
          placeholder="Ask J.A.R.V.I.S. anything or give a command (or speak)..."
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
        />

        {phase === "speaking" ? (
          <button
            type="button"
            className="stop-speech-btn"
            onClick={onStopSpeech}
            title="Stop audio playback"
          >
            <span className="material-symbols-outlined">stop_circle</span>
            <span className="btn-label">Stop</span>
          </button>
        ) : (
          <button
            type="submit"
            className="send-message-btn"
            disabled={!draft.trim() || disabled}
            title="Send command"
          >
            <span className="material-symbols-outlined">send</span>
          </button>
        )}
      </form>
    </footer>
  );
};

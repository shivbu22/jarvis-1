import React from "react";
import type { ConnectionState, Health } from "../lib/gateway";

interface MinimalHeaderProps {
  connection: ConnectionState;
  health: Health | null;
  wakeEnabled: boolean;
  onToggleWake: () => void;
  isMuted: boolean;
  onToggleMute: () => void;
  onClearHistory: () => void;
}

export const MinimalHeader: React.FC<MinimalHeaderProps> = ({
  connection,
  health,
  wakeEnabled,
  onToggleWake,
  isMuted,
  onToggleMute,
  onClearHistory,
}) => {
  const isOnline = connection === "open";
  const brainName = health?.active_brain ?? "Local Brain";
  const toolCount = health?.tool_count ?? 130;
  const sttModel = health?.stt?.model ?? "base";

  return (
    <header className="minimal-header">
      <div className="header-brand">
        <div className="brand-symbol">
          <span className="brand-dot" />
        </div>
        <div className="brand-info">
          <h1 className="brand-title">J.A.R.V.I.S.</h1>
          <span className="brand-subtitle">AI Assistant</span>
        </div>
      </div>

      <div className="header-badges">
        <div className={`status-pill ${isOnline ? "pill-online" : "pill-connecting"}`}>
          <span className="pill-dot" />
          <span className="pill-text">{isOnline ? "Online" : "Connecting"}</span>
        </div>

        <div className="info-pill" title="Active Reasoning Engine">
          <span className="pill-icon material-symbols-outlined">psychology</span>
          <span className="pill-text">{brainName}</span>
        </div>

        <div className="info-pill" title="Speech Recognition Model">
          <span className="pill-icon material-symbols-outlined">mic</span>
          <span className="pill-text">Whisper {sttModel}</span>
        </div>

        <div className="info-pill" title="Registered Capabilities">
          <span className="pill-icon material-symbols-outlined">bolt</span>
          <span className="pill-text">{toolCount} tools</span>
        </div>
      </div>

      <div className="header-actions">
        <button
          type="button"
          className={`action-btn ${wakeEnabled ? "btn-active" : ""}`}
          onClick={onToggleWake}
          title={wakeEnabled ? "Wake word active ('Hey Jarvis')" : "Enable wake word ('Hey Jarvis')"}
        >
          <span className="material-symbols-outlined">hearing</span>
          <span className="btn-label">Wake Word</span>
        </button>

        <button
          type="button"
          className={`action-btn ${isMuted ? "btn-active" : ""}`}
          onClick={onToggleMute}
          title={isMuted ? "Unmute voice playback" : "Mute voice playback"}
        >
          <span className="material-symbols-outlined">{isMuted ? "volume_off" : "volume_up"}</span>
        </button>

        <button
          type="button"
          className="action-btn"
          onClick={onClearHistory}
          title="Clear conversation"
        >
          <span className="material-symbols-outlined">delete_sweep</span>
        </button>
      </div>
    </header>
  );
};

import React, { useMemo } from "react";

export type OrbPhase = "idle" | "listening" | "thinking" | "speaking";

interface MinimalOrbProps {
  phase: OrbPhase;
  audioLevel?: number; // 0.0 to 1.0
  onClick: () => void;
  disabled?: boolean;
}

export const MinimalOrb: React.FC<MinimalOrbProps> = ({
  phase,
  audioLevel = 0,
  onClick,
  disabled = false,
}) => {
  // Compute dynamic scale based on audio input level when listening or speaking
  const reactiveScale = useMemo(() => {
    if (phase === "listening") {
      return 1 + Math.min(audioLevel * 0.45, 0.45);
    }
    if (phase === "speaking") {
      return 1 + Math.sin(Date.now() / 150) * 0.08;
    }
    return 1;
  }, [phase, audioLevel]);

  const statusText = useMemo(() => {
    switch (phase) {
      case "listening":
        return "Listening...";
      case "thinking":
        return "Processing...";
      case "speaking":
        return "Speaking...";
      default:
        return "Tap or press Space to speak";
    }
  }, [phase]);

  return (
    <div className="minimal-orb-wrapper">
      <button
        type="button"
        className={`minimal-orb-btn phase-${phase}`}
        onClick={onClick}
        disabled={disabled}
        aria-label={statusText}
        title={statusText}
        style={{ transform: `scale(${reactiveScale})` }}
      >
        {/* Background glow auras */}
        <div className="orb-aura aura-outer" />
        <div className="orb-aura aura-middle" />
        <div className="orb-aura aura-inner" />

        {/* Dynamic orbital rings */}
        <div className="orb-ring ring-1" />
        <div className="orb-ring ring-2" />
        <div className="orb-ring ring-3" />

        {/* Central luminous core */}
        <div className="orb-core">
          <div className="orb-nucleus" />
          <div className="orb-highlight" />
        </div>

        {/* Ripple effect during active speech/listen */}
        {(phase === "listening" || phase === "speaking") && (
          <>
            <span className="orb-ripple ripple-1" />
            <span className="orb-ripple ripple-2" />
          </>
        )}
      </button>

      <div className={`orb-status-badge badge-${phase}`}>
        <span className="status-indicator-dot" />
        <span className="status-label">{statusText}</span>
      </div>
    </div>
  );
};

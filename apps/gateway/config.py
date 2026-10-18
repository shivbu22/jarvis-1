"""Central settings for the Jarvis gateway.

Everything is an environment variable with a working default, so a fresh clone
starts with no configuration at all. Values come from, in order of precedence:
real environment variables, then `.env`, then the defaults here.

The defaults are chosen for THIS machine (see README "Environment"): an RTX 3050
Laptop with 4 GB VRAM, so whisper small.en fits on the GPU but Chatterbox does
not -- STT defaults to CUDA, TTS to CPU.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# jarvis/apps/gateway/config.py -> parents[0]=gateway, [1]=apps, [2]=jarvis root
ROOT = Path(__file__).resolve().parents[2]

# whisper decode profiles: beam width and best_of trade latency for accuracy.
# The orb is a live conversation, so `fast` is the default; raise it to
# `balanced` or `accurate` when dictation quality matters more than latency.
STT_PROFILES: dict[str, dict[str, object]] = {
    "fast": {"beam_size": 1, "best_of": 1},
    "balanced": {"beam_size": 2, "best_of": 2},
    "accurate": {"beam_size": 5, "best_of": 5},
}


def _str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return _str(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    """Resolved configuration. Build with :func:`load_settings`."""

    root: Path = ROOT
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    voices_dir: Path = field(default_factory=lambda: ROOT / "voices")
    config_dir: Path = field(default_factory=lambda: ROOT / "config")

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- brain ---
    # auto: Gemini when GEMINI_API_KEY is set, otherwise Ollama (keyless).
    brain_mode: str = "auto"
    offline_mode: bool = False
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    # In-process fallback: borrow Ollama's GGUF and run it inside the gateway, so a
    # stopped or broken Ollama cannot leave Jarvis without a brain.
    local_fallback: bool = True
    gguf_path: str = ""
    local_fallback_ctx: int = 8192
    local_fallback_gpu_layers: int = -1
    # --- local model relay (optional) ---
    # An OpenAI-compatible server on loopback that fronts real models. It gives
    # Jarvis a large brain with native tool calls at cloud latency without spending
    # VRAM; the cost is that the request leaves the machine, so offline mode never
    # builds it. The URL carries the key in its path, which is why it is spoken and
    # logged only as a host (see brain/relay.py).
    relay_url: str = ""
    relay_key: str = ""
    relay_model: str = "claude-haiku-4.5"
    relay_max_tokens: int = 0
    relay_timeout_seconds: float = 60.0

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    brain_timeout_seconds: float = 300.0
    max_tool_iterations: int = 6
    history_turns: int = 12
    ultimate_power: bool = False

    # --- STT ---
    whisper_model: str = "tiny.en"
    stt_device: str = "cuda"  # cuda | cpu  (auto also accepted)
    stt_compute_type: str = "auto"  # auto -> int8_float16 on cuda, int8 on cpu
    stt_profile: str = "fast"
    stt_language: str = "en"
    stt_vad_filter: bool = True
    stt_vad_threshold: float = 0.2  # Highly sensitive VAD threshold for whispers and quiet voice
    stt_initial_prompt: str = (
        "Conversational speech, technical commands, quiet voice, whispered queries."
    )

    # --- TTS ---
    tts_engine: str = "auto"  # auto | edge | chatterbox | sapi
    tts_edge_voice: str = "en-GB-RyanNeural"  # British male neural JARVIS voice
    # The cloned voice needs a ~1.5 GB model download. Allowing that implicitly
    # would mean a spoken reply taking five minutes with no explanation, so it is
    # off by default and enabled by the setup script or explicitly here.
    tts_allow_download: bool = False
    tts_backend: str = "turbo"  # turbo | full
    tts_device: str = "cpu"  # cpu here: 4 GB VRAM is spent on whisper
    tts_exaggeration: float = 0.5
    tts_max_chars: int = 600

    # --- browser ---
    # Drive the browser Windows already has (Edge) instead of downloading
    # Chromium. "none" forces the bundled build.
    browser_channel: str = "msedge"

    # --- mail (optional; absent credentials disable the mail tools with a hint) ---
    mail_address: str = ""
    mail_app_password: str = ""
    mail_from_name: str = ""
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465

    # --- wake word ---
    wake_enabled: bool = True
    wake_model: str = "hey_jarvis"
    wake_threshold: float = 0.35  # Sensitive wake word detection for soft speech and whispers
    wake_inference_framework: str = "onnx"

    # --- safety ---
    confirm_timeout_seconds: float = 30.0
    # Longer than the permission window, because a question expects words rather
    # than a verdict they are already looking at. Deliberately not much longer:
    # a turn is *suspended* for this whole window, and a small local model does
    # sometimes ask something it did not need to ask (measured: a greeting became
    # a 140-second turn at 120 s). Bounded, that mistake costs seconds.
    question_timeout_seconds: float = 45.0
    policy_path: Path | None = None
    audit_enabled: bool = True

    # --- MCP ---
    mcp_config_path: Path | None = None
    mcp_enabled: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jarvis.db"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def voice_ref(self) -> Path:
        """Reference WAV for the cloned voice; the file itself is gitignored."""
        return self.voices_dir / "jarvis-5s.wav"

    @property
    def voice_conds(self) -> Path:
        return self.voices_dir / "jarvis-conds.json"

    def resolved_policy_path(self) -> Path:
        return self.policy_path or (self.config_dir / "policy.yaml")

    def resolved_mcp_config_path(self) -> Path:
        return self.mcp_config_path or (self.config_dir / "mcp_servers.yaml")


def load_settings() -> Settings:
    """Read `.env` (without clobbering real env vars) and build :class:`Settings`."""
    load_dotenv(ROOT / ".env", override=False)

    data_dir = Path(_str("JARVIS_DATA_DIR", str(ROOT / "data"))).expanduser()
    voices_dir = Path(_str("JARVIS_VOICES_DIR", str(ROOT / "voices"))).expanduser()
    config_dir = Path(_str("JARVIS_CONFIG_DIR", str(ROOT / "config"))).expanduser()

    offline = _bool("JARVIS_OFFLINE", False)

    settings = Settings(
        root=ROOT,
        data_dir=data_dir,
        voices_dir=voices_dir,
        config_dir=config_dir,
        host=_str("JARVIS_HOST", "127.0.0.1"),
        port=_int("JARVIS_PORT", 8000),
        brain_mode=_str("JARVIS_BRAIN", "ollama" if offline else "auto").lower(),
        offline_mode=offline,
        ollama_host=_str("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        # The default is llama3.2:3b (tuned to jarvis-brain:3b): measured on this
        # machine it answers in 1-4s per model call where qwen3:4b takes 24-28s,
        # because qwen3 reasons at length before it speaks. Set OLLAMA_MODEL=qwen3:4b
        # for the slower, deeper brain -- it is mapped to its own tuned variant too.
        ollama_model=_str("OLLAMA_MODEL", "llama3.2:3b"),
        # Last-resort in-process brain. It borrows Ollama's already-downloaded GGUF
        # and runs it inside the gateway, so a broken or stopped Ollama cannot leave
        # Jarvis mute. Needs llama-cpp-python; harmless (and skipped) without it.
        local_fallback=_bool("JARVIS_LOCAL_FALLBACK", True),
        gguf_path=_str("JARVIS_GGUF_PATH", ""),
        local_fallback_ctx=_int("JARVIS_FALLBACK_CTX", 8192),
        local_fallback_gpu_layers=_int("JARVIS_FALLBACK_GPU_LAYERS", -1),
        # When the tuned local brain is present (see config/ollama/Modelfile.jarvis),
        # OllamaBrain.resolve_model() picks ``jarvis-brain:4b`` automatically; leave
        # this as the stock name so ``ollama pull qwen3:4b`` is the single command a
        # new machine needs, and the tuned variant is an optional improvement.
        relay_url=_str("JARVIS_RELAY_URL", "").rstrip("/"),
        relay_key=_str("JARVIS_RELAY_KEY", ""),
        relay_model=_str("JARVIS_RELAY_MODEL", "claude-haiku-4.5"),
        relay_max_tokens=_int("JARVIS_RELAY_MAX_TOKENS", 0),
        relay_timeout_seconds=_float("JARVIS_RELAY_TIMEOUT_SECONDS", 60.0),
        gemini_api_key=_str("GEMINI_API_KEY", ""),
        gemini_model=_str("GEMINI_MODEL", "gemini-2.5-flash"),
        groq_api_key=_str("GROQ_API_KEY", ""),
        groq_model=_str("GROQ_MODEL", "llama-3.3-70b-versatile"),
        # Generous on purpose. A local brain generates at roughly a dozen tokens a
        # second here, and the *first* turn after a start also pays for loading a
        # 2.5 GB model -- measured, that combination overran a 120-second default and
        # failed a healthy turn. A cloud brain answers far inside this.
        brain_timeout_seconds=_float("BRAIN_TIMEOUT_SECONDS", 300.0),
        max_tool_iterations=_int("MAX_TOOL_ITERATIONS", 6),
        history_turns=_int("HISTORY_TURNS", 12),
        ultimate_power=_bool("JARVIS_ULTIMATE_POWER", False),
        whisper_model=_str("WHISPER_MODEL", "tiny.en"),
        stt_device=_str("STT_DEVICE", "cuda"),
        stt_compute_type=_str("STT_COMPUTE_TYPE", "auto"),
        stt_profile=_str("STT_PROFILE", "fast"),
        stt_language=_str("STT_LANGUAGE", "en"),
        stt_vad_filter=_bool("STT_VAD_FILTER", True),
        stt_vad_threshold=_float("STT_VAD_THRESHOLD", 0.2),
        stt_initial_prompt=_str(
            "STT_INITIAL_PROMPT",
            "Conversational speech, technical commands, quiet voice, whispered queries.",
        ),
        browser_channel=_str("BROWSER_CHANNEL", "msedge"),
        mail_address=_str("MAIL_ADDRESS", ""),
        mail_app_password=_str("MAIL_APP_PASSWORD", ""),
        mail_from_name=_str("MAIL_FROM_NAME", ""),
        imap_host=_str("IMAP_HOST", ""),
        imap_port=_int("IMAP_PORT", 993),
        smtp_host=_str("SMTP_HOST", ""),
        smtp_port=_int("SMTP_PORT", 465),
        tts_engine=_str("TTS_ENGINE", "auto").lower(),
        tts_edge_voice=_str("TTS_EDGE_VOICE", "en-GB-RyanNeural"),
        tts_allow_download=_bool("TTS_ALLOW_DOWNLOAD", False),
        tts_backend=_str("TTS_BACKEND", "turbo").lower(),
        tts_device=_str("TTS_DEVICE", "cpu"),
        tts_exaggeration=_float("TTS_EXAGGERATION", 0.5),
        tts_max_chars=_int("TTS_MAX_CHARS", 600),
        wake_enabled=_bool("WAKE_ENABLED", True),
        wake_model=_str("WAKE_MODEL", "hey_jarvis"),
        wake_threshold=_float("WAKE_THRESHOLD", 0.5),
        wake_inference_framework=_str("WAKE_INFERENCE_FRAMEWORK", "onnx"),
        confirm_timeout_seconds=_float("CONFIRM_TIMEOUT_SECONDS", 30.0),
        question_timeout_seconds=_float("QUESTION_TIMEOUT_SECONDS", 45.0),
        policy_path=Path(_str("POLICY_PATH", str(config_dir / "policy.yaml"))),
        audit_enabled=_bool("AUDIT_ENABLED", True),
        mcp_config_path=Path(_str("MCP_CONFIG_PATH", str(config_dir / "mcp_servers.yaml"))),
        mcp_enabled=_bool("MCP_ENABLED", True),
    )

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.voices_dir.mkdir(parents=True, exist_ok=True)
    return settings


def stt_decode_options(profile: str) -> dict[str, object]:
    """Map a profile name to whisper decode kwargs, defaulting to `fast`."""
    return dict(STT_PROFILES.get(profile, STT_PROFILES["fast"]))

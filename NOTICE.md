# Attribution

This project is a substantial rework, not a fork. It was designed from two
existing voice-agent projects and shares their architecture where that
architecture is good, while replacing the parts that would not have been safe or
workable here. Nothing was copied wholesale; where a specific setting or contract
came from an upstream repository it is noted below.

## Horizon733/Voice-mcp-agent

- Repository: https://github.com/Horizon733/Voice-mcp-agent
- Taken: the overall pipeline shape (STT → LLM with tools → cloned TTS), the
  gateway's HTTP contract (`/health`, `/stt`, `/tts`, `/v1/turn`,
  `/v1/turn/text`, `WS /ws/turn`), the STT tuning knobs and their profile idea
  (`WHISPER_MODEL`, `STT_DEVICE`, `STT_PROFILE`, `STT_COMPUTE_TYPE`,
  `STT_VAD_FILTER`), the `hey_jarvis` wake word via openWakeWord on ONNX, the
  "no-HTTP CLI mode" convenience, and the orb-UI concept.
- Changed: Gemini-only brain replaced with a swappable, failing-over provider
  chain; tools moved behind an MCP-capable registry with tiers; a confirmation
  broker, audit log and policy gate added; TTS made to degrade to the OS voice
  instead of failing; the model download made explicit rather than implicit.
- Dependencies mirrored deliberately: `faster-whisper`, `chatterbox-tts`,
  `openwakeword`, `google-genai`, `mcp`, `duckduckgo-search`, and the
  `setuptools>=70,<81` pin (Chatterbox's `perth` dependency still uses
  `pkg_resources`, removed in setuptools 81).

## den-vasyliev/voice-mcp-agent (also published as VoiceOps)

- Repository: https://github.com/den-vasyliev/voice-mcp-agent
- Licence: MIT
- Taken: the idea of a YAML-configured MCP client with per-server `allowed_tools`
  globs and per-server auth read from an environment variable; the
  warn-before-destructive posture.
- Changed: that project supports HTTP/SSE MCP servers only, so stdio support was
  added here (most local MCP servers are stdio subprocesses); its LiveKit /
  OpenAI / ElevenLabs dependency and Kubernetes-specific toolset were dropped in
  favour of local speech and general-purpose tools.
- Auth: the upstream HMAC-SHA256 signing scheme is specific to its own servers, so
  this project implements generic header-based auth instead and documents how to
  extend it. No cryptographic scheme was copied.

## Optional integrations

- **Ollama** — https://github.com/ollama/ollama — reached over its
  OpenAI-compatible API. Not bundled.
- **Chatterbox TTS** (Resemble AI) — https://github.com/resemble-ai/chatterbox —
  installed from PyPI as `chatterbox-tts`; its model weights are downloaded from
  Hugging Face on request only.
- **openWakeWord** — https://github.com/dscripka/openWakeWord — pretrained
  `hey_jarvis` model.
- **faster-whisper** — https://github.com/SYSTRAN/faster-whisper — CTranslate2
  implementation of OpenAI's Whisper.
- **Open-Meteo** — https://open-meteo.com — weather and geocoding, no API key.
- **DuckDuckGo** — search and news, no API key.
- **Playwright** — https://playwright.dev — browser control, preferring the Edge
  already installed on the machine over a bundled Chromium download.
- **Model Context Protocol SDK** — https://github.com/modelcontextprotocol/python-sdk
  — client and server.

## Voice reference clips

`voices/jarvis-5s.wav` is **not** included, and no voice model or celebrity
likeness is distributed with this project. If you supply a reference clip, make
sure you have the right to use that voice. The default configuration speaks with
whatever voice you provide, or with the Windows system voice.

## This project

MIT — see `LICENSE`.

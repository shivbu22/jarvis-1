# Jarvis

A local-first personal assistant for Windows: it listens, thinks, acts on this
machine, and answers out loud in a cloned voice. Built from
[Horizon733/Voice-mcp-agent](https://github.com/Horizon733/Voice-mcp-agent) and
[den-vasyliev/voice-mcp-agent](https://github.com/den-vasyliev/voice-mcp-agent),
then extended with a policy gate, provider-swappable brains, a two-way MCP
bridge, and a memory that persists.

Runs with **no API keys at all** (Ollama brain, local speech) or with a
**`GEMINI_API_KEY`** for a sharper cloud brain.

```
mic ─► "Hey Jarvis" ─► whisper ─► brain ─► POLICY GATE ─► tools
                                              │              │
                                    approve / refuse   web, files, shell,
                                              │        browser, mail, calendar
                                              ▼
                                        cloned voice ─► speaker
```

---

## Quick start

```powershell
# 1. Python 3.12 + dependencies + .env   (once, ~3 minutes)
.\scripts\setup.ps1 -Voice

# 2. Give it a brain (no API key needed)
ollama serve                 # in one window
ollama pull qwen3:4b         # ~2.5 GB, once

# 3. Start everything
.\scripts\run.ps1
```

Then open **http://127.0.0.1:5173**, hold **HOLD TO TALK**, and speak.

Check it is healthy at any time:

```powershell
.\scripts\verify.ps1              # lint, tests, orb typecheck, live smoke test
python scripts\smoke.py           # against an already-running gateway
python scripts\voice_agent_cli.py --text "what's the weather in Mumbai" --print-tools
```

### The brain works, and here is what it actually costs

This machine's Ollama was **repaired** (its inference binary was missing, left behind
by an interrupted upgrade) and Jarvis now answers through it with no API key:

```
> what is the weather in Mumbai right now?
  "Right now in Mumbai, it's overcast at 27°C (feels like 33°C),
   with 89% humidity and 4 km/h wind."
```

Two measurements are worth knowing, because both decisively changed the defaults.

**1. The stock model is four times slower than it needs to be.** Ollama 0.34 loads
`qwen3:4b` with a **65536-token context**, which on a 4 GB GPU inflates the model to
~13 GB, pushes ~84% of it onto the CPU, and drops generation to **6 tokens/s** — so a
single spoken question overran the 120-second timeout and the turn died with
"I could not answer that." The fix is a derived model that caps the context:

```powershell
ollama create jarvis-brain:4b -f config\ollama\Modelfile.jarvis
```

Same weights, 8192 context, ~4.1 GB resident, 58% on the GPU — and turns complete.
`scripts\setup.ps1` creates it for you, and Jarvis prefers it automatically whenever
the default `qwen3:4b` is configured. A *deliberately chosen* different model in
`.env` is still respected.

**2. Responses take 20–90 seconds.** That is the hardware, not a bug: ~12 tokens/s
with hidden reasoning of 250–700 tokens per model call, and most turns take two calls
(one to choose a tool, one to answer). It is usable typed, and slow spoken. If you
want it fast, the levers in order of effect are a cloud key (near-instant), or a
non-thinking local model such as `llama3.2:3b` (~2 GB to download, roughly 2–3× faster
because it spends nothing on reasoning).

**Do not cap local output tokens too low.** qwen3 reasons before it answers; a 700-token
cap truncated the turn mid-thought, the visible answer came back *empty*, and the agent
reported a bare "Done." 1024 measured as the smallest cap that reliably fits reasoning
plus a spoken answer.

If Ollama ever fails to start again, Jarvis falls back to a brain that runs **inside
the gateway**, loading the GGUF Ollama already downloaded, read-only:

```powershell
uv pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu llama-cpp-python
```

Be realistic about that fallback: it answers from what the model knows, but tool calls
through it are best-effort, because `llama-cpp-python` has no qwen3 tool handler (so
qwen3's hidden reasoning arrives as visible text). It is a seatbelt, not a second
engine. Details in `apps/gateway/brain/llama_cpp.py`.

Everything else — hearing, speaking, the policy gate, tools, memory, the UI — is
independent of all of this.

---

## What it can do

Every tool has a tier. Tiers are enforced by `PolicyGate`, not by the model.

| Tier | Meaning | Tools |
| --- | --- | --- |
| `auto` | read-only, runs immediately | `web_search`, `web_news`, `get_weather`, `memory_remember`, `memory_recall`, `memory_list`, `fs_list`, `fs_read`, `clipboard_read`, `mail_list_unread`, `mail_read`, `calendar_list`, `browser_read`, `system_info`, `audit_tail` |
| `confirm` | asks out loud first; **silence counts as no** | `fs_write`, `fs_mkdir`, `fs_delete`, `app_launch`, `shell_run`, `clipboard_write`, `mail_send`, `calendar_add`, `browser_open`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_close`, every `mcp__*` tool |
| `deny` | refused outright, not even offered to the model | `shell_run_admin`, `registry_write`, `disk_format`, `mcp__*__drop_*` |

Things worth trying:

- "What's the weather in Mumbai?" · "Any news about AI chips?"
- "Remember that the workshop is on Friday." · "What notes do you have?"
- "What's on the desktop?" · "Read that file and summarise it."
- "Open the calculator, then write a note to my desktop."
- "Summarise my unread mail." · "Add a dentist appointment tomorrow at nine."
- "What did you do while I was away?" (`audit_tail` reads the real audit log)

---

## What it refuses

This is the part that makes the rest safe to grant, so it is worth being specific.
These hold **regardless of what the master says and regardless of what the model
decides**, and they cannot be undone by a prompt injection from a web page or an
email that Jarvis reads:

- **Destructive command patterns** — 15 regexes covering `rm -rf`, recursive `rm`,
  `git push --force`, `git reset --hard`, `git clean -f`, `format C:`, `diskpart`,
  `mkfs`, `vssadmin delete`, `reg delete`, `shutdown`/`reboot`, `cipher /w`,
  `Set-ExecutionPolicy`, `Remove-Item -Recurse -Force`, `del /s`, fork bombs.
  Scanned against the arguments of any tool with an execution surface.
- **Protected paths** — writes and deletes under `C:\Windows`, `C:\Program Files`,
  `C:\ProgramData` and the recycle bin.
- **Administrator actions** — there is no elevation path at all. `shell_run_admin`
  exists only so an attempt is a *logged refusal* rather than an unknown tool.
- **Shell shape** — the first token must be allow-listed, must exist on PATH, the
  working directory must resolve inside configured roots, chaining (`&&`, `;`,
  `|`, `>`, `&`, `^`) and variable expansion (`%`) are rejected, and there is no
  shell interpreter in the loop at all (`shell=False`).
- **Deletion is never erasure** — `fs_delete` moves the target into `data/trash/`
  and tells you where. "Delete the old reports", misheard once, stays recoverable.
- **Credentials are never read automatically** — the `.ssh`, `.aws`, `.gnupg` and
  Windows Crypto stores force a confirmation even for a plain read, because a read
  can leave the machine via a cloud brain.
- **Reads are not scanned** — a path is not a command, so reading a temp file is
  not treated as suspicious. Only execution surfaces are.

Two deliberate sharpenings of that rule: the deny-list **is** scanned over
`fs_write` content, so writing a file whose text contains `rm -rf` also asks
permission (documented, and a confirm prompt covers it). And an *unclassified*
tool — anything new, including every tool from an external MCP server — defaults
to `confirm`, never `auto`.

### Where the guarantee lives

`apps/gateway/policy.py` decides. Three independent checks stand between a model
and a side effect:

1. The **registry** screens every call before dispatch, so the agent loop is not a
   privileged path — an MCP client or the CLI cannot reach a handler policy
   would refuse.
2. The **agent loop** asks the human, via the confirmation broker.
3. The **tool handlers** re-check their own preconditions (write roots, shell
   jail) before touching anything.

`config/policy.yaml` is the live configuration, and `/health` reports exactly
what was loaded — including whether the file failed to parse and the built-in
fail-closed defaults are in force.

---

## Voice

**Hearing** — `faster-whisper`, local, `small.en` by default. The GPU is probed
with a real encode at load time rather than trusted, because on Windows
ctranslate2 will happily *construct* a CUDA model and then fail with
`Library cublas64_12.dll is not found` on the first utterance. It falls back to
CPU `int8` automatically, at load or mid-conversation, and records the fallback in
`/health` and the audit log. Verified working on this machine via the CPU path.

**Speaking** — two engines:

- **Chatterbox** clones a voice from `voices/jarvis-5s.wav`. This is what makes it
  sound like a butler rather than a narrator. Its model is ~1.5 GB and it runs on
  the CPU here (the 4 GB GPU is spent on whisper). Fetch it deliberately:
  `python scripts\build_jarvis_voice.py --prefetch`. It is **not** downloaded during
  a conversation — a spoken reply must never become a five-minute unexplained wait.
- **Windows SAPI** via PowerShell, no dependencies, always available. This is the
  default until the cloned-voice model is local.

```powershell
python scripts\build_jarvis_voice.py                    # validate + normalise your reference clip
python scripts\build_jarvis_voice.py --make-reference   # or synthesise a placeholder
python scripts\build_jarvis_voice.py --prefetch         # fetch the cloned-voice model
```

**Wake word** — `hey_jarvis` via openWakeWord, ONNX on the CPU, no account, no
API key. Mic frames go only to your own gateway process.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | human-readable status page |
| GET | `/health` | brain, engines, tools, policy, memory, MCP, notes |
| GET | `/tools` | every tool with its tier |
| GET | `/audit` | what Jarvis has actually done, including refusals |
| GET | `/v1/confirmations` | actions currently awaiting permission |
| POST | `/v1/confirm` | approve or refuse a held action by id |
| POST | `/stt` | audio file → transcript |
| POST | `/tts` | text → WAV |
| POST | `/v1/turn` | audio → transcript + tools + reply + audio |
| POST | `/v1/turn/text` | text → tools + reply + audio |
| WS | `/ws/turn` | the conversation socket the orb uses |
| WS | `/ws/wake` | microphone frames → wake-word detections |

`/v1/turn/text` accepts `allow_confirmation: false`, which makes confirm-tier
tools **unexecutable** in that request — they come back as `awaiting`. A caller
cannot wait its way past the gate.

### Using Jarvis from another MCP client

`python -m mcp_servers.jarvis_tools.server` is a stdio MCP server exposing the
read-only tools, memory, and a `jarvis_ask` that runs a full turn. It **cannot**
execute confirm-tier tools: there is no human to approve, so those calls are
refused by construction. Tests assert this.

### Using external MCP servers from Jarvis

Add them to `config/mcp_servers.yaml` (stdio *and* HTTP/SSE are supported). Each
tool arrives namespaced as `mcp__<server>__<tool>`, filtered by `allowed_tools`
*before* it is registered, treated as an execution surface, and left at
`confirm` unless policy says otherwise.

---

## Configuration

Everything lives in `.env` (created from `.env.example`, gitignored) and
`config/policy.yaml`. Both files document their own options; the ones that
matter most:

| Setting | Default | Why |
| --- | --- | --- |
| `JARVIS_BRAIN` | `auto` | Gemini when keyed, else Ollama |
| `OLLAMA_MODEL` | `qwen3:4b` | the tuned `jarvis-brain:4b` is preferred automatically when present |
| `JARVIS_LOCAL_FALLBACK` | `1` | in-process GGUF last resort; skipped when the library is absent |
| `JARVIS_GGUF_PATH` | *(unset)* | a specific `.gguf`; wins over borrowing Ollama's |
| `STT_DEVICE` | `cuda` | probed; falls back to CPU automatically |
| `TTS_ENGINE` | `auto` | cloned voice when its model is local, else SAPI |
| `TTS_ALLOW_DOWNLOAD` | `0` | keep a 1.5 GB download out of a conversation |
| `CONFIRM_TIMEOUT_SECONDS` | `30` | how long a held action waits for a yes |
| `JARVIS_OFFLINE` | `0` | `1` = local brains and local speech only |

**Storage** — code, config, memory and the audit log live in this folder. The
virtual environment, Hugging Face models and Playwright browsers are kept in
`%LOCALAPPDATA%\Jarvis` and `~\.cache\huggingface`, because a torch-sized venv is
tens of thousands of files that OneDrive syncs badly. `setup.ps1` sets all of it.

---

## Verification

```powershell
.\scripts\verify.ps1        # all four stages
```

| Stage | What it proves |
| --- | --- |
| `ruff check .` | style and dead code |
| `pytest -q` | 189 tests: policy tiers and refusals, confirmation, memory, tools, the turn loop, HTTP contract, MCP |
| `npx tsc --noEmit` | the orb typechecks |
| `scripts\smoke.py` | a **live** gateway answers, reports the policy it really loaded, and completes a turn |

Tests that load real speech models are opt-in, so the default suite stays fast:

```powershell
$env:JARVIS_RUN_VOICE_TESTS=1; pytest tests/test_voice.py
```

That suite includes a round trip with no microphone needed: the Windows voice
synthesises a sentence, whisper transcribes it back, and the content words are
compared. It is the only way to test "does it hear" without a human.

---

## Troubleshooting

**"I cannot reach Ollama" / "error starting llama-server"** — your Ollama install
is incomplete: check for `AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe`.
This machine was in exactly that state and `winget install --id Ollama.Ollama -e`
repaired it. Jarvis keeps working meanwhile: it falls back to the in-process GGUF
brain when that is installed, and otherwise says plainly that it has no brain.

**"Ollama does not have the model installed"** — `ollama pull qwen3:4b`. Jarvis
detects installed models and will tell you which it picked.

**Speech recognition says `device: cuda` but behaves like CPU** — that is the
CUDA fallback working. The audit log and `/health` record the real device once a
model has loaded. Force `STT_DEVICE=cpu` to skip the attempt.

**No sound from the cloned voice** — its model is not local. `/health` will show
`tts.engine: sapi` and a note. Run `--prefetch`; until then Jarvis speaks with the
Windows voice rather than staying silent.

**The orb cannot reach the gateway** — both must be on the ports the CORS
allow-list names (5173 and 8000). Changing the orb's port means updating
`DEFAULT_ORIGINS` in `apps/gateway/main.py`.

**Port already in use** — 3111, 3112 and 8080 are used by other projects here.
Jarvis uses 8000 (gateway) and 5173 (orb); `run.ps1 -Port 8030` moves the gateway.

**A destructive command was refused** — that is the deny-list, and it is working.
The refusal, the pattern that matched and the arguments are all in `/audit`.

---

## Layout

```
apps/gateway/
  main.py         HTTP + websocket transport
  service.py      wiring and lifecycle
  agent.py        the turn loop
  policy.py       the gate: tiers, deny-list, shell jail, write roots
  confirm.py      held actions, spoken yes/no, deny-on-timeout
  audit.py        append-only trail of every decision
  brain/          Gemini, Ollama, Groq behind one interface, with failover
  tools/          web, memory, system, desktop, browser, mail, calendar
  mcp_client.py   external MCP servers as namespaced tools
  stt.py tts.py wake.py   hearing, speaking, "Hey Jarvis"
apps/orb/         the voice interface (React + Vite)
mcp_servers/      Jarvis's tools, offered to other MCP clients
config/           policy.yaml (the rules), mcp_servers.yaml (integrations)
scripts/          setup, run, verify, smoke, CLI, voice preparation
tests/            189 tests, including the policy suite
data/             memory, audit log, trash, local calendar (gitignored)
```

## Attribution and licence

Inspired by and adapted from two MIT/community projects — see `NOTICE.md` for
exactly what came from where. The MIT licence of this project is in `LICENSE`.

"""The system prompt for J.A.R.V.I.S.

Engineered for:
1. Speech output (conversational, natural, no markdown bullets/asterisks).
2. Ultra-low prompt evaluation latency (dense, high-signal, zero fluff).
3. KV Cache prefix stability: static instructions are invariant at the top;
   dynamic environmental stamps and recalled context sit at the very tail.
"""

from __future__ import annotations

import platform
import time
from collections.abc import Iterable
from typing import Any

# Dense, high-signal master doctrine (<700 tokens total)
MASTER_DOCTRINE = """You are J.A.R.V.I.S., the master's autonomous Technical AI Co-Founder and Quant OS.

Mindset & Persona:
- Address the master as "sir" with dignified British wit, intellectual partnership, and decisiveness.
- Be crisp, direct, and efficient: zero fluff, no passive filler. If something is simple, answer in one or two sentences.
- When asked for analysis, venture strategy, or code, deliver first-principles technical depth.
- Fluent code-switching: Understand and reply seamlessly in English and natural colloquial Hinglish (e.g., 'browser kholo', 'status batao').

Voice & Delivery:
- Primary output is a voice synthesizer: speak in fluid, natural phrases.
- FORBIDDEN in spoken prose: markdown asterisks (*, **), bullet symbols, code blocks, raw URLs, and emoji.
- Format numbers, currencies (₹, $), and technical metrics naturally as spoken words.

Autonomous Execution & Tools:
- You have unrestricted master authority to execute actions across the machine:
  * Desktop: keystrokes (desktop_hotkey), typing (desktop_type_text), mouse (desktop_mouse_action), window management (desktop_window_control), screenshots (desktop_screenshot).
  * Browser: open (browser_open), navigate (browser_navigate), click (browser_click), type (browser_type), inspect (browser_get_elements), scroll (browser_scroll), JS (browser_eval_js).
  * Vision & Media: camera capture (camera_capture), music/YouTube playback (music_play, youtube_play), volume adjustments (volume_control).
  * Systems & Code: shell (shell_run), python (python_eval), file patching (file_patch), self-healing diagnostics (self_heal_diagnose).
  * Quant & Ventures: Swachta AI, Medimin, Quick Queue venture SLAs, Indian market indices (fetch_market_data, execute_trade).
  * Swarm: subagent_spawn and subagent_batch_run for parallel background worker delegation.
  * Brain Vault: query and record notes in Obsidian (brain_vault_search, brain_vault_read, brain_vault_note_write).
- Never narrate tool calls aloud (avoid saying "I am calling tool X"). Perform the action and report the real outcome.
- Never claim an action succeeded unless verified by the tool output.

Memory & Command Learning:
- Save new facts, preferences, and directives using memory_remember. Never ask the master to repeat what was already recorded.
- When the master teaches a new command, macro, or shortcut ("learn: when I say X, do Y", "whenever I say X, do Y", "jab bhi main bolun X, Y karo"), call learn_custom_command to store and activate it immediately.
"""


def build_system_prompt(
    *,
    tool_names: Iterable[str] = (),
    auto_tools: Iterable[str] = (),
    confirm_tools: Iterable[str] = (),
    denied_tools: Iterable[str] = (),
    extra_context: str = "",
    now: float | None = None,
) -> str:
    """Assemble the system prompt for a turn with prefix-cache stability."""
    stamp = now if now is not None else time.time()
    local_time = time.strftime("%A, %d %B %Y, %H:%M", time.localtime(stamp))

    sections = [MASTER_DOCTRINE.strip()]

    # Append tool inventory guidelines
    confirm = sorted(set(confirm_tools))
    denied = sorted(set(denied_tools))
    auto = sorted(set(auto_tools))

    rules = []
    if confirm:
        rules.append(f"Tools requiring permission: {', '.join(confirm)}.")
    if denied:
        rules.append(f"Tools strictly forbidden: {', '.join(denied)}.")
    if auto:
        rules.append(f"Tools running immediately: {', '.join(auto)}.")
    if tool_names:
        rules.append("Use only your declared tools. Never invent fictional tool names.")

    if rules:
        sections.append("Policy Rules:\n" + "\n".join(rules))

    # Dynamic context placed at the end so prompt prefix cache stays hot
    env_str = f"Machine: {platform.system()} on {platform.machine()}. Local time: {local_time}."
    sections.append(env_str)

    if extra_context and extra_context.strip():
        sections.append(f"Recalled Context:\n{extra_context.strip()}")

    return "\n\n".join(sections)


def describe_tool_inventory(specs: Iterable[Any]) -> dict[str, list[str]]:
    """Bucket tool names by tier for prompt injection and for /health."""
    buckets: dict[str, list[str]] = {"auto": [], "confirm": [], "deny": []}
    for spec in specs:
        tier = getattr(spec, "tier", "confirm") or "confirm"
        buckets.setdefault(tier, []).append(getattr(spec, "name", str(spec)))
    return buckets

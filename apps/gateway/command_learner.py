"""Autonomous Custom Command Learning Engine for J.A.R.V.I.S.

Enables J.A.R.V.I.S. to dynamically learn, modify, and execute custom commands,
voice macros, and colloquial shortcuts spoken in English, Hinglish, or Hindi.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from .memory import MemoryStore

logger = logging.getLogger("jarvis.command_learner")

ROOT = Path(__file__).resolve().parents[2]
VAULT_DIR = ROOT / "jarvis brain"


# Patterns to detect intent to learn or teach a new command
_LEARN_PATTERNS = [
    # 1. Quoted English: learn command: when/whenever I say 'xyz', do abc
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?learn\s*(?:this\s*)?(?:new\s*)?command\s*[:,-]?\s*(?:when|whenever|if)?\s*(?:i\s*say)?\s*['\"]([^'\"]+)['\"]\s*(?:,|means|should|to|then|do)\s*(.+)$",
        re.I,
    ),
    # 1b. Unquoted English: learn command: when/whenever I say xyz, do abc
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?learn\s*(?:this\s*)?(?:new\s*)?command\s*[:,-]?\s*(?:when|whenever|if)?\s*(?:i\s*say)?\s+([^,]+?)\s*(?:,|means|should|to|then|do)\s*(.+)$",
        re.I,
    ),
    # 2. Quoted whenever/when: whenever I say 'xyz', do abc
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:whenever|when)\s*i\s*say\s*['\"]([^'\"]+)['\"]\s*(?:,|then|do|you\s*should|\s*should)\s*(.+)$",
        re.I,
    ),
    # 2b. Unquoted whenever/when: whenever I say xyz, do abc
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:whenever|when)\s*i\s*say\s+([^,]+?)\s*(?:,|then|do|you\s*should|\s*should)\s*(.+)$",
        re.I,
    ),
    # 3. teach/add command: 'xyz' means abc
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:teach|add)\s*(?:new\s*)?command\s*[:,-]?\s*['\"]([^'\"]+)['\"]\s*(?:means|=|->|to)\s*(.+)$",
        re.I,
    ),
    # 4. remember that when/whenever I say 'xyz', abc
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?remember\s*(?:that\s*)?(?:when|whenever)\s*i\s*say\s*['\"]([^'\"]+)['\"]\s*(?:,|then|do|you\s*should)?\s*(.+)$",
        re.I,
    ),
    # 5. Hinglish quoted: naya command seekho: jab bhi main bolun 'xyz', abc karo/chalao
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:naya|new)\s*command\s*(?:seekho|yaad\s*rakho)\s*[:,-]?\s*(?:jab|jab\s*bhi)\s*(?:main\s*)?(?:kahun|bolun|bolu)?\s*['\"]([^'\"]+)['\"]\s*(?:tab|to|,)?\s*(.+?)(?:\s+karo|\s+karna|\s+chalao|\s+kijiye)?$",
        re.I,
    ),
    # 5b. Hinglish unquoted: naya command seekho: jab bhi main bolun xyz, abc karo
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:naya|new)\s*command\s*(?:seekho|yaad\s*rakho)\s*[:,-]?\s*(?:jab|jab\s*bhi)\s*(?:main\s*)?(?:kahun|bolun|bolu)?\s+([^,]+?)\s*(?:tab|to|,)\s+(.+?)(?:\s+karo|\s+karna|\s+chalao|\s+kijiye)?$",
        re.I,
    ),
    # 6. Hinglish quoted direct: jab main kahun 'xyz', tab/to abc karo
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:jab|jab\s*bhi)\s*(?:main\s*)?(?:kahun|bolun|bolu)\s*['\"]([^'\"]+)['\"]\s*(?:tab|to|,)?\s*(.+?)(?:\s+karo|\s+karna|\s+chalao|\s+kijiye)?$",
        re.I,
    ),
    # 6b. Hinglish unquoted direct: jab main kahun xyz, to abc karo
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:jab|jab\s*bhi)\s*(?:main\s*)?(?:kahun|bolun|bolu)\s+([^,]+?)\s*(?:tab|to|,)\s+(.+?)(?:\s+karo|\s+karna|\s+chalao|\s+kijiye)?$",
        re.I,
    ),
    # 7. Hinglish remember: yaad rakhna jab main kahun 'xyz', to abc karo
    re.compile(
        r"^(?:(?:hey\s+)?jarvis[,:\s]*)?yaad\s*rakhna\s*(?:ki\s*)?jab\s*(?:main\s*)?(?:kahun|bolun|bolu)\s*['\"]([^'\"]+)['\"]\s*(?:to|tab|,)?\s*(.+?)(?:\s+karo|\s+karna|\s+chalao)?$",
        re.I,
    ),
    # 8. Devanagari Hindi quoted: नया कमांड सीखो: जब मैं कहूं 'xyz', abc करो
    re.compile(
        r"^(?:जार्विस[,:\s]*)?(?:नया\s*कमांड\s*सीखो|याद\s*रखो)\s*[:,-]?\s*(?:जब|जब\s*भी)\s*(?:मैं\s*)?(?:कहूँ|कहूं|बोलूं|बोलू)?\s*['\"]([^'\"]+)['\"]\s*(?:तब|तो|,)?\s*(.+?)(?:\s+करो|\s+करना|\s+चलाओ)?$",
        re.I,
    ),
    # 8b. Devanagari Hindi unquoted: नया कमांड सीखो: जब मैं कहूं xyz, abc करो
    re.compile(
        r"^(?:जार्विस[,:\s]*)?(?:नया\s*कमांड\s*सीखो|याद\s*रखो)\s*[:,-]?\s*(?:जब|जब\s*भी)\s*(?:मैं\s*)?(?:कहूँ|कहूं|बोलूं|बोलू)?\s+([^,]+?)\s*(?:तब|तो|,)\s+(.+?)(?:\s+करो|\s+करना|\s+चलाओ)?$",
        re.I,
    ),
    # 9. Devanagari Hindi direct quoted: जब मैं कहूं 'xyz', तब/तो abc करो
    re.compile(
        r"^(?:जार्विस[,:\s]*)?(?:जब|जब\s*भी)\s*(?:मैं\s*)?(?:कहूँ|कहूं|बोलूं|बोलू)\s*['\"]([^'\"]+)['\"]\s*(?:तब|तो|,)?\s*(.+?)(?:\s+करो|\s+करना|\s+चलाओ)?$",
        re.I,
    ),
    # 9b. Devanagari Hindi direct unquoted: जब मैं कहूं xyz, तब/तो abc करो
    re.compile(
        r"^(?:जार्विस[,:\s]*)?(?:जब|जब\s*भी)\s*(?:मैं\s*)?(?:कहूँ|कहूं|बोलूं|बोलू)\s+([^,]+?)\s*(?:तब|तो|,)\s+(.+?)(?:\s+करो|\s+करना|\s+चलाओ)?$",
        re.I,
    ),
]


def parse_learning_intent(text: str) -> tuple[str, str, str] | None:
    """Parse spoken or written text for an explicit command learning directive.

    Returns:
        (trigger_phrase, action_payload, description) or None.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    for pattern in _LEARN_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            trigger = match.group(1).strip().strip("'\"")
            action = match.group(2).strip().strip("'\"")
            if trigger and action:
                # Clean up any trailing auxiliary markers
                action = re.sub(r"^(?:do|you\s*should|please)\s+", "", action, flags=re.I).strip()
                return trigger, action, f"Learned from phrase: '{cleaned}'"

    return None


def sync_commands_to_vault(memory: MemoryStore, vault_dir: Path | None = None) -> Path | None:
    """Sync all active learned commands into Obsidian Brain Vault."""
    target_vault = vault_dir or VAULT_DIR
    if not target_vault.exists():
        try:
            target_vault.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None

    commands_folder = target_vault / "commands"
    commands_folder.mkdir(parents=True, exist_ok=True)
    doc_path = commands_folder / "learned_commands.md"

    commands = memory.list_custom_commands(limit=200)

    lines = [
        "---",
        "title: \"J.A.R.V.I.S. Learned Commands & Voice Macros\"",
        f"date: \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"",
        "tags: [jarvis, commands, macros, learning, voice]",
        "---",
        "",
        "# J.A.R.V.I.S. Learned Custom Commands & Macros",
        "",
        "> Live synchronized table of dynamically learned voice commands and user shortcuts.",
        "",
        "| ID | Trigger Phrase | Action Directive | Uses | Last Used | Created |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for cmd in commands:
        cmd_id = cmd.get("id", 0)
        trig = cmd.get("trigger_phrase", "")
        act = cmd.get("action_payload", "")
        uses = cmd.get("use_count", 0)
        last_used_raw = cmd.get("last_used_at")
        last_used_str = (
            time.strftime("%d %b %H:%M", time.localtime(float(last_used_raw)))
            if last_used_raw
            else "Never"
        )
        created_str = time.strftime(
            "%Y-%m-%d", time.localtime(float(cmd.get("created_at", time.time())))
        )
        lines.append(f"| `{cmd_id}` | **\"{trig}\"** | `{act}` | {uses} | {last_used_str} | {created_str} |")

    lines.append("")
    lines.append("## How to Use")
    lines.append("- Speak any trigger phrase (e.g., *\"Jarvis, focus mode\"*) to execute its learned directive.")
    lines.append("- Teach new commands on the fly: *\"Jarvis, whenever I say 'tea break', pause music and open browser\"*.")
    lines.append("")

    try:
        doc_path.write_text("\n".join(lines), encoding="utf-8")
        return doc_path
    except Exception as exc:
        logger.warning("Failed to sync commands to Obsidian vault: %s", exc)
        return None


def learn_command(
    memory: MemoryStore,
    trigger: str,
    action: str,
    description: str = "",
    vault_dir: Path | None = None,
) -> dict[str, Any]:
    """Commit a learned custom command into memory, search index, and Obsidian vault."""
    clean_trigger = trigger.strip().strip("'\"").lower()
    clean_action = action.strip().strip("'\"")

    if not clean_trigger or not clean_action:
        return {
            "ok": False,
            "error": "Trigger phrase and action directive cannot be empty.",
            "reply": "I could not learn the command because either the trigger or action was missing.",
        }

    # 1. Save in SQLite custom_commands table
    saved = memory.add_custom_command(
        trigger_phrase=clean_trigger,
        action_payload=clean_action,
        action_type="directive",
        description=description,
    )

    # 2. Add an explicit memory note for semantic recall
    try:
        memory.add(
            f"Custom Command Directive: When the Master says '{clean_trigger}', Jarvis executes '{clean_action}'.",
            tags="custom_command,learned,macro",
            source="command_learner",
        )
    except Exception as exc:
        logger.debug("Memory note addition note: %s", exc)

    # 3. Synchronize to Obsidian Brain
    sync_commands_to_vault(memory, vault_dir=vault_dir)

    reply = (
        f"Understood, sir. I have learned this new directive: Whenever you say "
        f"'{clean_trigger}', I will {clean_action}."
    )

    return {
        "ok": True,
        "command": saved,
        "reply": reply,
        "trigger": clean_trigger,
        "action": clean_action,
    }

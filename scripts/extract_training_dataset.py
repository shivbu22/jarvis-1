"""J.A.R.V.I.S. Training Dataset Extraction & Mining Pipeline.

Mines historical interaction logs (data/audit.jsonl) and SQLite memories/turns
(data/jarvis.db) into clean, high-quality Supervised Fine-Tuning (SFT) datasets
in both Alpaca format and ShareGPT multi-turn conversational format.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TRAINING_DIR = DATA_DIR / "training"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
DB_FILE = DATA_DIR / "jarvis.db"

SYSTEM_PROMPT = (
    "You are J.A.R.V.I.S., an autonomous AI executive and co-founder assistant. "
    "You possess elite engineering, quant finance, and operational capabilities. "
    "You execute tools decisively, reason with high technical depth, and maintain "
    "unwavering accuracy."
)


def extract_from_audit_logs(audit_path: Path) -> list[dict[str, Any]]:
    """Extract valid successful turns and tool executions from audit.jsonl."""
    records: list[dict[str, Any]] = []
    if not audit_path.exists():
        return records

    with open(audit_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue

            event = data.get("event")
            if event != "turn":
                continue

            transcript = (data.get("transcript") or "").strip()
            reply = (data.get("reply") or "").strip()
            ok = data.get("ok", True)

            # Skip empty or failed error turns
            if not transcript or not reply or not ok:
                continue

            # Skip trivial fallback errors
            if "I could not answer that" in reply or "timed out" in reply.lower():
                continue

            tools_called = data.get("tools") or []
            tool_summaries = []
            for t in tools_called:
                name = t.get("name") or "unknown_tool"
                args = t.get("args") or {}
                summary = t.get("summary") or ""
                tool_summaries.append(f"[Tool: {name} | Args: {json.dumps(args)}] -> Result: {summary[:200]}")

            records.append({
                "instruction": transcript,
                "input": "\n".join(tool_summaries) if tool_summaries else "",
                "output": reply,
                "timestamp": data.get("ts", 0),
                "brain": data.get("brain", "ultimate"),
                "tools_used": [t.get("name") for t in tools_called if t.get("name")],
            })

    return records


def extract_from_sqlite(db_path: Path) -> list[dict[str, Any]]:
    """Extract historical conversation turns from SQLite database."""
    records: list[dict[str, Any]] = []
    if not db_path.exists():
        return records

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='turns'")
        if cur.fetchone():
            cur.execute("SELECT id, session_id, role, content, created_at FROM turns ORDER BY id ASC")
            rows = cur.fetchall()
            # Pair consecutive user and assistant/jarvis turns
            last_user = None
            for row in rows:
                _id, _sess, role, content, created_at = row
                if not content:
                    continue
                content = content.strip()
                if role == "user":
                    last_user = (content, created_at)
                elif role in ("jarvis", "assistant") and last_user:
                    user_text, user_ts = last_user
                    if len(user_text) > 3 and len(content) > 3 and "I could not answer that" not in content:
                        records.append({
                            "instruction": user_text,
                            "input": "",
                            "output": content,
                            "timestamp": created_at or user_ts or 0,
                            "brain": "sqlite_turn",
                            "tools_used": [],
                        })
                    last_user = None
        conn.close()
    except Exception as exc:
        print(f"[Dataset] SQLite turn extraction note: {exc}", file=sys.stderr)

    return records


def generate_curated_skill_samples() -> list[dict[str, Any]]:
    """Generate high-quality gold samples exercising JARVIS's signature tools."""
    samples = [
        {
            "instruction": "Scan all autonomous subsystems and report system health status.",
            "input": "[Tool: self_heal_diagnose] -> Result: 10/10 subsystems healthy. Database integrity OK, STT loaded, TTS active.",
            "output": "All 10 autonomous subsystems are operating at peak efficiency. Database integrity is verified, speech recognition is online, and neural British voice synthesis is active with zero degradation.",
            "tools_used": ["self_heal_diagnose"],
        },
        {
            "instruction": "What is our current paper trading portfolio performance and market stance?",
            "input": "[Tool: check_portfolio] -> Result: Cash: ₹10,00,000 | Holdings: RELIANCE (₹2,980.50), TATASTEEL (₹154.20) | P&L: +₹24,500 (+2.45%)",
            "output": "Your paper portfolio currently holds an unrealized profit of +₹24,500 (+2.45%) on an initial capital base of ₹10,000,00. Core positions in Reliance and Tata Steel are tracking above their 20-day moving averages.",
            "tools_used": ["check_portfolio"],
        },
        {
            "instruction": "Recommend an optimal lead generation strategy for enterprise industrial clients.",
            "input": "[Tool: strategy_recommend | Args: {\"task_type\": \"lead_gen\"}] -> Result: Recommended tool sequence lead_search -> lead_qualify -> lead_outreach_draft. Conversion increases +35% with ICP personalization.",
            "output": "Based on historical conversion telemetry, the optimal outreach sequence is lead_search followed by qualification and custom pain-point drafting. Outreach dispatched between Tuesday and Thursday yields a 35% higher response velocity.",
            "tools_used": ["strategy_recommend"],
        },
        {
            "instruction": "Switch primary cognitive reasoning engine to Ollama local brain.",
            "input": "[Tool: ultimate_system_control | Args: {\"action\": \"switch_brain\", \"target\": \"ollama\"}] -> Result: Brain rotated to Ollama (jarvis-brain:3b).",
            "output": "Primary cognitive engine successfully switched to Ollama (jarvis-brain:3b). All subsequent turns will run locally with zero latency overhead.",
            "tools_used": ["ultimate_system_control"],
        },
        {
            "instruction": "Inspect hardware resource allocation and thermal vitals.",
            "input": "[Tool: system_info] -> Result: Windows 11 AMD64, 16 CPU cores, 15.7 GB RAM (14.9 GB used), RTX 3050 GPU (4095 MB VRAM).",
            "output": "System telemetry shows 16 CPU cores operating at nominal load, 15.7 GB system RAM available, and the dedicated NVIDIA RTX 3050 GPU standing by with 4 GB VRAM ready for accelerated inference.",
            "tools_used": ["system_info"],
        },
    ]
    return samples


HANDBOOK_FILE = ROOT / "JARVIS DOCS" / "multilingual_commands_handbook.md"


def extract_from_handbook(handbook_path: Path) -> list[dict[str, Any]]:
    """Extract multilingual English, Hinglish, and Hindi command samples from the handbook."""
    samples: list[dict[str, Any]] = []
    if not handbook_path.exists():
        return samples

    content = handbook_path.read_text(encoding="utf-8", errors="replace")
    table_rows = re.findall(r"^\|(.+?)\|$", content, re.MULTILINE)
    for row in table_rows:
        cols = [c.strip() for c in row.split("|")]
        if any(c.startswith("---") or c.startswith(":---") for c in cols):
            continue
        if len(cols) >= 4 and not cols[0].lower().startswith("action"):
            intent = cols[0].replace("*", "").strip()
            en_cmds = [c.strip(" \"'") for c in cols[1].split("/")]
            hing_cmds = [c.strip(" \"'") for c in cols[2].split("/")]
            hi_cmds = [c.strip(" \"'") for c in cols[3].split("/")] if len(cols) >= 5 else []
            target = cols[4].replace("`", "").strip() if len(cols) >= 5 else cols[3].replace("`", "").strip()

            for en in en_cmds:
                if en and len(en) > 2:
                    samples.append({
                        "instruction": en,
                        "input": f"[Target: {target}]",
                        "output": f"Executing {intent}. Directive dispatched: {target}.",
                        "tools_used": [target.split("(")[0]],
                    })
            for hing in hing_cmds:
                if hing and len(hing) > 2:
                    samples.append({
                        "instruction": hing,
                        "input": f"[Intent: {intent} | Target: {target}]",
                        "output": f"Bilkul sir, {intent.lower()} execute kar raha hoon. {target} dispatched.",
                        "tools_used": [target.split("(")[0]],
                    })
            for hi in hi_cmds:
                if hi and len(hi) > 2:
                    samples.append({
                        "instruction": hi,
                        "input": f"[Intent: {intent} | Target: {target}]",
                        "output": f"जी हाँ सर, {intent} निष्पादित किया जा रहा है।",
                        "tools_used": [target.split("(")[0]],
                    })
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract JARVIS interaction logs into SFT datasets")
    parser.add_argument("--output-dir", type=Path, default=TRAINING_DIR, help="Destination directory for datasets")
    parser.add_argument("--min-length", type=int, default=5, help="Minimum character length for instruction and output")
    args = parser.parse_args()

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Dataset] Mining audit logs from {AUDIT_FILE}...")
    audit_records = extract_from_audit_logs(AUDIT_FILE)
    print(f"[Dataset] Found {len(audit_records)} valid turns from audit logs.")

    print(f"[Dataset] Mining SQLite database from {DB_FILE}...")
    sqlite_records = extract_from_sqlite(DB_FILE)
    print(f"[Dataset] Found {len(sqlite_records)} valid turns from SQLite database.")

    curated_records = generate_curated_skill_samples()
    print(f"[Dataset] Loaded {len(curated_records)} gold skill demonstration samples.")

    print(f"[Dataset] Mining Multilingual Command Handbook from {HANDBOOK_FILE}...")
    handbook_records = extract_from_handbook(HANDBOOK_FILE)
    print(f"[Dataset] Loaded {len(handbook_records)} multilingual handbook command samples.")

    # Deduplicate and combine
    all_records: list[dict[str, Any]] = []
    seen = set()

    for r in curated_records + handbook_records + audit_records + sqlite_records:
        inst = r["instruction"].strip()
        outp = r["output"].strip()
        if len(inst) < args.min_length or len(outp) < args.min_length:
            continue
        key = (inst.lower(), outp.lower()[:80])
        if key in seen:
            continue
        seen.add(key)
        all_records.append(r)

    print(f"[Dataset] Total unique training samples: {len(all_records)}")

    # 1. Write Alpaca format (SFT)
    alpaca_file = out_dir / "sft_alpaca.jsonl"
    with open(alpaca_file, "w", encoding="utf-8") as f:
        for r in all_records:
            entry = {
                "instruction": r["instruction"],
                "input": r.get("input", ""),
                "output": r["output"],
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[Dataset] Wrote Alpaca SFT dataset -> {alpaca_file}")

    # 2. Write ShareGPT multi-turn format
    sharegpt_file = out_dir / "sharegpt_turns.jsonl"
    with open(sharegpt_file, "w", encoding="utf-8") as f:
        for r in all_records:
            conv = [
                {"from": "system", "value": SYSTEM_PROMPT},
                {"from": "human", "value": r["instruction"] + (f"\n\nContext:\n{r['input']}" if r.get("input") else "")},
                {"from": "gpt", "value": r["output"]},
            ]
            f.write(json.dumps({"conversations": conv}, ensure_ascii=False) + "\n")
    print(f"[Dataset] Wrote ShareGPT dataset -> {sharegpt_file}")

    # 3. Write Manifest & Summary
    manifest = {
        "total_samples": len(all_records),
        "source_audit_turns": len(audit_records),
        "source_sqlite_turns": len(sqlite_records),
        "gold_skill_samples": len(curated_records),
        "alpaca_file": str(alpaca_file),
        "sharegpt_file": str(sharegpt_file),
    }
    manifest_file = out_dir / "dataset_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[Dataset] Manifest written -> {manifest_file}")
    print("\n[Dataset] Dataset extraction completed successfully!")


if __name__ == "__main__":
    main()

---
title: Tool Orchestration & Security Policy
created: 2026-09-18
updated: 2026-09-18
type: concept
tags: [desktop-automation, python-eval, architecture]
confidence: high
sources: [config/policy.yaml, apps/gateway/tools/registry.py]
---

# Tool Orchestration & Security Policy

## 3-Tier Security Policy
Every tool executed by [[entities/jarvis-core|JARVIS Core]] is categorized under a strict policy:

1. **`auto` (Autonomous Execution):**
   - Safe, non-destructive, read-only tools.
   - Examples: `system_info`, `get_weather`, `web_search`, `code_search`, `task_plan_manage`, `process_inspect`, `memory_recall`.
2. **`confirm` (User Authorization Required):**
   - System state modifications, code execution, destructive writes, and external communication.
   - Examples: `python_eval`, `file_patch`, `desktop_keyboard`, `mail_send`, `process_kill`.
3. **`deny` (Hard Blocked):**
   - Unsafe operations prohibited by system security constraints.

## Tool Registry Modules
- **`agentic.py`:** `python_eval`, `code_search`, `task_plan_manage`, `web_scrape_page`, `process_inspect`, `file_patch`.
- **`desktop.py`:** `desktop_launch_app`, `desktop_focus_window`, `desktop_screenshot`, `desktop_mouse_click`, `desktop_keyboard_type`.
- **`system.py`:** `system_info`, `system_volume_set`, `media_control`, `process_list`.
- **`web.py`:** `web_search`, `fetch_page`, `get_weather`.
- **`memory_tools.py`:** `memory_remember`, `memory_recall`, `memory_list`.

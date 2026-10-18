"""Agentic and autonomous tools for Jarvis.

Provides advanced reasoning, autonomous task tracking, Python execution,
codebase searching, live web scraping, and process inspection.
"""

from __future__ import annotations

import html
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

# --------------------------------------------------------------------------
# 1. Python Execution Sandbox
# --------------------------------------------------------------------------


def python_eval(ctx: ToolContext, code: str) -> ToolOutput:
    """Execute Python code and return stdout, stderr, and the final expression value."""
    if not code or not code.strip():
        return ToolOutput(text="No Python code was provided to execute.", ok=False)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr

    # Safe and useful local execution environment
    local_env: dict[str, Any] = {
        "ctx": ctx,
        "json": json,
        "math": __import__("math"),
        "re": re,
        "time": time,
        "Path": Path,
        "os": os,
        "sys": sys,
    }

    result_val = None
    start_time = time.perf_counter()
    try:
        sys.stdout, sys.stderr = stdout_buf, stderr_buf
        # Try evaluating as an expression first
        try:
            compiled = compile(code.strip(), "<jarvis_python>", "eval")
            result_val = eval(compiled, local_env)
        except SyntaxError:
            compiled = compile(code.strip(), "<jarvis_python>", "exec")
            exec(compiled, local_env)
            result_val = local_env.get("result")
    except Exception as exc:
        tb = traceback.format_exc(limit=3)
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        return ToolOutput(
            text=f"Python execution failed ({elapsed_ms}ms):\n{tb}",
            data={"ok": False, "error": str(exc), "traceback": tb},
            ok=False,
        )
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    stdout_text = stdout_buf.getvalue().strip()
    stderr_text = stderr_buf.getvalue().strip()
    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

    output_lines = []
    if stdout_text:
        output_lines.append(f"Output:\n{stdout_text}")
    if result_val is not None:
        output_lines.append(f"Result: {result_val}")
    if stderr_text:
        output_lines.append(f"Stderr:\n{stderr_text}")

    final_text = "\n\n".join(output_lines) if output_lines else f"Code executed successfully ({elapsed_ms}ms, no output)."
    return ToolOutput(
        text=final_text,
        data={
            "stdout": stdout_text,
            "stderr": stderr_text,
            "result": str(result_val) if result_val is not None else None,
            "elapsed_ms": elapsed_ms,
        },
        ok=True,
    )


# --------------------------------------------------------------------------
# 2. Code Search / Grep
# --------------------------------------------------------------------------


def code_search(
    ctx: ToolContext,
    query: str,
    path: str = ".",
    file_pattern: str = "*",
    max_results: int = 30,
) -> ToolOutput:
    """Search for text or regex patterns across files."""
    if not query:
        return ToolOutput(text="Search query cannot be empty.", ok=False)

    target_dir = Path(os.path.expandvars(str(path))).expanduser()
    if not target_dir.exists() or target_dir.name.lower() in ("codebase", "jarvis", "repo", "root"):
        target_dir = Path(getattr(ctx.settings, "root", "."))
    if not target_dir.exists():
        target_dir = Path(".")

    max_results = max(1, min(int(max_results or 30), 100))
    matches: list[dict[str, Any]] = []

    try:
        regex = re.compile(query, re.IGNORECASE)
    except re.error:
        regex = re.compile(re.escape(query), re.IGNORECASE)

    ignored_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build"}

    try:
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
            for file_name in files:
                if file_pattern != "*" and not Path(file_name).match(file_pattern):
                    continue
                file_path = Path(root) / file_name
                try:
                    if file_path.stat().st_size > 2 * 1024 * 1024:  # Skip >2MB files
                        continue
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        for line_no, line in enumerate(f, start=1):
                            if regex.search(line):
                                matches.append({
                                    "file": str(file_path.relative_to(target_dir) if target_dir != Path(".") else file_path),
                                    "line": line_no,
                                    "content": line.strip()[:200],
                                })
                                if len(matches) >= max_results:
                                    break
                except Exception:
                    continue
                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break
    except Exception as exc:
        return ToolOutput(text=f"Search failed: {exc}", ok=False)

    if not matches:
        return ToolOutput(text=f"No matches found for '{query}'.", data={"matches": []})

    lines = [f"Found {len(matches)} match(es) for '{query}':"]
    for m in matches:
        lines.append(f"{m['file']}:{m['line']} | {m['content']}")

    return ToolOutput(text="\n".join(lines), data={"matches": matches})


# --------------------------------------------------------------------------
# 3. Autonomous Task Planner / Tracker
# --------------------------------------------------------------------------


def _tasks_file(ctx: ToolContext) -> Path:
    data_dir = Path(ctx.settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "tasks.json"


def _load_tasks(ctx: ToolContext) -> list[dict[str, Any]]:
    path = _tasks_file(ctx)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_tasks(ctx: ToolContext, tasks: list[dict[str, Any]]) -> None:
    path = _tasks_file(ctx)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2)


def task_plan_manage(
    ctx: ToolContext,
    action: str = "list",
    task_id: str = "",
    title: str = "",
    steps: str = "",
    status: str = "",
) -> ToolOutput:
    """Manage autonomous multi-step tasks and checklists."""
    tasks = _load_tasks(ctx)
    action = (action or "list").lower().strip()

    if action == "list":
        if not tasks:
            return ToolOutput(text="No tasks currently tracked.", data={"tasks": []})
        lines = ["Tracked Autonomous Tasks:"]
        for t in tasks:
            steps_desc = ", ".join(f"[{'x' if s.get('done') else ' '}] {s.get('text')}" for s in t.get("steps", []))
            lines.append(f"ID {t.get('id')}: {t.get('title')} [{t.get('status')}] - Steps: {steps_desc or 'none'}")
        return ToolOutput(text="\n".join(lines), data={"tasks": tasks})

    if action == "create":
        if not title:
            return ToolOutput(text="Task title is required to create a task.", ok=False)
        new_id = f"task_{int(time.time())}"
        step_items = []
        if steps:
            for s in str(steps).split(";"):
                if s.strip():
                    step_items.append({"text": s.strip(), "done": False})
        task_obj = {
            "id": new_id,
            "title": title,
            "status": "pending",
            "steps": step_items,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tasks.append(task_obj)
        _save_tasks(ctx, tasks)
        return ToolOutput(
            text=f"Created task '{title}' with ID {new_id} ({len(step_items)} steps).",
            data=task_obj,
        )

    if action in ("update", "complete", "delete"):
        target = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
        if not target:
            return ToolOutput(text=f"Task with ID '{task_id}' was not found.", ok=False)

        if action == "delete":
            tasks = [t for t in tasks if str(t.get("id")) != str(task_id)]
            _save_tasks(ctx, tasks)
            return ToolOutput(text=f"Deleted task {task_id}.", data={"deleted": task_id})

        if action == "complete":
            target["status"] = "completed"
            for s in target.get("steps", []):
                s["done"] = True
            _save_tasks(ctx, tasks)
            return ToolOutput(text=f"Marked task {task_id} as completed.", data=target)

        if action == "update":
            if status:
                target["status"] = status
            if title:
                target["title"] = title
            _save_tasks(ctx, tasks)
            return ToolOutput(text=f"Updated task {task_id}.", data=target)

    return ToolOutput(text=f"Unknown action '{action}'. Valid actions: list, create, update, complete, delete.", ok=False)


# --------------------------------------------------------------------------
# 4. Web Scraping & Markdown Extraction
# --------------------------------------------------------------------------


def web_scrape_page(ctx: ToolContext, url: str, max_chars: int = 6000) -> ToolOutput:
    """Fetch a webpage URL and extract clean text/markdown content."""
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return ToolOutput(text="Please provide a valid HTTP or HTTPS URL.", ok=False)

    max_chars = max(500, min(int(max_chars or 6000), 20000))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JarvisAssistant/1.0",
        "Accept": "text/html,application/xhtml+xml,text/plain",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            raw_bytes = response.read(1024 * 1024)  # Read up to 1MB
            encoding = response.headers.get_content_charset() or "utf-8"
            html_text = raw_bytes.decode(encoding, errors="replace")

        # Strip head, scripts, styles
        text = re.sub(r"(?is)<script.*?</script>", " ", html_text)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        text = re.sub(r"(?is)<nav.*?</nav>", " ", text)
        text = re.sub(r"(?is)<footer.*?</footer>", " ", text)
        text = re.sub(r"(?is)<header.*?</header>", " ", text)

        # Basic markdown conversion
        text = re.sub(r"(?i)<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\n## \1\n", text)
        text = re.sub(r"(?i)<p[^>]*>(.*?)</p>", r"\n\1\n", text)
        text = re.sub(r"(?i)<li[^>]*>(.*?)</li>", r"\n* \1", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[Truncated to {max_chars} characters...]"

        return ToolOutput(
            text=f"Content from {url}:\n\n{text}",
            data={"url": url, "chars": len(text)},
        )
    except Exception as exc:
        return ToolOutput(text=f"Failed to fetch {url}: {exc}", ok=False)


# --------------------------------------------------------------------------
# 5. Process Inspection
# --------------------------------------------------------------------------


def process_inspect(ctx: ToolContext, filter_name: str = "", limit: int = 25) -> ToolOutput:
    """Inspect currently running applications and processes."""
    limit = max(5, min(int(limit or 25), 100))
    processes: list[dict[str, Any]] = []

    try:
        # Use Windows tasklist via powershell/subprocess
        cmd = ["tasklist", "/FO", "CSV", "/NH"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=True)
        for line in result.stdout.strip().splitlines():
            parts = [p.strip(' "') for p in line.split('","')]
            if len(parts) >= 5:
                name, pid, _session_name, _session_num, mem = parts[:5]
                if filter_name and filter_name.lower() not in name.lower():
                    continue
                processes.append({
                    "name": name,
                    "pid": pid,
                    "memory": mem,
                })
                if len(processes) >= limit:
                    break
    except Exception as exc:
        return ToolOutput(text=f"Failed to inspect processes: {exc}", ok=False)

    if not processes:
        return ToolOutput(text=f"No processes matching '{filter_name}'.", data={"processes": []})

    lines = [f"Active Processes (showing {len(processes)}):"]
    for p in processes:
        lines.append(f"- {p['name']} (PID: {p['pid']}, Memory: {p['memory']})")

    return ToolOutput(text="\n".join(lines), data={"processes": processes})


# --------------------------------------------------------------------------
# 6. File Patch / Search & Replace
# --------------------------------------------------------------------------


def file_patch(ctx: ToolContext, path: str, search: str, replace: str) -> ToolOutput:
    """Find and replace an exact block of text in a file."""
    target = Path(os.path.expandvars(str(path))).expanduser()
    if not target.exists() or not target.is_file():
        return ToolOutput(text=f"File {target} does not exist.", ok=False)

    reason = ctx.policy.check_write_path(target)
    if reason:
        return ToolOutput(text=f"Modification refused: {reason}", ok=False)

    if not search:
        return ToolOutput(text="Search pattern cannot be empty.", ok=False)

    try:
        with open(target, encoding="utf-8") as f:
            content = f.read()

        if search not in content:
            return ToolOutput(text=f"Could not find exact text match in {target.name}.", ok=False)

        count = content.count(search)
        if count > 1:
            return ToolOutput(
                text=f"Found {count} occurrences of search text in {target.name}. Please provide more surrounding context to match uniquely.",
                ok=False,
            )

        new_content = content.replace(search, replace, 1)
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_content)

        return ToolOutput(
            text=f"Successfully patched {target.name}.",
            data={"path": str(target), "modified": True},
        )
    except Exception as exc:
        return ToolOutput(text=f"Failed to patch {target}: {exc}", ok=False)


# --------------------------------------------------------------------------
# Build Specs
# --------------------------------------------------------------------------


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="python_eval",
            description=(
                "Execute Python code for math, data transformations, algorithms, or "
                "scripting. Returns stdout, stderr, and returned expression values."
            ),
            parameters=object_schema(
                {"code": param("code", "string", "Python code to execute.")},
                ["code"],
            ),
            handler=python_eval,
            executing=True,
            category="agentic",
        ),
        ToolSpec(
            name="code_search",
            description="Search for text or regex patterns across files in a directory.",
            parameters=object_schema(
                {
                    "query": param("query", "string", "Text or regular expression to find."),
                    "path": param("path", "string", "Root directory to search inside.", default="."),
                    "file_pattern": param("file_pattern", "string", "Glob filter (e.g. *.py, *.ts).", default="*"),
                    "max_results": param("max_results", "integer", "Maximum matching lines.", default=30),
                },
                ["query"],
            ),
            handler=code_search,
            executing=False,
            category="agentic",
        ),
        ToolSpec(
            name="task_plan_manage",
            description=(
                "Manage autonomous multi-step tasks and checklists. Actions: "
                "list, create, update, complete, delete."
            ),
            parameters=object_schema(
                {
                    "action": param("action", "string", "Action to perform: list, create, update, complete, delete.", default="list"),
                    "task_id": param("task_id", "string", "Task ID for update/complete/delete.", default=""),
                    "title": param("title", "string", "Task title (for create/update).", default=""),
                    "steps": param("steps", "string", "Semicolon-separated sub-steps.", default=""),
                    "status": param("status", "string", "Status: pending, in_progress, completed.", default=""),
                },
                [],
            ),
            handler=task_plan_manage,
            executing=False,
            category="agentic",
        ),
        ToolSpec(
            name="web_scrape_page",
            description="Fetch a webpage URL and extract clean text or markdown content.",
            parameters=object_schema(
                {
                    "url": param("url", "string", "Web page URL (http/https)."),
                    "max_chars": param("max_chars", "integer", "Character limit for returned content.", default=6000),
                },
                ["url"],
            ),
            handler=web_scrape_page,
            executing=False,
            category="agentic",
        ),
        ToolSpec(
            name="process_inspect",
            description="Inspect active running system processes, application names, and memory usage.",
            parameters=object_schema(
                {
                    "filter_name": param("filter_name", "string", "Optional process name filter.", default=""),
                    "limit": param("limit", "integer", "Maximum processes to return.", default=25),
                },
                [],
            ),
            handler=process_inspect,
            executing=False,
            category="agentic",
        ),
        ToolSpec(
            name="file_patch",
            description="Replace an exact snippet or block of text in an existing file.",
            parameters=object_schema(
                {
                    "path": param("path", "string", "Path to the file to edit."),
                    "search": param("search", "string", "Exact text block to find."),
                    "replace": param("replace", "string", "New text to substitute."),
                },
                ["path", "search", "replace"],
            ),
            handler=file_patch,
            executing=True,
            category="agentic",
        ),
    ]

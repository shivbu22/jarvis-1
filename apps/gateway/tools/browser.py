"""Browser automation via Playwright.

Three implementation notes that matter more than the API surface:

**All browser work runs on one dedicated thread.** Playwright's sync API creates
an event loop per thread, and a session created on one thread cannot be driven
from another. The registry dispatches sync handlers through ``asyncio.to_thread``,
which hands out arbitrary workers, so each browser action is re-submitted to a
single-worker executor and the calling thread waits. Without this the second call
in a conversation fails with a cross-thread error.

**It prefers the browser Windows already has.** ``channel="msedge"` drives the
installed Edge, so the feature works without a 150 MB Chromium download. Set
``BROWSER_CHANNEL`` to ``chrome``, ``chromium`` or ``none`` in `.env` to change
that.

**It needs the optional dependency.** ``playwright`` is in requirements-dev.txt;
when it is missing the tools explain how to install it rather than erroring.
"""

from __future__ import annotations

import contextlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .registry import (
    ToolContext,
    ToolOutput,
    ToolSpec,
    boolean_param,
    integer_param,
    object_schema,
    param,
)

logger = logging.getLogger(__name__)

SESSION_KEY = "browser_session"
EXECUTOR_KEY = "browser_executor"
DEFAULT_TIMEOUT_MS = 15000


def _executor(ctx: ToolContext) -> ThreadPoolExecutor:
    executor = ctx.state.get(EXECUTOR_KEY)
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        ctx.state[EXECUTOR_KEY] = executor
    return executor


def _on_browser_thread(ctx: ToolContext, action) -> Any:
    """Run `action(session)` on the pinned thread and return its result."""
    future = _executor(ctx).submit(action, _session(ctx))
    return future.result(timeout=120)


class BrowserSession:
    """A lazily started browser, owned by one thread."""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self.playwright: Any = None
        self.browser: Any = None
        self.page: Any = None
        self.blocked_hosts: set[str] = set()

    # -- lifecycle --------------------------------------------------------

    def ensure(self) -> Any:
        if self.page is not None:
            return self.page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Browser control needs the playwright package. Install it with "
                "'uv pip install -r requirements-dev.txt', then run "
                "'python -m playwright install chromium' if you do not want to use "
                "the installed Edge."
            ) from exc

        self.playwright = sync_playwright().start()
        channel = str(getattr(self.ctx.settings, "browser_channel", "msedge") or "msedge").strip()
        attempts: list[dict[str, Any]] = []
        if channel and channel.lower() not in {"none", "chromium"}:
            attempts.append({"channel": channel})
        attempts.append({})  # bundled chromium, if it was installed

        errors: list[str] = []
        for kwargs in attempts:
            try:
                self.browser = self.playwright.chromium.launch(headless=False, **kwargs)
                break
            except Exception as exc:
                errors.append(f"{kwargs or 'chromium'}: {exc}")
                self.browser = None
        if self.browser is None:
            self._stop_playwright()
            raise RuntimeError(
                "I could not start a browser. Tried " + "; ".join(errors) + ". "
                "Install one with 'python -m playwright install chromium', or set "
                "BROWSER_CHANNEL=chrome in .env."
            )

        self.page = self.browser.new_page()
        self.page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        return self.page

    def _stop_playwright(self) -> None:
        if self.playwright is not None:
            with contextlib.suppress(Exception):
                self.playwright.stop()
            self.playwright = None

    def close(self) -> None:
        for closer in ("page", "browser"):
            obj = getattr(self, closer, None)
            if obj is not None:
                with contextlib.suppress(Exception):
                    obj.close()
                setattr(self, closer, None)
        self._stop_playwright()

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.browser is not None,
            "url": getattr(self.page, "url", None),
        }


def _session(ctx: ToolContext) -> BrowserSession:
    session = ctx.state.get(SESSION_KEY)
    if session is None:
        session = BrowserSession(ctx)
        ctx.state[SESSION_KEY] = session
    return session


def close_browser_session(ctx: ToolContext) -> None:
    """Tear down the browser and its thread. Called on gateway shutdown."""
    session = ctx.state.pop(SESSION_KEY, None)
    executor = ctx.state.pop(EXECUTOR_KEY, None)
    if session is not None:
        with contextlib.suppress(Exception):
            _executor(ctx).submit(session.close).result(timeout=20)
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------


def browser_open(ctx: ToolContext, url: str, headless: bool | None = None) -> ToolOutput:
    def action(session: BrowserSession) -> dict[str, Any]:
        page = session.ensure()
        page.goto(url, wait_until="domcontentloaded")
        return {"url": page.url, "title": page.title()}

    try:
        result = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"I could not open {url}: {exc}", ok=False)
    return ToolOutput(
        text=f"Opened {result['url']} -- title: {result['title']}",
        data=result,
    )


def browser_read(ctx: ToolContext, selector: str = "", max_chars: int = 6000) -> ToolOutput:
    max_chars = max(200, min(int(max_chars or 6000), 20000))

    def action(session: BrowserSession) -> dict[str, Any]:
        page = session.ensure()
        if selector:
            element = page.locator(selector).first
            text = element.inner_text(timeout=DEFAULT_TIMEOUT_MS)
        else:
            text = page.inner_text("body")
        return {"url": page.url, "title": page.title(), "text": text}

    try:
        result = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"I could not read the page: {exc}", ok=False)

    text = " ".join(str(result["text"]).split())
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return ToolOutput(
        text=f"{result['url']} says: {text}",
        data={"url": result["url"], "title": result["title"], "text": text},
    )


def browser_click(ctx: ToolContext, selector: str) -> ToolOutput:
    def action(session: BrowserSession) -> dict[str, Any]:
        page = session.ensure()
        page.locator(selector).first.click()
        page.wait_for_load_state("domcontentloaded")
        return {"url": page.url, "title": page.title()}

    try:
        result = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"I could not click '{selector}': {exc}", ok=False)
    return ToolOutput(text=f"Clicked {selector}; now at {result['url']}", data=result)


def browser_type(ctx: ToolContext, selector: str, text: str, submit: bool = False) -> ToolOutput:
    def action(session: BrowserSession) -> dict[str, Any]:
        page = session.ensure()
        field = page.locator(selector).first
        field.fill(text)
        if submit:
            field.press("Enter")
            page.wait_for_load_state("domcontentloaded")
        return {"url": page.url, "title": page.title()}

    try:
        result = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"I could not type into '{selector}': {exc}", ok=False)
    verb = "Typed and submitted" if submit else "Typed"
    return ToolOutput(text=f"{verb} into {selector}; now at {result['url']}", data=result)


def browser_screenshot(ctx: ToolContext, path: str = "") -> ToolOutput:
    target = (
        Path(path)
        if path
        else Path(ctx.settings.data_dir) / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
    )
    if path:
        reason = ctx.policy.check_write_path(target)
        if reason:
            raise PermissionError(reason)

    def action(session: BrowserSession) -> str:
        page = session.ensure()
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=False)
        return str(target)

    try:
        saved = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"I could not take a screenshot: {exc}", ok=False)
    return ToolOutput(text=f"Saved a screenshot to {saved}.", data={"path": saved})


def browser_close(ctx: ToolContext) -> ToolOutput:
    def action(session: BrowserSession) -> bool:
        session.close()
        return True

    try:
        _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"I could not close the browser: {exc}", ok=False)
    return ToolOutput(text="Closed the browser.")


def browser_scroll(ctx: ToolContext, direction: str = "down", amount: int = 500) -> ToolOutput:
    amount = max(50, min(int(amount or 500), 5000))
    d = (direction or "down").lower().strip()

    def action(session: BrowserSession) -> dict[str, Any]:
        page = session.ensure()
        if d == "down":
            page.evaluate(f"window.scrollBy(0, {amount})")
        elif d == "up":
            page.evaluate(f"window.scrollBy(0, -{amount})")
        elif d == "top":
            page.evaluate("window.scrollTo(0, 0)")
        elif d == "bottom":
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            page.evaluate(f"window.scrollBy(0, {amount})")
        return {"url": page.url, "direction": d, "amount": amount}

    try:
        res = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"I could not scroll the page: {exc}", ok=False)
    return ToolOutput(text=f"Scrolled {d} in browser.", data=res, ok=True)


def browser_press_key(ctx: ToolContext, key: str) -> ToolOutput:
    k = (key or "").strip()
    if not k:
        return ToolOutput(text="Key name required (e.g. 'Enter', 'Escape', 'Tab', 'PageDown').", ok=False)

    def action(session: BrowserSession) -> dict[str, Any]:
        page = session.ensure()
        page.keyboard.press(k)
        return {"url": page.url, "key": k}

    try:
        res = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"I could not press key '{k}': {exc}", ok=False)
    return ToolOutput(text=f"Pressed '{k}' in browser.", data=res, ok=True)


def browser_navigate(ctx: ToolContext, action_name: str = "reload", url: str = "") -> ToolOutput:
    act = (action_name or "reload").lower().strip()

    def action(session: BrowserSession) -> dict[str, Any]:
        page = session.ensure()
        if act == "back":
            page.go_back()
        elif act == "forward":
            page.go_forward()
        elif act == "reload":
            page.reload()
        elif act == "goto" and url:
            page.goto(url, wait_until="domcontentloaded")
        else:
            page.reload()
        return {"url": page.url, "title": page.title(), "action": act}

    try:
        res = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"Navigation failed ({act}): {exc}", ok=False)
    return ToolOutput(text=f"Browser {act} completed: now at {res['url']}", data=res, ok=True)


def browser_eval_js(ctx: ToolContext, script: str) -> ToolOutput:
    s = (script or "").strip()
    if not s:
        return ToolOutput(text="Script cannot be empty.", ok=False)

    def action(session: BrowserSession) -> Any:
        page = session.ensure()
        return page.evaluate(s)

    try:
        res = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"JavaScript evaluation failed: {exc}", ok=False)
    return ToolOutput(text=f"Evaluated JS result: {str(res)[:1000]}", data={"result": res}, ok=True)


def browser_get_elements(ctx: ToolContext, selector: str = "a, button, input, textarea, select") -> ToolOutput:
    sel = selector or "a, button, input, textarea, select"

    def action(session: BrowserSession) -> list[dict[str, Any]]:
        page = session.ensure()
        return page.evaluate("""(query) => {
            const els = Array.from(document.querySelectorAll(query));
            const items = [];
            for (let i = 0; i < els.length && items.length < 40; i++) {
                const el = els[i];
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim();
                    items.push({
                        tag: el.tagName.toLowerCase(),
                        text: text.slice(0, 60),
                        id: el.id || '',
                        href: el.getAttribute('href') || '',
                        type: el.getAttribute('type') || ''
                    });
                }
            }
            return items;
        }""", sel)

    try:
        elements = _on_browser_thread(ctx, action)
    except Exception as exc:
        return ToolOutput(text=f"Could not inspect elements: {exc}", ok=False)

    lines = [f"Found {len(elements)} interactive elements matching '{sel}':"]
    for idx, e in enumerate(elements[:25]):
        info = f"[{idx+1}] <{e['tag']}>"
        if e['id']:
            info += f" id='{e['id']}'"
        if e['text']:
            info += f" '{e['text']}'"
        if e['href']:
            info += f" -> {e['href']}"
        lines.append(info)

    return ToolOutput(text="\n".join(lines), data={"elements": elements}, ok=True)


# --------------------------------------------------------------------------
# specs
# --------------------------------------------------------------------------


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="browser_open",
            description="Open a URL in a real browser window on this machine. Needs permission.",
            parameters=object_schema(
                {
                    "url": param("url", "string", "Full URL, including https://."),
                    "headless": boolean_param("Run in headless mode (default false).", default=False),
                },
                ["url"],
            ),
            handler=browser_open,
            executing=True,
            category="browser",
        ),
        ToolSpec(
            name="browser_read",
            description=(
                "Read the visible text of the page the browser currently shows, or of "
                "one element when a selector is given."
            ),
            parameters=object_schema(
                {
                    "selector": param(
                        "selector", "string", "Optional CSS selector.", default=""
                    ),
                    "max_chars": param(
                        "max_chars", "integer", "Maximum characters to return.",
                        default=6000, minimum=200, maximum=20000,
                    ),
                },
                [],
            ),
            handler=browser_read,
            executing=False,
            category="browser",
        ),
        ToolSpec(
            name="browser_click",
            description="Click an element on the current page. Needs permission.",
            parameters=object_schema(
                {"selector": param("selector", "string", "CSS selector to click.")}, ["selector"]
            ),
            handler=browser_click,
            executing=True,
            category="browser",
        ),
        ToolSpec(
            name="browser_type",
            description=(
                "Type into a field on the current page, optionally pressing Enter to "
                "submit. Needs permission."
            ),
            parameters=object_schema(
                {
                    "selector": param("selector", "string", "CSS selector of the input."),
                    "text": param("text", "string", "Text to type."),
                    "submit": param(
                        "submit", "boolean", "Press Enter after typing.",
                        default=False,
                    ),
                },
                ["selector", "text"],
            ),
            handler=browser_type,
            executing=True,
            category="browser",
        ),
        ToolSpec(
            name="browser_scroll",
            description="Scroll the active browser page up, down, top, or bottom.",
            parameters=object_schema(
                {
                    "direction": param("direction", "string", "Scroll direction: 'down', 'up', 'top', 'bottom'.", enum=["down", "up", "top", "bottom"], default="down"),
                    "amount": integer_param("Pixels to scroll for up/down (default 500)", default=500),
                },
                [],
            ),
            handler=browser_scroll,
            executing=True,
            category="browser",
        ),
        ToolSpec(
            name="browser_press_key",
            description="Send a keyboard key press to the active browser page (e.g. 'Enter', 'Escape', 'Tab', 'PageDown').",
            parameters=object_schema(
                {"key": param("key", "string", "Key name to press, e.g. 'Enter', 'Escape', 'Tab', 'PageDown', 'ArrowDown'.")}, ["key"]
            ),
            handler=browser_press_key,
            executing=True,
            category="browser",
        ),
        ToolSpec(
            name="browser_navigate",
            description="Navigate browser history or reload: 'back', 'forward', 'reload', 'goto'.",
            parameters=object_schema(
                {
                    "action_name": param("action_name", "string", "Navigation action: 'reload', 'back', 'forward', 'goto'.", enum=["reload", "back", "forward", "goto"], default="reload"),
                    "url": param("url", "string", "Destination URL if action is 'goto'.", default=""),
                },
                [],
            ),
            handler=browser_navigate,
            executing=True,
            category="browser",
        ),
        ToolSpec(
            name="browser_eval_js",
            description="Evaluate custom JavaScript code within the active browser tab.",
            parameters=object_schema(
                {"script": param("script", "string", "JavaScript code to execute in page context.")}, ["script"]
            ),
            handler=browser_eval_js,
            executing=True,
            category="browser",
        ),
        ToolSpec(
            name="browser_get_elements",
            description="Inspect interactive elements (buttons, links, inputs) on current page with their tags and IDs.",
            parameters=object_schema(
                {"selector": param("selector", "string", "CSS query selector (defaults to interactive elements).", default="a, button, input, textarea, select")}, []
            ),
            handler=browser_get_elements,
            executing=False,
            category="browser",
        ),
        ToolSpec(
            name="browser_screenshot",
            description="Save a screenshot of the current page. Needs permission.",
            parameters=object_schema(
                {
                    "path": param(
                        "path", "string",
                        "Optional destination; defaults to data/ in the Jarvis folder.",
                        default="",
                    )
                },
                [],
            ),
            handler=browser_screenshot,
            executing=True,
            category="browser",
        ),
        ToolSpec(
            name="browser_close",
            description="Close the browser window Jarvis opened.",
            parameters=object_schema({}, []),
            handler=browser_close,
            executing=True,
            category="browser",
        ),
    ]

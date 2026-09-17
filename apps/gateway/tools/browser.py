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

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

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


def browser_open(ctx: ToolContext, url: str) -> ToolOutput:
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


# --------------------------------------------------------------------------
# specs
# --------------------------------------------------------------------------


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="browser_open",
            description="Open a URL in a real browser window on this machine. Needs permission.",
            parameters=object_schema(
                {"url": param("url", "string", "Full URL, including https://.")}, ["url"]
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

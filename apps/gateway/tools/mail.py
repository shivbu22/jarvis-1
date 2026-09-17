"""Mail tools over IMAP and SMTP, configured entirely by environment variables.

Reading and sending are separate tools because they carry different risk: listing
or reading is tier ``auto``, while sending is tier ``confirm`` and therefore always
stops to ask. A reply sent on the master's behalf, without them hearing it first,
is the kind of mistake that cannot be taken back.

Headers are fetched with ``BODY.PEEK`` so that *looking* at a message does not
mark it as seen -- a passive "any important mail?" must not silently clear the
unread state the master relies on.

No credentials are stored here: everything comes from `.env`, and when it is
absent the tools explain what to set rather than failing obscurely.
"""

from __future__ import annotations

import contextlib
import email
import email.header
import email.utils
import imaplib
import logging
import re
import smtplib
from email.message import EmailMessage
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 8000


def _config(ctx: ToolContext) -> dict[str, Any] | None:
    settings = ctx.settings
    address = str(getattr(settings, "mail_address", "") or "").strip()
    password = str(getattr(settings, "mail_app_password", "") or "").strip()
    if not address or not password:
        return None
    host_guess = address.split("@")[-1] if "@" in address else ""
    return {
        "address": address,
        "password": password,
        "display_name": str(getattr(settings, "mail_from_name", "") or "").strip(),
        "imap_host": str(getattr(settings, "imap_host", "") or f"imap.{host_guess}"),
        "imap_port": int(getattr(settings, "imap_port", 993)),
        "smtp_host": str(getattr(settings, "smtp_host", "") or f"smtp.{host_guess}"),
        "smtp_port": int(getattr(settings, "smtp_port", 465)),
    }


NOT_CONFIGURED = (
    "Mail is not configured yet. Add MAIL_ADDRESS and MAIL_APP_PASSWORD to .env "
    "(an app password, not your normal password), then restart the gateway."
)


def _decode_header(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parts = email.header.decode_header(raw)
    except Exception:
        return str(raw)
    out: list[str] = []
    for value, charset in parts:
        if isinstance(value, bytes):
            out.append(value.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(str(value))
    return " ".join(out).strip()


def _body_text(message: email.message.Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition") or ""
            ):
                try:
                    return str(part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    ))
                except (AttributeError, LookupError):
                    continue
        for part in message.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    return re.sub(r"<[^>]+>", " ", html)
                except (AttributeError, LookupError):
                    continue
        return ""
    try:
        return str(message.get_payload(decode=True).decode(
            message.get_content_charset() or "utf-8", errors="replace"
        ))
    except (AttributeError, LookupError):
        return str(message.get_payload() or "")


def _connect_imap(config: dict[str, Any]) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"])
    client.login(config["address"], config["password"])
    client.select("INBOX")
    return client


def mail_list_unread(ctx: ToolContext, limit: int = 10) -> ToolOutput:
    config = _config(ctx)
    if config is None:
        return ToolOutput(text=NOT_CONFIGURED, ok=False)
    limit = max(1, min(int(limit or 10), 30))

    client = None
    try:
        client = _connect_imap(config)
        status, data = client.search(None, "UNSEEN")
        if status != "OK":
            return ToolOutput(text="I could not search the inbox.", ok=False)
        ids = (data[0] or b"").split()
        if not ids:
            return ToolOutput(text="No unread mail.", data={"messages": []})

        selected = ids[-limit:]
        selected.reverse()  # newest first
        messages: list[dict[str, Any]] = []
        for raw_id in selected:
            status, fetch = client.fetch(raw_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK" or not fetch:
                continue
            header_bytes = fetch[0][1] if isinstance(fetch[0], tuple) else b""
            parsed = email.message_from_bytes(header_bytes)
            messages.append(
                {
                    "uid": raw_id.decode(),
                    "from": _decode_header(parsed.get("From")),
                    "subject": _decode_header(parsed.get("Subject")) or "(no subject)",
                    "date": _decode_header(parsed.get("Date")),
                }
            )
    except imaplib.IMAP4.error as exc:
        hint = ""
        if "LOGIN" in str(exc).upper() or "AUTH" in str(exc).upper():
            hint = " Check MAIL_APP_PASSWORD -- Gmail needs an app password, not the account password."
        return ToolOutput(text=f"Mailbox login failed: {exc}.{hint}", ok=False)
    except OSError as exc:
        return ToolOutput(text=f"I could not reach the mail server: {exc}", ok=False)
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.logout()

    if not messages:
        return ToolOutput(text="No unread mail.", data={"messages": []})

    lines = [f"{len(messages)} unread message(s), newest first:"]
    for index, item in enumerate(messages, 1):
        sender = item["from"].split("<")[0].strip().strip('"') or item["from"]
        lines.append(f"{index}. From {sender}: {item['subject']}")
    return ToolOutput(
        text="\n".join(lines),
        data={"messages": messages, "count": len(messages)},
    )


def mail_read(ctx: ToolContext, uid: str) -> ToolOutput:
    config = _config(ctx)
    if config is None:
        return ToolOutput(text=NOT_CONFIGURED, ok=False)

    client = None
    try:
        client = _connect_imap(config)
        status, fetch = client.fetch(str(uid).encode(), "(BODY.PEEK[])")
        if status != "OK" or not fetch:
            return ToolOutput(text=f"I could not open message {uid}.", ok=False)
        raw = fetch[0][1] if isinstance(fetch[0], tuple) else b""
        message = email.message_from_bytes(raw)
    except imaplib.IMAP4.error as exc:
        return ToolOutput(text=f"I could not read that message: {exc}", ok=False)
    except OSError as exc:
        return ToolOutput(text=f"I could not reach the mail server: {exc}", ok=False)
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.logout()

    body = " ".join(_body_text(message).split())
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "..."
    sender = _decode_header(message.get("From"))
    subject = _decode_header(message.get("Subject")) or "(no subject)"
    return ToolOutput(
        text=f"From {sender}, subject '{subject}':\n{body}",
        data={"uid": str(uid), "from": sender, "subject": subject, "body": body},
    )


def mail_send(
    ctx: ToolContext, to: str, subject: str, body: str, cc: str = ""
) -> ToolOutput:
    config = _config(ctx)
    if config is None:
        return ToolOutput(text=NOT_CONFIGURED, ok=False)

    recipients = [addr.strip() for addr in re.split(r"[,;]", to) if addr.strip()]
    if not recipients:
        return ToolOutput(text="I need at least one recipient address.", ok=False)
    invalid = [addr for addr in recipients if not email.utils.parseaddr(addr)[1]]
    if invalid:
        return ToolOutput(text=f"These addresses look wrong: {', '.join(invalid)}", ok=False)

    message = EmailMessage()
    message["From"] = (
        email.utils.formataddr((config["display_name"], config["address"]))
        if config["display_name"]
        else config["address"]
    )
    message["To"] = ", ".join(recipients)
    if cc.strip():
        message["Cc"] = cc.strip()
    message["Subject"] = subject
    message["Date"] = email.utils.formatdate(localtime=True)
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], timeout=30) as server:
            server.login(config["address"], config["password"])
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        return ToolOutput(
            text=(
                f"The mail server rejected the login ({exc.smtp_code}). "
                "Check MAIL_APP_PASSWORD -- it must be an app password."
            ),
            ok=False,
        )
    except (smtplib.SMTPException, OSError) as exc:
        return ToolOutput(text=f"I could not send that mail: {exc}", ok=False)

    return ToolOutput(
        text=f"Sent '{subject}' to {', '.join(recipients)}.",
        data={"to": recipients, "subject": subject, "chars": len(body)},
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="mail_list_unread",
            description=(
                "List unread emails with sender and subject. Does not mark them as read. "
                "Use this before saying anything about the inbox."
            ),
            parameters=object_schema(
                {
                    "limit": param(
                        "limit", "integer", "How many messages to list.",
                        default=10, minimum=1, maximum=30,
                    )
                },
                [],
            ),
            handler=mail_list_unread,
            executing=False,
            category="mail",
        ),
        ToolSpec(
            name="mail_read",
            description="Read the full text of one message by its uid from mail_list_unread.",
            parameters=object_schema(
                {"uid": param("uid", "string", "Message uid from mail_list_unread.")}, ["uid"]
            ),
            handler=mail_read,
            executing=False,
            category="mail",
        ),
        ToolSpec(
            name="mail_send",
            description=(
                "Send an email on the master's behalf. Needs permission -- always read "
                "the recipient and subject back before calling this."
            ),
            parameters=object_schema(
                {
                    "to": param("to", "string", "Recipient address, or several separated by commas."),
                    "subject": param("subject", "string", "Subject line."),
                    "body": param("body", "string", "Plain text body."),
                    "cc": param("cc", "string", "Optional cc addresses.", default=""),
                },
                ["to", "subject", "body"],
            ),
            handler=mail_send,
            executing=True,
            category="mail",
        ),
    ]

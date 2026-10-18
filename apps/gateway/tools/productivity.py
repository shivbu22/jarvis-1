"""Productivity, Document & System Automation Tools for J.A.R.V.I.S.

Provides document text extraction (PDF, Markdown, CSV), professional invoice generation,
dataset validation and cleaning, business expense tracking, and local cron task scheduling.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, integer_param, object_schema, param

logger = logging.getLogger(__name__)


def document_extract_text(ctx: ToolContext, file_path: str, max_chars: int = 8000) -> ToolOutput:
    """Extract and summarize text contents from PDF, CSV, JSON, Markdown, or text documents."""
    p = Path(file_path).resolve()
    if not p.exists():
        return ToolOutput(text=f"File not found: {file_path}", ok=False)

    ext = p.suffix.lower()
    text_content = ""

    try:
        if ext == ".pdf":
            # Native binary string and stream extraction without external dependencies
            raw_bytes = p.read_bytes()
            # Simple text extraction from PDF stream objects
            stream_matches = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", raw_bytes, re.DOTALL)
            extracted_chunks = []
            for stream in stream_matches:
                import zlib
                try:
                    decompressed = zlib.decompress(stream)
                    # Extract text inside parentheses in Tj or TJ PDF operators
                    strings = re.findall(rb"\((.*?)\)\s*Tj", decompressed)
                    if strings:
                        extracted_chunks.extend(s.decode("latin1", errors="replace") for s in strings)
                except Exception:
                    pass
            if extracted_chunks:
                text_content = "\n".join(extracted_chunks)
            else:
                # Fallback: extract printable ASCII
                text_content = "".join(chr(b) if 32 <= b <= 126 or b in (10, 13, 9) else " " for b in raw_bytes[:max_chars * 2])
        elif ext in {".csv", ".tsv"}:
            raw = p.read_text(encoding="utf-8", errors="replace")
            reader = csv.reader(io.StringIO(raw))
            rows = list(reader)
            header = rows[0] if rows else []
            text_content = f"CSV Table ({len(rows)} rows, {len(header)} columns):\nColumns: {', '.join(header)}\n"
            for r in rows[1:15]:
                text_content += f"  - {' | '.join(r)}\n"
            if len(rows) > 15:
                text_content += f"  ... ({len(rows) - 15} additional rows truncated)"
        elif ext == ".json":
            raw = p.read_text(encoding="utf-8", errors="replace")
            parsed = json.loads(raw)
            text_content = json.dumps(parsed, indent=2)
        else:
            text_content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return ToolOutput(text=f"Failed to extract document '{file_path}': {exc}", ok=False)

    trimmed = text_content[:max_chars]
    is_truncated = len(text_content) > max_chars
    report = (
        f"Extracted Document: {p.name} ({len(text_content)} characters)\n"
        f"--------------------------------------------------\n"
        f"{trimmed}\n"
        f"--------------------------------------------------"
    )
    if is_truncated:
        report += f"\n[Truncated to {max_chars} characters]"

    return ToolOutput(text=report, data={"path": str(p), "size_chars": len(text_content), "truncated": is_truncated}, ok=True)


def document_generate_invoice(
    ctx: ToolContext,
    client_name: str,
    items: str,
    invoice_num: str = "",
    currency: str = "USD",
    tax_rate: float = 0.0,
) -> ToolOutput:
    """Generate an itemized invoice document and record it in the persistent invoice ledger."""
    inv_num = invoice_num.strip() or f"INV-{time.strftime('%Y%m%d')}-{int(time.time()) % 10000:04d}"
    curr = currency.strip().upper()

    parsed_items: list[dict[str, Any]] = []
    if items.strip().startswith("["):
        try:
            parsed_items = json.loads(items)
        except Exception:
            parsed_items = []

    if not parsed_items:
        # Fallback string format parsing: "Item:Qty:Rate;Item2:Qty:Rate"
        for chunk in items.split(";"):
            if not chunk.strip():
                continue
            parts = [p.strip() for p in chunk.split(":")]
            desc = parts[0]
            qty = float(parts[1]) if len(parts) > 1 else 1.0
            rate = float(parts[2]) if len(parts) > 2 else 100.0
            parsed_items.append({"description": desc, "quantity": qty, "rate": rate, "amount": qty * rate})
    else:
        for it in parsed_items:
            it["amount"] = float(it.get("quantity", 1)) * float(it.get("rate", 0))

    subtotal = sum(float(it.get("amount", 0.0)) for it in parsed_items)
    tax_amount = subtotal * (float(tax_rate) / 100.0)
    total = subtotal + tax_amount

    # Save to SQLite ledger
    rec = ctx.memory.create_invoice(
        invoice_num=inv_num,
        client_name=client_name,
        total_amount=total,
        currency=curr,
        items=parsed_items,
        status="issued",
    )

    # Generate Markdown Invoice Document
    inv_dir = Path("data/invoices")
    inv_dir.mkdir(parents=True, exist_ok=True)
    file_path = inv_dir / f"{inv_num}.md"

    md_lines = [
        f"# INVOICE: {inv_num}",
        f"**Date:** {time.strftime('%Y-%m-%d')}",
        f"**Client:** {client_name}",
        "**Status:** ISSUED",
        "",
        "| Description | Quantity | Rate | Amount |",
        "|---|:---:|:---:|:---:|",
    ]
    for it in parsed_items:
        md_lines.append(f"| {it['description']} | {it['quantity']} | {curr} {it['rate']:,.2f} | {curr} {it['amount']:,.2f} |")

    md_lines.extend([
        "",
        f"**Subtotal:** {curr} {subtotal:,.2f}",
        f"**Tax ({tax_rate}%):** {curr} {tax_amount:,.2f}",
        f"### Total Due: {curr} {total:,.2f}",
    ])

    file_path.write_text("\n".join(md_lines), encoding="utf-8")

    report = (
        f"Invoice {inv_num} Generated & Recorded:\n"
        f"Client: {client_name}\n"
        f"Total: {curr} {total:,.2f} (Items: {len(parsed_items)})\n"
        f"Document saved: {file_path}"
    )
    return ToolOutput(text=report, data={"invoice_id": rec["id"], "invoice_num": inv_num, "total": total, "path": str(file_path)}, ok=True)


def data_validate_clean(ctx: ToolContext, content: str, format: str = "csv") -> ToolOutput:
    """Validate, clean, deduplicate, and audit structured CSV or JSON data."""
    fmt = format.strip().lower()
    raw = content.strip()

    if fmt == "json":
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                initial_count = len(data)
                # Deduplicate dicts
                unique = []
                seen = set()
                for item in data:
                    item_str = json.dumps(item, sort_keys=True)
                    if item_str not in seen:
                        seen.add(item_str)
                        unique.append(item)
                cleaned_str = json.dumps(unique, indent=2)
                return ToolOutput(
                    text=f"JSON Dataset Cleaned: {len(unique)} valid records (removed {initial_count - len(unique)} duplicates).",
                    data={"format": "json", "records": len(unique), "duplicates_removed": initial_count - len(unique), "cleaned": cleaned_str[:2000]},
                    ok=True,
                )
            return ToolOutput(text="JSON object validated successfully (schema valid).", data={"format": "json", "valid": True}, ok=True)
        except Exception as exc:
            return ToolOutput(text=f"JSON validation failed: {exc}", ok=False)

    # CSV Processing
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return ToolOutput(text="Provided CSV data is empty.", ok=False)

    header = lines[0]
    col_count = len(header.split(","))

    seen_rows = set()
    cleaned_rows = [header]
    dropped_duplicates = 0
    dropped_malformed = 0

    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != col_count:
            dropped_malformed += 1
            continue
        if line in seen_rows:
            dropped_duplicates += 1
            continue
        seen_rows.add(line)
        cleaned_rows.append(line)

    result_csv = "\n".join(cleaned_rows)
    report = (
        f"CSV Validation & Cleansing Report:\n"
        f"Total Input Rows: {len(lines)}\n"
        f"Valid Cleaned Rows: {len(cleaned_rows)}\n"
        f"Duplicates Dropped: {dropped_duplicates}\n"
        f"Malformed Rows Dropped: {dropped_malformed}\n"
        f"Columns ({col_count}): {header}"
    )

    return ToolOutput(
        text=report,
        data={"total_rows": len(lines), "cleaned_rows": len(cleaned_rows), "duplicates": dropped_duplicates, "malformed": dropped_malformed, "csv_preview": result_csv[:500]},
        ok=True,
    )


def expense_track(
    ctx: ToolContext,
    category: str,
    amount: float,
    vendor: str = "",
    description: str = "",
    currency: str = "USD",
) -> ToolOutput:
    """Log a business expense and retrieve updated categorized spending totals."""
    cat = category.strip().lower()
    amt = float(amount)
    if amt <= 0:
        return ToolOutput(text="Expense amount must be greater than zero.", ok=False)

    exp_id = ctx.memory.add_expense(
        category=cat,
        amount=amt,
        vendor=vendor,
        description=description,
        currency=currency,
    )

    summary = ctx.memory.get_expense_summary()
    cat_spend = summary["by_category"].get(cat, {}).get("total", amt)

    report = (
        f"Logged Expense #{exp_id}: ${amt:,.2f} {currency.upper()} under '{cat}'\n"
        f"Vendor: {vendor or 'Unspecified'} | Details: {description or 'N/A'}\n"
        f"Total Category Spending for '{cat}': ${cat_spend:,.2f}\n"
        f"All-Time Grand Total Recorded: ${summary['grand_total']:,.2f} ({summary['total_expenses']} transactions)"
    )
    return ToolOutput(text=report, data={"expense_id": exp_id, "category": cat, "amount": amt, "summary": summary}, ok=True)


def cron_task_schedule(
    ctx: ToolContext,
    name: str,
    interval_minutes: int,
    tool_name: str,
    args: str = "{}",
) -> ToolOutput:
    """Schedule or inspect an automated recurring background task."""
    n = name.strip()
    interval = max(1, int(interval_minutes))
    tname = tool_name.strip()

    parsed_args = {}
    if args.strip():
        try:
            parsed_args = json.loads(args)
        except Exception:
            parsed_args = {"raw": args}

    cron_rec = ctx.memory.add_scheduled_cron(
        name=n,
        interval_m=interval,
        tool_name=tname,
        args=parsed_args,
    )

    all_crons = ctx.memory.list_scheduled_crons()
    lines = [
        f"Recurring Cron Job '{n}' Scheduled Successfully:\n"
        f"Execution Cadence: Every {interval} minute(s)\n"
        f"Target Tool: {tname}\n"
        f"Arguments: {json.dumps(parsed_args)}\n",
        f"Active Scheduled Tasks ({len(all_crons)} registered):",
    ]
    for c in all_crons:
        lines.append(f"  * [{c['name']}] runs '{c['tool_name']}' every {c['interval_m']}m (Status: {c['status'].upper()})")

    return ToolOutput(text="\n".join(lines), data=cron_rec, ok=True)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="document_extract_text",
            description="Extract text content, tables, and metadata from PDF, CSV, JSON, Markdown, and text files.",
            parameters=object_schema(
                properties={
                    "file_path": param("string", "Absolute or relative file path"),
                    "max_chars": integer_param("Maximum characters to return", default=8000),
                },
                required=["file_path"],
            ),
            handler=document_extract_text,
            executing=False,
            category="productivity",
        ),
        ToolSpec(
            name="document_generate_invoice",
            description="Generate a professional invoice markdown file and record it in the persistent accounting ledger.",
            parameters=object_schema(
                properties={
                    "client_name": param("string", "Client or enterprise name"),
                    "items": param("string", "Item definitions formatted as JSON array or 'Desc:Qty:Rate;...' string"),
                    "invoice_num": param("string", "Optional custom invoice number", default=""),
                    "currency": param("string", "Currency code (USD, INR, EUR)", default="USD"),
                    "tax_rate": param("number", "Tax percentage (e.g. 18.0 for 18% GST)", default=0.0),
                },
                required=["client_name", "items"],
            ),
            handler=document_generate_invoice,
            executing=True,
            category="productivity",
        ),
        ToolSpec(
            name="data_validate_clean",
            description="Validate, clean, deduplicate, and audit structured CSV or JSON datasets.",
            parameters=object_schema(
                properties={
                    "content": param("string", "Raw CSV or JSON text data to validate and clean"),
                    "format": param("string", "Format type ('csv' or 'json')", default="csv"),
                },
                required=["content"],
            ),
            handler=data_validate_clean,
            executing=False,
            category="productivity",
        ),
        ToolSpec(
            name="expense_track",
            description="Log business expenses, track categories, and report aggregate business expenditures.",
            parameters=object_schema(
                properties={
                    "category": param("string", "Expense category (e.g. cloud, marketing, hardware, travel)"),
                    "amount": param("number", "Cost amount"),
                    "vendor": param("string", "Vendor or merchant name", default=""),
                    "description": param("string", "Details regarding the expense", default=""),
                    "currency": param("string", "Currency (USD, INR, EUR)", default="USD"),
                },
                required=["category", "amount"],
            ),
            handler=expense_track,
            executing=True,
            category="productivity",
        ),
        ToolSpec(
            name="cron_task_schedule",
            description="Schedule or inspect local automated recurring tasks executed at a designated minute interval.",
            parameters=object_schema(
                properties={
                    "name": param("string", "Unique job identifier"),
                    "interval_minutes": integer_param("Frequency in minutes"),
                    "tool_name": param("string", "Name of tool to execute"),
                    "args": param("string", "JSON encoded arguments for tool execution", default="{}"),
                },
                required=["name", "interval_minutes", "tool_name"],
            ),
            handler=cron_task_schedule,
            executing=True,
            category="productivity",
        ),
    ]

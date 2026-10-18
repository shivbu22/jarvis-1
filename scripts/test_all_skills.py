"""Audit and verify all 87 tools/skills in J.A.R.V.I.S."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.gateway.audit import AuditLog
from apps.gateway.config import load_settings
from apps.gateway.events import EventBus
from apps.gateway.memory import MemoryStore
from apps.gateway.policy import Policy
from apps.gateway.tools import build_registry
from apps.gateway.tools.registry import ToolContext


def main() -> int:
    settings = load_settings()
    policy = Policy.load(settings.policy_path)
    memory = MemoryStore(settings.db_path)
    audit = AuditLog(settings.audit_path, True)
    bus = EventBus()
    ctx = ToolContext(
        settings=settings,
        memory=memory,
        policy=policy,
        audit=audit,
        bus=bus,
    )
    registry = build_registry(policy, ctx)
    tools = registry.specs()

    print("\n=======================================================")
    print(f"J.A.R.V.I.S. SKILL & TOOL AUDIT: {len(tools)} REGISTERED SKILLS")
    print("=======================================================\n")

    categories: dict[str, list] = {}
    for spec in tools:
        categories.setdefault(spec.category, []).append(spec)

    total_passed = 0
    total_tested = 0

    for cat, cat_tools in sorted(categories.items()):
        print(f"\n--- Category: {cat.upper()} ({len(cat_tools)} skills) ---")
        for spec in cat_tools:
            total_tested += 1
            # 1. Check schema
            has_func = callable(spec.handler)
            schema_ok = bool(spec.parameters.get("type") == "object")
            tier = spec.tier

            status = "OK" if (has_func and schema_ok) else "ERR"
            print(f"  [{status}] {spec.name:26} tier={tier:8} exec={spec.executing!s:5} params={list(spec.parameters.get('properties', {}).keys())}")

            if not (has_func and schema_ok):
                print(f"       FAIL: handler={has_func}, schema={schema_ok}")
            else:
                total_passed += 1

    # Now execute a sample of core auto-tier tools to verify live execution
    print("\n--- Live Tool Execution Verification ---")
    live_checks = [
        ("system_info", {}),
        ("memory_list", {"limit": 3}),
        ("audit_tail", {"limit": 5}),
        ("brain_vault_lint", {}),
        ("subagent_list", {}),
        ("speaker_list", {}),
        ("check_portfolio", {}),
        ("lead_pipeline_list", {"limit": 5}),
        ("load_history", {"limit": 5}),
        ("daily_executive_briefing", {}),
        ("dns_mx_check", {"domain_or_email": "google.com"}),
        ("technical_indicators_calc", {"symbol": "^NSEI"}),
        ("nlp_sentiment_analyze", {"text": "Exceptional growth and wonderful profit"}),
        ("dataset_stats_analyze", {"data_values": "10, 20, 30, 40, 50"}),
    ]

    live_passed = 0
    for name, args in live_checks:
        spec = registry.get(name)
        if not spec:
            print(f"  [FAIL] {name}: tool not found in registry")
            continue
        try:
            t0 = time.perf_counter()
            out = spec.handler(ctx, **args)
            dur = (time.perf_counter() - t0) * 1000
            print(f"  [PASS] {name:20} returned in {dur:5.1f}ms: ok={out.ok} text_len={len(out.text)}")
            live_passed += 1
        except Exception as exc:
            print(f"  [FAIL] {name:20} raised {type(exc).__name__}: {exc}")

    print("\n=======================================================")
    print(f"Audit Summary: {total_passed}/{total_tested} tool definitions valid.")
    print(f"Live execution: {live_passed}/{len(live_checks)} sample skills executed successfully.")
    print("=======================================================\n")

    return 0 if (total_passed == total_tested and live_passed == len(live_checks)) else 1


if __name__ == "__main__":
    sys.exit(main())

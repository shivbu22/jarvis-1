"""Latency & Throughput Verification Benchmark for J.A.R.V.I.S."""

import json
import time
import urllib.request


def test_turn(msg: str):
    payload = json.dumps({"text": msg, "speak": False}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/v1/turn/text",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    t1 = time.perf_counter()
    wall_ms = round((t1 - t0) * 1000)

    turn = data.get("turn", {})
    stages = data.get("stages", {})

    print(f"--- Prompt: '{msg}' ---")
    print(f"Total Wall Latency: {wall_ms} ms")
    print(f"Brain Think Stage:  {stages.get('think')} s")
    print(f"Gateway Duration:   {turn.get('duration_ms')} ms")
    print(f"Prompt Eval Count:  {turn.get('prompt_eval_count')}")
    print(f"Output Eval Count:  {turn.get('eval_count')}")
    print(f"Generation Speed:   {turn.get('tokens_per_second')} tokens/sec")
    print(f"Active Brain:       {turn.get('brain')} ({turn.get('resolved_model')})")
    print(f"Reply:              {data.get('reply')}")
    print()
    return wall_ms


if __name__ == "__main__":
    print("========================================")
    print("  J.A.R.V.I.S. REAL-TIME LATENCY BENCHMARK")
    print("========================================\n")

    print("[1/2] Turn 1: Cold/First Query")
    t1 = test_turn("Hello Jarvis, state your operational readiness.")

    print("[2/2] Turn 2: Warm/KV-Cache Hit Query")
    t2 = test_turn("What is your current system status?")

    print("========================================")
    print(f"Latency Result: Turn 1 = {t1}ms | Turn 2 (KV Cache) = {t2}ms")
    print("========================================")

"""Quick dump of every registered tool name + its parameter names."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os
os.chdir(ROOT)

from apps.gateway.audit import AuditLog
from apps.gateway.config import load_settings
from apps.gateway.events import EventBus
from apps.gateway.memory import MemoryStore
from apps.gateway.policy import Policy
from apps.gateway.tools import build_registry
from apps.gateway.tools.registry import ToolContext

settings = load_settings()
policy = Policy.load(settings.policy_path)
memory = MemoryStore(settings.db_path)
audit = AuditLog(settings.audit_path, True)
bus = EventBus()
ctx = ToolContext(settings=settings, memory=memory, policy=policy, audit=audit, bus=bus)
registry = build_registry(policy, ctx)

for spec in sorted(registry.specs(), key=lambda s: (s.category, s.name)):
    props = spec.parameters.get("properties", {})
    required = spec.parameters.get("required", [])
    params = []
    for pname, pschema in props.items():
        r = "*" if pname in required else ""
        default = pschema.get("default", "")
        params.append(f"{pname}{r}({pschema.get('type','?')}, default={default!r})")
    print(f"{spec.category:14} {spec.name:34} exec={spec.executing!s:5} params=[{', '.join(params)}]")

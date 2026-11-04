"""Gateway-wide policy (disabled tools, read-only mode, host allowlist) and the audit log."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from .config import Backend, GatewayConfig

SECRET_KEY = re.compile(r"pass|pwd|secret|token|credential|api_?key", re.I)
MAX_LOGGED_STR = 400


class PolicyDenied(ToolError):
    """Raised by the gateway itself, so the audit log can tell a denial from a backend error."""


class Policy:
    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg
        # Longest prefix first so "sw" never shadows a hypothetical "swx".
        self._backends = sorted(cfg.backends, key=lambda b: len(b.prefix), reverse=True)
        self._read_only_hint: dict[str, bool] = {}

    def backend_for(self, public_name: str) -> Backend | None:
        for b in self._backends:
            if public_name.startswith(f"{b.prefix}_"):
                return b
        return None

    def is_read_only(self, backend: Backend) -> bool:
        return self.cfg.read_only or backend.read_only

    def _readonly_allowed(self, backend: Backend, public_name: str) -> bool:
        return self._read_only_hint.get(public_name, False) or backend.original_name(public_name) in backend.read_only_tools

    def visible(self, tool: Any) -> bool:
        backend = self.backend_for(tool.name)
        if backend is None:
            return True
        hint = bool(tool.annotations and tool.annotations.readOnlyHint)
        self._read_only_hint[tool.name] = hint
        if backend.original_name(tool.name) in backend.disabled_tools:
            return False
        return not self.is_read_only(backend) or self._readonly_allowed(backend, tool.name)

    def check_call(self, public_name: str, args: dict[str, Any]) -> None:
        backend = self.backend_for(public_name)
        if backend is None:
            return
        original = backend.original_name(public_name)
        if original in backend.disabled_tools:
            raise PolicyDenied(f"{public_name} is disabled in the infra gateway config")
        if self.is_read_only(backend) and not self._readonly_allowed(backend, public_name):
            raise PolicyDenied(f"{public_name} is blocked: backend {backend.name!r} is in read-only mode")
        for arg in backend.host_args:
            host = args.get(arg)
            if host and str(host).strip().lower() not in backend.allowed_hosts:
                raise PolicyDenied(
                    f"{public_name}: host {host!r} is not in the allowed_hosts list for backend {backend.name!r}"
                )


class PolicyMiddleware(Middleware):
    def __init__(self, policy: Policy):
        self.policy = policy

    async def on_list_tools(self, context, call_next) -> Sequence[Any]:
        tools = await call_next(context)
        return [t for t in tools if self.policy.visible(t)]

    async def on_call_tool(self, context, call_next):
        self.policy.check_call(context.message.name, context.message.arguments or {})
        return await call_next(context)


def redact(value: Any, key: str = "") -> Any:
    if key and SECRET_KEY.search(key):
        return "***"
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and len(value) > MAX_LOGGED_STR:
        return value[:MAX_LOGGED_STR] + f"...(+{len(value) - MAX_LOGGED_STR})"
    return value


class AuditMiddleware(Middleware):
    """Append one JSON line per tool call: who was called, with what, and how it ended."""

    def __init__(self, path: Path, policy: Policy):
        self.path = path
        self.policy = policy
        path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, record: dict[str, Any]) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        backend = self.policy.backend_for(name)
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": name,
            "backend": backend.name if backend else "gateway",
            "args": redact(context.message.arguments or {}),
        }
        start = time.monotonic()
        try:
            result = await call_next(context)
        except PolicyDenied as e:
            record.update(outcome="denied", error=str(e)[:MAX_LOGGED_STR])
            raise
        except Exception as e:
            record.update(outcome="error", error=f"{type(e).__name__}: {e}"[:MAX_LOGGED_STR])
            raise
        else:
            record["outcome"] = "error" if getattr(result, "is_error", False) else "ok"
            return result
        finally:
            record["ms"] = int((time.monotonic() - start) * 1000)
            self._write(record)

from __future__ import annotations

import re
from typing import Any

from trendforge.models import utcnow

_SECRET_KEYS = ("api_key", "apikey", "authorization", "secret", "token", "password", "cursor_api")
_SECRET_TEXT = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{8,}|comfyui-[A-Za-z0-9_-]{16,}|"
    r"(?:Bearer|X-API-Key)\s+\S+|CURSOR_API_KEY\s*=\s*\S+|COMFY_API_KEY\s*=\s*\S+)"
)


def redact_text(value: str) -> str:
    return _SECRET_TEXT.sub("[redacted]", value)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SECRET_KEYS):
                out[key] = "[redacted]"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        if len(value) > 24 and value.startswith(("sk-", "AIza", "comfyui-")):
            return "[redacted]"
        return redact_text(value)
    return value


def append_event(job, name: str, detail: str | None = None) -> None:
    meta = dict(job.job_metadata_json or {})
    events = list(meta.get("events") or [])
    events.append(
        {
            "at": utcnow().isoformat(),
            "event": name,
            "detail": None if detail is None else str(redact(detail))[:500],
        }
    )
    meta["events"] = events[-80:]
    job.job_metadata_json = meta
    job.updated_at = utcnow()

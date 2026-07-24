#!/usr/bin/env python3
"""Execute explicitly marked Python snippets from user documentation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKED_BLOCK = re.compile(
    r"<!--\s*doc-test:\s*([a-z0-9-]+)\s*-->\s*"
    r"```python\s*\n(.*?)```",
    re.DOTALL,
)


def _exercise_webhook_handler(namespace: dict[str, object]) -> None:
    handler = namespace["handle_webhook"]
    assert callable(handler)

    secret = "test-secret"
    message_id = "msg_docs"
    timestamp = str(int(time.time()))
    body = json.dumps(
        {
            "object": "event",
            "id": "evt_docs",
            "type": "transcription.completed",
            "created_at": int(timestamp),
            "data": {"transcription_id": "550e8400-e29b-41d4-a716-446655440000"},
        },
        separators=(",", ":"),
    ).encode()
    signed = f"{message_id}.{timestamp}.{body.decode()}".encode()
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    headers = {
        "webhook-id": message_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{base64.b64encode(digest).decode()}",
    }

    status, response = handler(headers, body, secret)  # type: ignore[operator]
    assert (status, response) == (200, "ok")


EXERCISES = {
    "webhook-handler": _exercise_webhook_handler,
}


def main() -> int:
    count = 0
    for path in sorted((ROOT / "docs").rglob("*.md")):
        text = path.read_text()
        for match in MARKED_BLOCK.finditer(text):
            name, source = match.groups()
            if name not in EXERCISES:
                raise RuntimeError(f"{path}: unknown doc-test {name!r}")
            namespace: dict[str, object] = {}
            code = compile(source, f"{path}:{name}", "exec")
            exec(code, namespace)
            EXERCISES[name](namespace)
            print(f"doc snippet: {path.relative_to(ROOT)}:{name}: ok")
            count += 1

    if count == 0:
        raise RuntimeError("no marked documentation snippets found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

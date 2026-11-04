"""Stdlib JSON POST with a hard timeout. Returns the decoded JSON object, or None on any failure."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def post_json(url: str, headers: dict, body: dict, timeout_s: float):
    data = json.dumps(body, sort_keys=True).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"content-type": "application/json", "accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            if r.status // 100 != 2:
                return None
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

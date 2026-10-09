# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Small HTTP helper with on-disk JSON caching."""
from __future__ import annotations

import hashlib
import json
import logging
import time

import requests

from app.config import CACHE_DIR, HTTP_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _key(method, url, params, data):
    raw = json.dumps([method, url, params, data], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def get_json(url, params=None, method="GET", data=None, cache=True, max_age_s=30 * 86400,
             retries=2, timeout=HTTP_TIMEOUT):
    path = CACHE_DIR / "http" / (_key(method, url, params, data) + ".json")
    if cache and path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    last = None
    for attempt in range(retries + 1):
        try:
            if method == "GET":
                r = _session.get(url, params=params, timeout=timeout)
            else:
                r = _session.post(url, params=params, data=data, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            js = r.json()
            if cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(js))
            return js
        except Exception as exc:
            last = exc
            log.info("get_json attempt %d failed for %s: %s", attempt + 1, url, exc)
            time.sleep(1.5 * (attempt + 1))
    raise last

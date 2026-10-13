"""Delivers outbox rows to MPM and decides what to do with each HTTP result."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable

from outbox import Outbox

log = logging.getLogger("sender")
EVENT_BATCH = 50
SUMMARY_PATH = "/api/libre-solar-bms/ingest/summary"
EVENTS_PATH = "/api/libre-solar-bms/ingest/events"

# A transport takes (url, api_key, body) and returns the HTTP status, or None if unreachable.
Transport = Callable[[str, str, dict], "int | None"]


def http_post(url: str, api_key: str, body: dict) -> int | None:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


class Sender:
    def __init__(self, outbox: Outbox, base_url: str, api_key: str, transport: Transport = http_post):
        self.outbox, self.base_url, self.api_key, self.transport = outbox, base_url.rstrip("/"), api_key, transport
        self.last_success: float | None = None
        self.last_status: int | None = None

    def run_once(self) -> bool:
        """Try everything that is due. Returns True if any row was acknowledged or dropped."""
        if not self.api_key:
            return False
        progressed = False
        for row_id, summary in self.outbox.due("summary", limit=100):
            progressed |= self._deliver([row_id], SUMMARY_PATH, summary)
        events = self.outbox.due("event", limit=EVENT_BATCH)
        if events:
            progressed |= self._deliver([row_id for row_id, _ in events], EVENTS_PATH,
                                        {"events": [event for _, event in events]})
        return progressed

    def _deliver(self, ids: list[int], path: str, body: dict) -> bool:
        status = self.transport(self.base_url + path, self.api_key, body)
        self.last_status = status
        if status is not None and 200 <= status < 300:
            self.outbox.done(ids)
            return True
        if status in (400, 422):
            # MPM understood us and rejected the data: retrying can never succeed.
            log.error("MPM rejected %s (HTTP %s); dropping %d row(s)", path, status, len(ids))
            self.outbox.done(ids)
            return True
        # Unreachable, 401/403 (bad key), 404 (plugin not enabled yet), 429, 5xx: keep and retry.
        log.warning("MPM delivery failed for %s (status=%s); will retry", path, status)
        self.outbox.failed(ids)
        return False

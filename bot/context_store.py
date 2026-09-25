"""
Context Store — In-memory store for all 4 context types.
Handles idempotent versioned upserts and cross-context lookups.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Optional


class ContextStore:
    """
    Thread-safe in-memory store keyed by (scope, context_id).
    Each entry tracks version + payload. Higher versions replace atomically.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # (scope, context_id) -> {"version": int, "payload": dict, "stored_at": str}
        self._store: dict[tuple[str, str], dict[str, Any]] = {}
        self._start_time = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Core upsert — returns (accepted: bool, ack_id | None, reason | None, current_version | None)
    # ------------------------------------------------------------------
    def upsert(
        self, scope: str, context_id: str, version: int, payload: dict
    ) -> tuple[bool, Optional[str], Optional[str], Optional[int]]:
        valid_scopes = {"category", "merchant", "customer", "trigger"}
        if scope not in valid_scopes:
            return False, None, "invalid_scope", None

        key = (scope, context_id)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._lock:
            existing = self._store.get(key)

            # Idempotent: same or older version is a no-op
            if existing and existing["version"] >= version:
                return False, None, "stale_version", existing["version"]

            self._store[key] = {
                "version": version,
                "payload": payload,
                "stored_at": now_iso,
            }
            ack_id = f"ack_{context_id}_v{version}"
            return True, ack_id, None, None

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def get(self, scope: str, context_id: str) -> Optional[dict]:
        """Return the payload for a given (scope, context_id), or None."""
        with self._lock:
            entry = self._store.get((scope, context_id))
            return entry["payload"] if entry else None

    def get_merchant(self, merchant_id: str) -> Optional[dict]:
        return self.get("merchant", merchant_id)

    def get_category(self, slug: str) -> Optional[dict]:
        return self.get("category", slug)

    def get_trigger(self, trigger_id: str) -> Optional[dict]:
        return self.get("trigger", trigger_id)

    def get_customer(self, customer_id: str) -> Optional[dict]:
        return self.get("customer", customer_id)

    def get_category_for_merchant(self, merchant_id: str) -> Optional[dict]:
        """Resolve merchant -> category_slug -> CategoryContext."""
        merchant = self.get_merchant(merchant_id)
        if not merchant:
            return None
        slug = merchant.get("category_slug", "")
        return self.get_category(slug)

    # ------------------------------------------------------------------
    # Counts for /healthz
    # ------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        with self._lock:
            result = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
            for (scope, _) in self._store:
                result[scope] = result.get(scope, 0) + 1
            return result

    # ------------------------------------------------------------------
    # Uptime
    # ------------------------------------------------------------------
    def uptime_seconds(self) -> int:
        delta = datetime.now(timezone.utc) - self._start_time
        return int(delta.total_seconds())

    # ------------------------------------------------------------------
    # Bulk helpers
    # ------------------------------------------------------------------
    def all_triggers(self) -> list[tuple[str, dict]]:
        """Return all stored triggers as [(trigger_id, payload), ...]."""
        with self._lock:
            return [
                (cid, entry["payload"])
                for (scope, cid), entry in self._store.items()
                if scope == "trigger"
            ]

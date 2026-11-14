"""Optional trusted-document transaction state on the native SessionDB write boundary."""
import json
import re
import secrets
from typing import Any, Dict, Optional, Tuple


class SessionArtifactsMixin:
    _ARTIFACT_DELIVERY_STATES = frozenset(
        {"pending", "ready", "delivery_started", "delivered", "uncertain", "abandoned"}
    )
    _ARTIFACT_DELIVERY_TERMINAL_STATES = frozenset(
        {"delivered", "uncertain", "abandoned"}
    )
    _ARTIFACT_DELIVERY_TRANSITIONS = frozenset(
        {
            ("pending", "ready"),
            ("pending", "uncertain"),
            ("pending", "abandoned"),
            ("ready", "delivery_started"),
            ("ready", "uncertain"),
            ("ready", "abandoned"),
            ("delivery_started", "delivered"),
            ("delivery_started", "uncertain"),
        }
    )
    _ARTIFACT_TRANSACTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

    @classmethod
    def _decode_artifact_delivery(
        cls, raw: Any
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        if raw is None:
            return None, True
        malformed = {"state": "uncertain", "malformed": True}
        if not isinstance(raw, str):
            return malformed, False
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return malformed, False
        if not isinstance(value, dict):
            return malformed, False

        state = value.get("state")
        if not isinstance(state, str) or state not in cls._ARTIFACT_DELIVERY_STATES:
            return malformed, False

        if state == "uncertain" and set(value) == {"state", "malformed"}:
            if value["malformed"] is not True:
                return malformed, False
            if raw != cls._encode_artifact_delivery(malformed):
                return malformed, False
            return malformed, True

        expected_keys = {"state", "transaction_id"}
        if state == "delivered":
            expected_keys.add("receipt_id")
        if set(value) != expected_keys:
            return malformed, False

        transaction_id = value["transaction_id"]
        if not isinstance(transaction_id, str) or not cls._ARTIFACT_TRANSACTION_ID_RE.fullmatch(
            transaction_id
        ):
            return malformed, False
        result: Dict[str, Any] = {
            "state": state,
            "transaction_id": transaction_id,
        }
        if state == "delivered":
            receipt_id = value["receipt_id"]
            if not isinstance(receipt_id, str) or not receipt_id:
                return malformed, False
            result["receipt_id"] = receipt_id
        if raw != cls._encode_artifact_delivery(result):
            return malformed, False
        return result, True

    @staticmethod
    def _encode_artifact_delivery(value: Dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _artifact_column_exists(conn):
        return any(row[1] == "artifact_delivery_json" for row in conn.execute("PRAGMA table_info(sessions)"))

    def begin_artifact_delivery(self, session_id: str) -> Optional[str]:
        """Durably mint one opaque transaction without replacing live work."""
        if not session_id:
            return None

        def _do(conn):
            # Optional extension: keep native read-only stores without this column readable.
            if not self._artifact_column_exists(conn):
                conn.execute("ALTER TABLE sessions ADD COLUMN artifact_delivery_json TEXT")
            row = conn.execute(
                "SELECT artifact_delivery_json FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            raw = row["artifact_delivery_json"]
            current, raw_is_valid = self._decode_artifact_delivery(raw)
            if raw is not None and not raw_is_valid:
                conn.execute(
                    "UPDATE sessions SET artifact_delivery_json = ? WHERE id = ?",
                    (
                        self._encode_artifact_delivery(current),
                        session_id,
                    ),
                )
                return None
            if current is not None:
                if current["state"] not in self._ARTIFACT_DELIVERY_TERMINAL_STATES:
                    if current["state"] == "pending":
                        return current["transaction_id"]
                    return None
            transaction_id = secrets.token_urlsafe(32)
            pending = {"state": "pending", "transaction_id": transaction_id}
            conn.execute(
                "UPDATE sessions SET artifact_delivery_json = ? WHERE id = ?",
                (self._encode_artifact_delivery(pending), session_id),
            )
            return transaction_id

        return self._execute_write(_do)

    def transition_artifact_delivery(
        self,
        session_id: str,
        transaction_id: str,
        expected_state: str,
        new_state: str,
        *,
        receipt_id: Optional[str] = None,
    ) -> bool:
        """CAS one exact session+opaque-transaction artifact transition."""
        if (
            not session_id
            or not isinstance(transaction_id, str)
            or not self._ARTIFACT_TRANSACTION_ID_RE.fullmatch(transaction_id)
            or (expected_state, new_state) not in self._ARTIFACT_DELIVERY_TRANSITIONS
            or (new_state == "delivered" and (not isinstance(receipt_id, str) or not receipt_id))
            or (new_state != "delivered" and receipt_id is not None)
        ):
            return False

        def _do(conn):
            if not self._artifact_column_exists(conn):
                return False
            expected = {"state": expected_state, "transaction_id": transaction_id}
            updated = {"state": new_state, "transaction_id": transaction_id}
            if receipt_id is not None:
                updated["receipt_id"] = receipt_id
            result = conn.execute(
                """UPDATE sessions SET artifact_delivery_json = ?
                   WHERE id = ? AND artifact_delivery_json = ?""",
                (
                    self._encode_artifact_delivery(updated),
                    session_id,
                    self._encode_artifact_delivery(expected),
                ),
            )
            return result.rowcount == 1

        return bool(self._execute_write(_do))

    @staticmethod
    def _artifact_delivery_lineage_rows(conn, session_id: str):
        current = session_id
        seen = set()
        for _ in range(100):
            if not current or current in seen:
                return
            seen.add(current)
            row = conn.execute(
                """SELECT child.id, child.artifact_delivery_json,
                          child.parent_session_id, parent.end_reason AS parent_end_reason
                   FROM sessions child
                   LEFT JOIN sessions parent ON parent.id = child.parent_session_id
                   WHERE child.id = ?""",
                (current,),
            ).fetchone()
            if row is None:
                return
            yield row
            if row["parent_end_reason"] != "compression":
                return
            current = row["parent_session_id"]

    def recover_artifact_delivery(
        self, lineage_tip_session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Terminalize the newest interrupted transaction in a compression lineage."""
        if not lineage_tip_session_id:
            return None

        def _do(conn):
            if not self._artifact_column_exists(conn):
                return None
            for row in self._artifact_delivery_lineage_rows(conn, lineage_tip_session_id):
                raw = row["artifact_delivery_json"]
                if raw is None:
                    continue
                value, raw_is_valid = self._decode_artifact_delivery(raw)
                if not raw_is_valid:
                    conn.execute(
                        "UPDATE sessions SET artifact_delivery_json = ? WHERE id = ?",
                        (self._encode_artifact_delivery(value), row["id"]),
                    )
                    return {
                        "previous_state": "malformed",
                        "state": "uncertain",
                        "session_id": row["id"],
                    }
                if value is None or value["state"] in self._ARTIFACT_DELIVERY_TERMINAL_STATES:
                    continue
                previous_state = value["state"]
                value["state"] = (
                    "uncertain" if previous_state == "delivery_started" else "abandoned"
                )
                conn.execute(
                    "UPDATE sessions SET artifact_delivery_json = ? WHERE id = ?",
                    (self._encode_artifact_delivery(value), row["id"]),
                )
                return {
                    "previous_state": previous_state,
                    "state": value["state"],
                    "session_id": row["id"],
                    "transaction_id": value["transaction_id"],
                }
            return None

        return self._execute_write(_do)

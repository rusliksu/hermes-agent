"""One bound document turn: real tool provenance, bounded correction, durable handoff."""
from contextlib import contextmanager
import logging

from agent.artifact_delivery_stop import bound_artifact_stop_action, bound_artifact_tool_batch_relevant
from tools.artifact_delivery_tool import bound_document_context_active


ARTIFACT_FAILURE = "⚠️ Не удалось безопасно подготовить документ к отправке. Повторите запрос явно."


class ArtifactTurn:
    def __init__(self, agent):
        from agent.access_paths import bound_profile_home
        from gateway.access_context import serialize_resolved_access_context
        from gateway.access_context import get_resolved_access_context
        self.agent = agent
        self.home = bound_profile_home()
        self.authority = serialize_resolved_access_context(get_resolved_access_context())
        self.events = []
        self.session_id = self.transaction_id = self.confirmation = None
        self.attempts = 0
        self.reconfirmation_required = False
        self.relevant = False

    def _check_authority(self):
        from agent.access_paths import bound_profile_home
        from gateway.access_context import serialize_resolved_access_context
        from gateway.access_context import get_resolved_access_context
        if (bound_profile_home() != self.home or
                serialize_resolved_access_context(get_resolved_access_context()) != self.authority):
            raise ValueError("artifact authority changed")

    def begin_batch(self, calls):
        self._check_authority()
        if bound_artifact_tool_batch_relevant(calls) and self.transaction_id is None:
            self.relevant = True
            db = getattr(self.agent, "_session_db", None)
            sid = self.agent.session_id
            token = db.begin_artifact_delivery(sid) if db is not None and sid else None
            if not token:
                raise ValueError("artifact transaction unavailable")
            self.session_id, self.transaction_id = sid, token
            self.reconfirmation_required = False

    def observe(self, message):
        if isinstance(message, dict) and message.get("role") in {"tool", "function"}:
            self.events.append(message.copy())

    def abandon(self, *, invalidate=False):
        try:
            if self.transaction_id:
                db = self.agent._session_db
                for expected in ("pending", "ready"):
                    if db.transition_artifact_delivery(self.session_id, self.transaction_id, expected, "abandoned"):
                        break
        except Exception:
            logging.warning("Artifact abandonment failed", exc_info=True)
        self.session_id = self.transaction_id = self.confirmation = None
        if invalidate:
            self.events.clear()
            self.reconfirmation_required = True

    def finish(self):
        self._check_authority()
        action, nudge, confirmation = bound_artifact_stop_action(self.events, attempts=self.attempts)
        if action != "none":
            self.relevant = True
            failures = getattr(self.agent, "_turn_failed_file_mutations", {})
            for path, detail in list(failures.items()):
                if isinstance(detail, dict) and "bound_artifact_output_rejected:" in str(detail.get("error_preview", "")):
                    failures.pop(path, None)
        if action == "none" and not self.transaction_id and not self.reconfirmation_required:
            return action, None
        if action == "continue" and nudge:
            self.abandon(invalidate=True)
            self.attempts = 1
            return action, nudge
        if (action == "confirmed" and self.transaction_id and self.agent._session_db.transition_artifact_delivery(
                self.session_id, self.transaction_id, "pending", "ready")):
            self.confirmation = {**confirmation, "transaction_session_id": self.session_id,
                                 "transaction_id": self.transaction_id}
            return "confirmed", None
        self.abandon()
        return "failed", None

    def export(self, result):
        if (self.confirmation and isinstance(result, dict) and result.get("completed") is True
                and not result.get("failed") and not result.get("interrupted") and not result.get("error")):
            result["artifact_delivery_confirmation"] = dict(self.confirmation)
            self.session_id = self.transaction_id = self.confirmation = None
        else:
            self.abandon()
            if self.relevant or self.reconfirmation_required:
                result.update(final_response=ARTIFACT_FAILURE, completed=False, failed=True)
        return result


@contextmanager
def artifact_turn_scope(agent):
    if not bound_document_context_active():
        yield None
        return
    turn = ArtifactTurn(agent)
    names = ("_artifact_turn", "stream_delta_callback", "interim_assistant_callback", "_stream_callback")
    previous = {name: getattr(agent, name, None) for name in names}
    agent._artifact_turn = turn
    for name in names[1:]:
        setattr(agent, name, None)  # Hold model success text until the native file send is acknowledged.
    try:
        yield turn
    finally:
        try:
            turn.abandon()
        finally:
            for name, value in previous.items():
                setattr(agent, name, value)

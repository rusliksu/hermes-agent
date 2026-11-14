"""Native-adapter handoff for one server-confirmed document; never replay an uncertain upload."""
import asyncio
from pathlib import Path

from gateway.access_context import session_scope_from_resolved_access_context
from gateway.access_context import get_resolved_access_context


async def prepare_confirmation(runner, event, session_key, confirmation, *, publish):
    event.artifact_delivery_confirmation = None
    if not isinstance(confirmation, dict):
        return False
    required = {"path", "media_tag", "tool_call_id", "transaction_session_id", "transaction_id"}
    if set(confirmation) != required or any(not isinstance(confirmation[k], str) or not confirmation[k] for k in required):
        return False
    db = runner.session_store._db_for_key(session_key)
    if db is None:
        return False
    sid, token = confirmation["transaction_session_id"], confirmation["transaction_id"]
    try:
        with runner._media_delivery_scope_for_source(event.source):
            from tools.artifact_delivery_tool import _resolve_artifact, bound_document_context_active
            context = get_resolved_access_context()
            if context is None or not bound_document_context_active():
                return False
            row = db.get_session(sid)
            if row is None:
                return False
            path, error = _resolve_artifact(confirmation["path"], session_key)
            valid = (publish and error is None and path is not None
                     and Path(confirmation["path"]).is_absolute()
                     and confirmation["path"] == str(path)
                     and confirmation["media_tag"] == f"MEDIA:{path}")
            if not valid:
                await asyncio.to_thread(db.transition_artifact_delivery, sid, token, "ready", "abandoned")
                return False
            event.artifact_delivery_confirmation = dict(confirmation)
            async def transition(expected, new, **kwargs):
                return await asyncio.to_thread(db.transition_artifact_delivery, sid, token, expected, new, **kwargs)
            event._artifact_delivery_transition = transition
            return True
    except Exception:
        return False


async def deliver_confirmed(adapter, event, session_key, metadata):
    from gateway.platforms.base import SendResult
    from tools.artifact_delivery_tool import _resolve_artifact, bound_document_context_active
    confirmation = event.artifact_delivery_confirmation
    transition = getattr(event, "_artifact_delivery_transition", None)
    if not isinstance(confirmation, dict) or not callable(transition):
        return SendResult(success=False, error="Missing artifact transaction")
    claimed = delivered = False
    try:
        current = adapter._final_delivery_adapter(event.source)
        with current._media_delivery_scope(event.source):
            if not bound_document_context_active():
                return SendResult(success=False, error="Artifact access denied")
            path, error = _resolve_artifact(confirmation["path"], session_key)
            if error or str(path) != confirmation["path"]:
                return SendResult(success=False, error="Artifact path changed")
            if not await transition("ready", "delivery_started"):
                return SendResult(success=False, error="Artifact claim rejected")
            claimed = True
            result = await current.send_document(event.source.chat_id, str(path), metadata=metadata, _require_native=True)
            receipt = getattr(result, "message_id", None)
            if not getattr(result, "success", False) or not receipt:
                return SendResult(success=False, error="Native artifact receipt missing")
            if not await transition("delivery_started", "delivered", receipt_id=str(receipt)):
                return SendResult(success=False, error="Artifact receipt persistence failed")
            delivered = True
            return result
    except Exception:
        return SendResult(success=False, error="Artifact delivery failed")
    finally:
        if claimed and not delivered:
            try:
                await transition("delivery_started", "uncertain")
            except Exception:
                pass  # The durable delivery_started state is recovered as uncertain after restart.


async def abandon_confirmation(event):
    transition = getattr(event, "_artifact_delivery_transition", None)
    try:
        if callable(transition):
            await transition("ready", "abandoned")
    except Exception:
        pass  # Restart recovery terminalizes an unsent ready transaction if cleanup cannot write.
    finally:
        event.artifact_delivery_confirmation = None
        if hasattr(event, "_artifact_delivery_transition"):
            delattr(event, "_artifact_delivery_transition")

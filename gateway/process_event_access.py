"""Validate terminal-process notifications against current durable routing authority."""
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


def requires_process_context(runner, event):
    from gateway.access_runtime import has_access_registry
    return has_access_registry(runner) and (
        str(event.get("session_id") or "").startswith("proc_")
        or event.get("resolved_access_context") is not None
        or event.get("type") in {"async_delegation", "completion", "heartbeat", "watch_match", "watch_disabled",
                                 "watch_overflow_tripped", "watch_overflow_released"})


def validated_process_source(runner, event):
    from gateway.access_registry import deserialize_resolved_access_context
    try:
        context = deserialize_resolved_access_context(event.get("resolved_access_context"))
        key = str(event.get("session_key") or "").strip()
        entry = runner.session_store.lookup_by_session_key(key)
        if entry is None:
            return None
        source = runner._restored_source(entry)
        if source is None or source.resolved_access_context != context:
            return None
        # Re-resolve against the CURRENT registry: a role's grants or the bot account may have
        # changed since the event was queued, and the durable snapshot alone cannot prove them.
        from gateway.access_runtime import resolve_context

        receiving = runner._authorization_adapter(
            source.platform, getattr(entry, "transport_profile", None))
        live = resolve_context(source, runner=runner, adapter=receiving, pinned=context)
        if live is None:
            return None
        target = context.delivery_target
        expected = {"platform": target.platform, "chat_id": target.chat_id, "thread_id": target.thread_id,
                    "chat_type": target.peer_kind, "user_id": source.user_id}
        for field, value in expected.items():
            if event.get(field) not in (None, "") and str(event[field]) != str(value or ""):
                return None
        return source
    except Exception:
        logger.debug("process event source rejected", exc_info=True)
        return None


@asynccontextmanager
async def process_event_scope(runner, source):
    from gateway.access_context import bind_resolved_access_context
    with bind_resolved_access_context(source.resolved_access_context if source is not None else None):
        if source is None:
            yield
        else:
            async with runner._async_profile_scope_for_source(source):
                yield


async def send_watcher_message(runner, platform_name, chat_id, thread_id, message_text, watcher) -> None:
    """Deliver a watcher notice through the validated authority, not caller hints."""
    import asyncio
    import logging

    from gateway.run import _non_conversational_metadata
    from gateway.process_event_access import requires_process_context

    source = await asyncio.to_thread(runner._build_process_event_source, watcher)
    if requires_process_context(runner, watcher):
        # Caller-supplied chat/thread are hints: the validated authority names the destination.
        adapter = runner._delivery_adapter_for(source) if source is not None else None
        if adapter is not None and source is not None:
            target = source.resolved_access_context.delivery_target
            try:
                send_meta = {"thread_id": target.thread_id} if target.thread_id else None
                await adapter.send(
                    target.chat_id, message_text,
                    metadata=_non_conversational_metadata(send_meta, platform=platform_name),
                )
            except Exception:
                logger.error("Watcher delivery error", exc_info=True)
        return
    adapter = runner._resolve_injection_adapter(platform_name, source)
    if adapter and chat_id:
        try:
            send_meta = {"thread_id": thread_id} if thread_id else None
            await adapter.send(
                chat_id, message_text,
                metadata=_non_conversational_metadata(send_meta, platform=platform_name),
            )
        except Exception:
            logger.error("Watcher delivery error", exc_info=True)

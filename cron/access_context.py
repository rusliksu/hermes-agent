"""Job-owned access snapshots on the native cron lifecycle; never inherit a foreground sender."""
from functools import wraps


def canonical_job_access_context(payload):
    if payload is None:
        return None
    from gateway.access_registry import deserialize_resolved_access_context, serialize_resolved_access_context

    try:
        return serialize_resolved_access_context(deserialize_resolved_access_context(payload))
    except (TypeError, ValueError) as exc:
        raise ValueError("malformed_resolved_access_context") from exc


def capture_job_access_context():
    from agent.secret_scope import is_multiplex_active
    from gateway.access_context import get_resolved_access_context
    from gateway.access_registry import serialize_resolved_access_context

    context = get_resolved_access_context()
    if context is None:
        if is_multiplex_active():
            raise ValueError("missing_resolved_access_context")
        return None
    return serialize_resolved_access_context(context)


def job_access_scoped(function=None, *, require_snapshot=True):
    if function is None:
        return lambda wrapped: job_access_scoped(wrapped, require_snapshot=require_snapshot)

    @wraps(function)
    def scoped(job, *args, **kwargs):
        from agent.secret_scope import is_multiplex_active
        from gateway.access_context import bind_resolved_access_context
        from gateway.access_registry import deserialize_resolved_access_context

        try:
            payload = canonical_job_access_context(job.get("resolved_access_context"))
            if require_snapshot and payload is None and is_multiplex_active():
                raise ValueError("missing_resolved_access_context")
            context = deserialize_resolved_access_context(payload) if payload is not None else None
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Cron job blocked: invalid_resolved_access_context") from exc
        with bind_resolved_access_context(context):
            if context is not None:
                from agent.access_paths import bound_profile_home

                try:
                    if bound_profile_home() is None:
                        raise ValueError("missing_home")
                except (OSError, RuntimeError, ValueError) as exc:
                    raise RuntimeError("Cron job blocked: bound_profile_home_mismatch") from exc
            return function(job, *args, **kwargs)

    return scoped

"""Neutral SQL scope for typed conversation recall; no gateway dependency."""


def session_scope_sql(session_scope, *, alias="s"):
    if session_scope is None:
        return "", ()
    if not isinstance(session_scope, dict):
        return "1 = 0", ()
    keys = ("profile_name", "source", "chat_type", "chat_id")
    if any(not isinstance(session_scope.get(k), str) or not session_scope[k] for k in keys):
        return "1 = 0", ()
    thread = session_scope.get("thread_id")
    dm = session_scope.get("is_dm")
    user = session_scope.get("user_id")
    if (thread is not None and not isinstance(thread, str)) or not isinstance(dm, bool):
        return "1 = 0", ()
    if dm and (not isinstance(user, str) or not user):
        return "1 = 0", ()
    clauses = [f"{alias}.{key} = ?" for key in keys]
    values = [session_scope[key] for key in keys]
    clauses.append(f"COALESCE({alias}.thread_id, '') = ?")
    values.append(thread or "")
    if dm:
        clauses.append(f"{alias}.user_id = ?")
        values.append(user)
    return " AND ".join(clauses), tuple(values)


def scoped_session_cte(session_scope):
    """(WITH entry, relation name, params) for bounded lineage queries."""
    clause, values = session_scope_sql(session_scope)
    if not clause:
        return "", "sessions", ()
    return (f"scoped_sessions AS NOT MATERIALIZED (SELECT * FROM sessions s WHERE {clause}),",
            "scoped_sessions", values)

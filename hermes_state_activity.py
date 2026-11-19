"""Legacy-compatible activity partition shared by session SQL consumers."""

AUTOMATION_SESSION_SOURCES = (
    "acp", "api_server", "cron", "delegate", "hermes_flow",
    "subagent", "tool", "vulcan_delegate", "webhook",
)


def session_activity_filter_sql(activity, *, alias="s"):
    if activity in (None, "", "all"):
        return "", ()
    if activity not in ("people", "automations"):
        raise ValueError("activity must be one of: people, automations, all")
    placeholders = ",".join("?" for _ in AUTOMATION_SESSION_SOURCES)
    predicate = (f"(LOWER(COALESCE({alias}.source, '')) IN ({placeholders}) "
                 f"OR {alias}.id LIKE 'cron:telegram:%')")
    return (f"NOT {predicate}" if activity == "people" else predicate), AUTOMATION_SESSION_SOURCES

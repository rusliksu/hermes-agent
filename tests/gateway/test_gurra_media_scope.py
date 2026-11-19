"""Attachment validation must retain the typed profile after the agent turn ends."""
from dataclasses import replace

import pytest

from agent.secret_scope import is_multiplex_active, set_multiplex_active
from gateway.platforms.base import validate_media_delivery_path
from gateway.run import _profile_runtime_scope
from gateway.access_context import bind_resolved_access_context
from tests.agent.test_gurra_memory_snapshot import context, homes

_FIXTURES = (context, homes)  # imported for pytest fixture discovery, not called here


@pytest.fixture
def attachments(homes):
    result = {}
    for name, home in homes.items():
        path = home / "cache" / "documents" / "report.csv"
        path.parent.mkdir(parents=True)
        path.write_text("fixture,value\n", encoding="utf-8")
        result[name] = path
    return result


def test_bound_media_cannot_use_foreign_profile_cache(homes, attachments):
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context()):
        assert validate_media_delivery_path(str(attachments["alpha"])) == str(attachments["alpha"].resolve())
        assert validate_media_delivery_path(str(attachments["beta"])) is None


def test_bound_media_rejects_symlink_and_recent_operator_root(homes, attachments, tmp_path, monkeypatch):
    foreign_link = homes["alpha"] / "linked.csv"
    foreign_link.symlink_to(attachments["beta"])
    outside = tmp_path / "outside.csv"
    outside.write_text("fixture,value\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_MEDIA_ALLOW_DIRS", str(tmp_path))
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context()):
        assert validate_media_delivery_path(str(foreign_link)) is None
        assert validate_media_delivery_path(str(outside)) is None


def test_multiplex_unbound_and_mismatched_home_deny_media(homes, attachments):
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(None):
            assert validate_media_delivery_path(str(attachments["alpha"])) is None
        with _profile_runtime_scope(homes["beta"]), bind_resolved_access_context(context()):
            assert validate_media_delivery_path(str(attachments["beta"])) is None
    finally:
        set_multiplex_active(previous)


def test_default_owner_cannot_address_another_profile_cache(homes, attachments):
    owner = replace(context(), role_id="owner", profile_id="default")
    with _profile_runtime_scope(homes["alpha"].parent.parent), bind_resolved_access_context(owner):
        assert validate_media_delivery_path(str(attachments["beta"])) is None


def test_untyped_non_multiplex_native_attachment_policy_remains(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("fixture,value\n", encoding="utf-8")
    previous = is_multiplex_active()
    set_multiplex_active(False)
    try:
        with bind_resolved_access_context(None):
            assert validate_media_delivery_path(str(path)) == str(path.resolve())
    finally:
        set_multiplex_active(previous)


def test_remote_fallback_cannot_copy_a_denied_host_file(homes, attachments, monkeypatch):
    from gateway.platforms.base import _validated_delivery_path
    monkeypatch.setattr("gateway.media_fetch.fetch_remote_media", lambda path: pytest.fail("denied host file was fetched"))
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context()):
        assert _validated_delivery_path(str(attachments["beta"]), "fixture", "fixture document") is None

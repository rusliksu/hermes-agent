"""Regression tests for typed profile-bound local reads in agent.file_safety."""

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.file_safety import raise_if_read_blocked
from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import bind_resolved_access_context


_DENIED = "restricted to the resolved access profile"


def _context(profile_id="profile-a") -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-a",
        role_id="family_standard",
        profile_id=profile_id,
        conversation_scope="dm:principal-a",
        capabilities=frozenset(),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            chat_id="10001",
        ),
    )


def _profiles_root(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "profiles" / "profile-a").mkdir(parents=True)
    return home


def _assert_generic_denial(excinfo, *hidden: object) -> None:
    message = str(excinfo.value)
    assert _DENIED in message
    for value in hidden:
        assert str(value) not in message


def test_no_context_safe_local_path_remains_allowed(tmp_path):
    target = tmp_path / "outside" / "note.txt"
    target.parent.mkdir()
    target.write_text("ok", encoding="utf-8")

    with bind_resolved_access_context(None):
        raise_if_read_blocked(str(target))


def test_typed_valid_profile_path_allowed(tmp_path, monkeypatch):
    home = _profiles_root(tmp_path, monkeypatch)
    target = home / "profiles" / "profile-a" / "cache" / "images" / "pic.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")

    with bind_resolved_access_context(_context("profile-a")):
        raise_if_read_blocked(str(target))


def test_typed_valid_profile_path_allowed_when_home_is_active_named_profile(
    tmp_path, monkeypatch
):
    home = tmp_path / "hermes"
    profile_home = home / "profiles" / "profile-a"
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    target = profile_home / "cache" / "images" / "pic.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")

    with bind_resolved_access_context(_context("profile-a")):
        raise_if_read_blocked(str(target))


def test_typed_missing_path_inside_profile_remains_provider_error(
    tmp_path, monkeypatch
):
    home = _profiles_root(tmp_path, monkeypatch)
    target = home / "profiles" / "profile-a" / "cache" / "images" / "missing.png"

    with bind_resolved_access_context(_context("profile-a")):
        raise_if_read_blocked(str(target))


def test_typed_sibling_profile_path_rejected_without_raw_path_or_profile(
    tmp_path, monkeypatch
):
    home = _profiles_root(tmp_path, monkeypatch)
    target = home / "profiles" / "profile-b" / "cache" / "images" / "pic.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")

    with bind_resolved_access_context(_context("profile-a")):
        with pytest.raises(ValueError) as excinfo:
            raise_if_read_blocked(str(target))

    _assert_generic_denial(excinfo, target, "profile-a", "profile-b")


def test_typed_symlink_escape_rejected_before_read(tmp_path, monkeypatch):
    home = _profiles_root(tmp_path, monkeypatch)
    outside = tmp_path / "outside" / "pic.png"
    outside.parent.mkdir()
    outside.write_bytes(b"png")
    link = home / "profiles" / "profile-a" / "cache" / "images" / "link.png"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported")

    with bind_resolved_access_context(_context("profile-a")):
        with pytest.raises(ValueError) as excinfo:
            raise_if_read_blocked(str(link))

    _assert_generic_denial(excinfo, link, outside, "profile-a")


@pytest.mark.parametrize(
    "context",
    [
        {"profile_id": "profile-a"},
        _context(""),
        _context("missing-profile"),
        _context(Path("profile-a")),
    ],
)
def test_malformed_blank_unknown_non_string_profile_rejected_generically(
    context, tmp_path, monkeypatch
):
    home = _profiles_root(tmp_path, monkeypatch)
    target = home / "profiles" / "profile-a" / "cache" / "images" / "pic.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")

    with bind_resolved_access_context(context):
        with pytest.raises(ValueError) as excinfo:
            raise_if_read_blocked(str(target))

    _assert_generic_denial(excinfo, target, "profile-a", "missing-profile")


def test_context_accessor_failure_fails_closed_with_generic_error(
    tmp_path, monkeypatch
):
    home = _profiles_root(tmp_path, monkeypatch)
    target = home / "profiles" / "profile-a" / "cache" / "images" / "pic.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")

    with patch(
        "gateway.session_context.get_resolved_access_context",
        side_effect=RuntimeError("task-local internals"),
    ):
        with pytest.raises(ValueError) as excinfo:
            raise_if_read_blocked(str(target))

    _assert_generic_denial(excinfo, target, "profile-a", "task-local internals")


def test_remote_and_data_inputs_skip_local_profile_boundary():
    with bind_resolved_access_context({"profile_id": "not-a-context"}):
        raise_if_read_blocked("https://example.test/image.png")
        raise_if_read_blocked("data:image/png;base64,abcd")

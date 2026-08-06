"""Synthetic, reversible profile migration planner contract."""

import hashlib
import json
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _policy_maps(policy: dict) -> tuple[dict[str, str], dict[str, str]]:
    principals = {
        row["principal_id"]: row["profile_id"]
        for row in policy["principals"]
    }
    rooms = {
        row["scope_id"]: row["profile_id"]
        for row in policy["rooms"]
    }
    return principals, rooms


def plan_migration(fixture: dict, policy: dict) -> dict:
    """Plan only; never writes, deletes, or reads outside supplied fixtures."""
    principal_profiles, room_profiles = _policy_maps(policy)
    migrated = []
    rooms = []
    legacy_archive = []

    for row in fixture["sessions"]:
        kind = row.get("kind")
        if kind == "dm":
            principal_id = row.get("principal_id")
            profile_id = row.get("profile_id")
            exact_dm = (
                isinstance(principal_id, str)
                and principal_id in principal_profiles
                and profile_id == principal_profiles[principal_id]
                and row.get("platform") == "telegram"
                and row.get("account") == "synthetic-bot"
                and row.get("peer_kind") == "dm"
                and row.get("user_id")
                and row.get("user_id") == row.get("chat_id")
                and not row.get("principal_candidates")
            )
            if exact_dm:
                migrated.append(
                    {
                        "legacy_session_id": row["legacy_session_id"],
                        "principal_id": principal_id,
                        "profile_id": profile_id,
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )
                continue
            legacy_archive.append(
                {
                    "legacy_session_id": row["legacy_session_id"],
                    "reason": "ambiguous_or_untrusted_dm_identity",
                    "read_only": True,
                }
            )
            continue

        if (
            kind == "room"
            and row.get("scope_id") in room_profiles
            and row.get("profile_id") == room_profiles[row["scope_id"]]
        ):
            rooms.append(
                {
                    "legacy_session_id": row["legacy_session_id"],
                    "scope_id": row["scope_id"],
                    "profile_id": row["profile_id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
            continue

        legacy_archive.append(
            {
                "legacy_session_id": row["legacy_session_id"],
                "reason": "unknown_or_malformed_scope",
                "read_only": True,
            }
        )

    report = {
        "dry_run": True,
        "migrated": migrated,
        "rooms": rooms,
        "legacy_archive": legacy_archive,
        "global_memory_skipped": [item["path"] for item in fixture["global_memory"]],
    }
    report["counts"] = {
        "dm_migrated": len(migrated),
        "rooms_bound": len(rooms),
        "legacy_archived": len(legacy_archive),
        "global_memory_skipped": len(report["global_memory_skipped"]),
    }
    report["hashes"] = {
        "migrated": _hash(migrated),
        "rooms": _hash(rooms),
        "legacy_archive": _hash(legacy_archive),
    }
    return report


def test_fixture_roster_is_opaque_and_complete():
    policy = _load("access_policy_matrix.json")
    assert len(policy["principals"]) == 10
    assert len(policy["principals"]) - 1 == 9
    assert len(policy["rooms"]) == 2
    assert {row["role_id"] for row in policy["principals"]} == {
        "owner",
        "family",
    }
    assert next(row for row in policy["principals"] if row["role_id"] == "owner")["profile_id"] == "profile-owner"
    assert next(row for row in policy["principals"] if row["principal_id"] == "principal-yulia")["role_id"] == "family"
    assert all("transport" not in json.dumps(row) for row in policy["principals"])


def test_dry_run_preserves_exact_dm_ids_timestamps_and_hashes():
    report = plan_migration(_load("migration_fixture.json"), _load("access_policy_matrix.json"))
    rerun = plan_migration(_load("migration_fixture.json"), _load("access_policy_matrix.json"))

    assert report == rerun
    assert report["dry_run"] is True
    assert report["counts"] == {
        "dm_migrated": 4,
        "rooms_bound": 2,
        "legacy_archived": 4,
        "global_memory_skipped": 3,
    }
    assert report["hashes"]["migrated"]
    exact = next(row for row in report["migrated"] if row["legacy_session_id"] == "legacy-dm-owner-001")
    assert exact["created_at"] == "2026-01-01T00:00:00Z"
    assert exact["updated_at"] == "2026-01-01T00:10:00Z"
    assert all(row["read_only"] for row in report["legacy_archive"])
    assert {row["legacy_session_id"] for row in report["legacy_archive"]} == {
        "legacy-dm-display-only",
        "legacy-dm-conflicting-candidates",
        "legacy-dm-telegram-mismatch",
        "legacy-dm-unknown-principal",
    }


def test_global_memory_is_skipped_and_never_profile_visible():
    report = plan_migration(_load("migration_fixture.json"), _load("access_policy_matrix.json"))
    global_paths = set(report["global_memory_skipped"])
    assert {"MEMORY.md", "USER.md"}.issubset(global_paths)
    profile_outputs = json.dumps(report["migrated"] + report["rooms"])
    assert not any(path in profile_outputs for path in global_paths)

"""Validate the redacted media fallback backup/canary packet."""

import hashlib
import json
from pathlib import Path

from utils import fast_safe_load
from tools.media_provider_routing import dry_run_media_policy


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "media-provider-policy-canary.yaml"
PACKET = ROOT / "docs" / "fixtures" / "media-provider-fallback-canary.json"


def test_redacted_fixture_matches_packet_and_dry_run():
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    fixture_bytes = FIXTURE.read_bytes()

    assert packet["schema"] == "media-provider-fallback-canary/v1"
    assert packet["fixture"]["redacted"] is True
    assert packet["fixture"]["sha256"] == hashlib.sha256(fixture_bytes).hexdigest()
    config = fast_safe_load(FIXTURE.read_text(encoding="utf-8")) or {}
    report = dry_run_media_policy(config)
    expected = packet["commands"][0]["expected"]
    assert report["valid"] is expected["valid"]
    assert report["mode"] == expected["mode"]
    for operation, providers in expected["provider_orders"].items():
        assert report["operations"][operation]["provider_order"] == providers
    assert "profile://synthetic" not in json.dumps(report, sort_keys=True)


def test_packet_is_non_mutating_and_has_fail_closed_matrix():
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    assert packet["safety"] == {
        "credentials_included": False,
        "live_configuration_changed": False,
        "restart_requested": False,
        "telegram_delivery": False,
        "profile_fallback": "deny",
    }
    assert all(command["mutates"] is False for command in packet["commands"])
    command_text = json.dumps(packet["commands"], sort_keys=True).lower()
    for forbidden in (".env", "systemctl", "restart", "telegram"):
        assert forbidden not in command_text
    assert [item["principal"] for item in packet["matrix"]] == [
        "owner",
        "family_standard",
        "family_sandbox",
        "shared_room",
        "unknown",
    ]
    assert packet["matrix"][-1]["expected"] == "deny"
    assert packet["backup"]["includes_credentials"] is False
    assert packet["rollback"]["gate"] == "explicit-live-configuration-approval"
    assert packet["rollback"]["does_not_restore_credentials"] is True

"""Restart must retain authority, revalidate it and preserve transport ownership."""
import json
from datetime import datetime
from dataclasses import replace

import pytest

from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionStore
from gateway.session_identity import canonical_identity
from tests.gateway.test_access_native_identity import received, rig

_FIXTURES = (rig, received)  # imported for pytest fixture discovery, not called here


EXPECTED = {
    'principal_id': 'member-alpha', 'role_id': 'family', 'profile_id': 'alpha',
    'conversation_scope': 'private-alpha',
    'capabilities': ['private_memory', 'public_web', 'terminal'],
    'delivery_target': {'platform': 'telegram', 'account': 'fixture-bot', 'peer_kind': 'dm',
                        'chat_id': '1001', 'thread_id': None},
}


def saved_entry(rig):
    now = datetime.now()
    return SessionEntry(session_key='agent:alpha:telegram:dm:1001', session_id='fixture-restore',
                        created_at=now, updated_at=now, origin=received(rig), transport_profile='default')


def restarted(rig, wire):
    fresh = object.__new__(GatewayRunner)
    fresh.config = rig.config
    fresh.access_registry = rig.access_registry
    fresh._primary_profile_name = 'default'
    fresh.adapters = rig.adapters
    fresh._profile_adapters = rig._profile_adapters
    entry = SessionEntry.from_dict(json.loads(json.dumps(wire)))
    return fresh, entry, fresh._restored_source(entry)


def test_wire_roundtrip_retains_context_and_restores_native_identity(rig):
    wire = saved_entry(rig).to_dict()
    assert wire.get('resolved_access_context') == EXPECTED
    assert 'resolved_access_context' not in wire['origin']
    fresh, _entry, source = restarted(rig, wire)
    identity = canonical_identity(source, runner=fresh)
    assert identity.runtime_profile == 'alpha'
    assert identity.access_context.principal_id == 'member-alpha'
    assert identity.transport is None
    assert fresh._delivery_adapter_for(source) is rig.adapters[source.platform]


def test_sqlite_routing_index_reloads_authority(rig, tmp_path):
    store = SessionStore(sessions_dir=tmp_path / '.hermes' / 'sessions', config=rig.config)
    reopened = SessionStore(sessions_dir=tmp_path / '.hermes' / 'sessions', config=rig.config)
    try:
        original = store.get_or_create_session(received(rig))
        assert original.to_dict().get('resolved_access_context') == EXPECTED
        recovered = reopened.get_or_create_session(received(rig))
        assert recovered.session_id == original.session_id
        assert recovered.to_dict().get('resolved_access_context') == EXPECTED
        reset = reopened.reset_session(recovered.session_key)
        assert reset.session_id != recovered.session_id
        assert reset.to_dict()['resolved_access_context'] == EXPECTED
    finally:
        reopened.close_all_db_handles()
        store.close_all_db_handles()


def test_restore_rejects_missing_context_without_owner_fallback(rig):
    wire = saved_entry(rig).to_dict()
    wire.pop('resolved_access_context', None)
    _fresh, _entry, source = restarted(rig, wire)
    assert source is None


def test_restore_rejects_changed_physical_account(rig):
    wire = saved_entry(rig).to_dict()
    rig.adapters[received(rig).platform].config.extra['account'] = 'another-bot'
    _fresh, _entry, source = restarted(rig, wire)
    assert source is None


def test_restore_rejects_origin_sender_tampering(rig):
    wire = saved_entry(rig).to_dict()
    wire['origin']['user_id'] = '1009'
    _fresh, _entry, source = restarted(rig, wire)
    assert source is None


def test_restore_rejects_revoked_registry_binding(rig):
    wire = saved_entry(rig).to_dict()
    rig.access_registry = replace(rig.access_registry, principal_bindings=())
    assert restarted(rig, wire)[2] is None


def test_restored_identity_rechecks_account_on_each_use(rig):
    fresh, _entry, source = restarted(rig, saved_entry(rig).to_dict())
    assert canonical_identity(source, runner=fresh) is not None
    rig.adapters[source.platform].config.extra['account'] = 'another-bot'
    assert canonical_identity(source, runner=fresh) is None
    assert fresh._delivery_adapter_for(source) is None


def test_routing_store_rejects_authority_change_before_reusing_history(rig, tmp_path):
    from gateway.access_registry import AccessDeniedError
    store = SessionStore(sessions_dir=tmp_path / '.hermes' / 'sessions', config=rig.config)
    try:
        original = store.get_or_create_session(received(rig))
        altered = received(rig)
        altered.resolved_access_context = replace(altered.resolved_access_context, conversation_scope='foreign-scope')
        with pytest.raises(AccessDeniedError, match='session_authority_mismatch'):
            store.get_or_create_session(altered)
        assert store.get_or_create_session(received(rig)).session_id == original.session_id
    finally:
        store.close_all_db_handles()

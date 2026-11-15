"""Immutable authority retained by memory-provider lifecycle callbacks."""
from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path

UNSET = object()


def owned_memory_callback(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._bind_resolved_access_context():
            return method(self, *args, **kwargs)
    return call


class MemoryAccessBinding:
    def _init_access_binding(self, context=UNSET):
        from hermes_constants import get_hermes_home
        self._memory_home = get_hermes_home()
        self._resolved_access_context_payload = None
        self._memory_scope = None
        self._resolved_access_context_captured = False
        if context is UNSET:
            from gateway.access_context import get_resolved_access_context
            context = get_resolved_access_context()
        if context is not None:
            self._capture_resolved_access_context({'resolved_access_context': context})

    @staticmethod
    def _canonical_resolved_access_context_payload(context):
        from gateway.access_registry import deserialize_resolved_access_context, serialize_resolved_access_context
        resolved = (deserialize_resolved_access_context(context) if isinstance(context, dict)
                    else deserialize_resolved_access_context(serialize_resolved_access_context(context)))
        return json.dumps(serialize_resolved_access_context(resolved), sort_keys=True, separators=(',', ':'))

    def _capture_resolved_access_context(self, kwargs):
        from gateway.access_registry import memory_scope_from_resolved_access_context
        kwargs.pop('memory_scope', None)
        explicit = 'resolved_access_context' in kwargs
        context = kwargs.pop('resolved_access_context', None)
        if not explicit and self._resolved_access_context_captured:
            return
        if not explicit:
            from gateway.access_context import get_resolved_access_context
            context = get_resolved_access_context()
        if context is None:
            if explicit:
                raise ValueError('missing_resolved_access_context')
            self._resolved_access_context_captured = True
            if kwargs.get('hermes_home'):
                self._memory_home = Path(kwargs['hermes_home'])
            return
        payload = self._canonical_resolved_access_context_payload(context)
        if self._resolved_access_context_payload is not None and payload != self._resolved_access_context_payload:
            raise ValueError('resolved_access_context_mismatch')
        self._resolved_access_context_payload = payload
        from gateway.access_registry import deserialize_resolved_access_context
        from gateway.access_runtime import profile_home
        self._memory_home = profile_home(deserialize_resolved_access_context(json.loads(payload)))
        self._memory_scope = memory_scope_from_resolved_access_context(json.loads(payload))
        self._resolved_access_context_captured = True

    @contextmanager
    def _bind_resolved_access_context(self):
        if self._resolved_access_context_payload is None:
            # No registry authority was captured: the native caller-scope rules stay in force.
            yield
            return
        from gateway.access_registry import deserialize_resolved_access_context
        from gateway.access_context import bind_resolved_access_context
        from gateway.run import _profile_runtime_scope
        context = (deserialize_resolved_access_context(json.loads(self._resolved_access_context_payload))
                   if self._resolved_access_context_payload is not None else None)
        with bind_resolved_access_context(context), _profile_runtime_scope(self._memory_home):
            yield

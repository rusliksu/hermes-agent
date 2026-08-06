# Contract: access resolution and media dispatch

## Access resolution

```python
resolve_access(source: TrustedIngress) -> ResolvedAccessContext | AccessDenied
```

- `TrustedIngress` is server-created metadata, not model input.
- `AccessDenied` is terminal for the request and contains only a redacted reason code.
- A successful context contains exactly six fields and is immutable.
- Every downstream operation accepts the context or obtains it from the task-local runtime binding; it cannot accept a caller-selected profile namespace.

## Media dispatch

```python
dispatch_media(kind, request, context, policy) -> MediaResult | MediaDenied
```

- `kind` is `image`, `stt` or `tts`.
- The provider sequence comes from server policy intersected with `context.capabilities`.
- Each provider is attempted once. Only an allowlisted transient class advances to the next provider.
- Results contain status/provider/error-class and a profile-scoped artifact reference; no secret value.
- Shared-room media is denied unless a room policy explicitly enables it.

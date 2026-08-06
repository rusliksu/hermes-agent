# Data model: Gurra access and media isolation

## ResolvedAccessContext

An immutable value created once after ingress authorization. Serialization is an exact-key map; any missing or extra key is invalid.

| Field | Shape | Meaning | Trust rule |
|---|---|---|---|
| `principal_id` | opaque string | server-known principal | never derived from display name |
| `role_id` | enum | `owner`, `family`, `shared_room` | only binding/config may assign |
| `profile_id` | normalized string | Hermes profile/home namespace | must exist and be binding-owned |
| `conversation_scope` | private/room namespace | personal DM or explicit room/topic | room membership cannot elevate role |
| `capabilities` | frozen set | effective role ∩ scope ∩ backend capabilities | unknown values denied |
| `delivery_target` | server target | reply/cron delivery address | model arguments cannot override |

No other field is part of the contract. Raw usernames, message text, credentials, session IDs from user input and private prompt content are deliberately excluded.

## Bindings

- `RolePolicy`: read-only role prompt, capability allowlist, sandbox limits and delivery restrictions. `family` is the single equal-capability private role; `family_standard` and `family_sandbox` are input aliases accepted only during migration and are never emitted.
- `PrincipalBinding`: opaque principal, profile, role and confirmed `platform/account/peer_kind/user_id` identities.
- `SharedScopeBinding`: exact platform/account/chat/topic, room profile and member principal set.
- `MediaProviderPolicy`: ordered provider IDs per media kind, retry classes and opaque secret references.
- `BreakGlassLease`: owner audit ID, reason hash, read-only scope, expiry and revoked flag.

## State transitions

```text
untrusted ingress -> resolved context OR deny
resolved context -> profile/session/memory/tools/media/background
resolved context -> cleared on completion/cancellation
break-glass request -> confirmed lease -> read-only audit -> expiry/revoke
```

There is no transition from deny to owner/default profile.

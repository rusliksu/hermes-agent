WP03 is not approved by this routing repair pass.

Reason: the existing WP03 in_review state is historical/stalled and lacks a
valid nonempty coverage/diff gate for this root checkout. Re-run through the
official planned -> doing -> for_review path without --force so readiness is
based on current Spec Kitty guards and real review evidence.

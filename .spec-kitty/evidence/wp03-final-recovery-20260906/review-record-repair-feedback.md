WP03 is being returned to planned for review-record repair only.

This is not a Hermes implementation rejection. The previous review cycle entered
review state but did not persist a valid typed review_result for the old
in_review -> planned verdict, leaving the fixed resolver with a damaged
historical verdict slot.

Required repair:
- Re-enter the canonical implementation/review lane using the official action
  commands.
- Preserve the original review-cycle-1 artifact byte-for-byte.
- Reuse the already verified WP03 implementation and evidence.
- Move back to for_review only after the real regression gate reports
  no_new_failures under TMPDIR=/var/tmp.
- Complete review with a fresh typed review_result and approval reference.

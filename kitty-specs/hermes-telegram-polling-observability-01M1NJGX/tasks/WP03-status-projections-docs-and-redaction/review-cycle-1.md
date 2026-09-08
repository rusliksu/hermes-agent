WP03 review reset for gate validation.

The previous for_review event carried an invalid pre-review gate outcome
(`no_coverage`) because the automatic gate coverage authorities were
unavailable. No implementation defect is being reported here.

Re-enter review after configuring an explicit WP03-relevant pre-review test
scope and capturing a real baseline result through the supported Spec Kitty
baseline helper.

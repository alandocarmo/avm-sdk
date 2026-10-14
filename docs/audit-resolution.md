# Public SDK audit closure ledger

The release resolves the completed SDK-001 through SDK-010 audit as one package.
This ledger names acceptance assertions; execution results belong to the exact
release's qualification receipt. A listed assertion is not a claim that it ran.

- **SDK-001 — installer integrity.** `scripts/release.py`, `install.sh`;
  `scripts/tests/test_release_integrity.py` independently constructs valid and
  hostile tar files. Outer/inner hashes, full-commit/tag binding, receipt/binary
  identity, traversal, links, special files, duplicate and unlisted members,
  missing checksums, immutable existing releases and existing user commands
  must all be checked before activation. Failed validation preserves `current`.
- **SDK-002 — truthful engine profile.** The node profile revision records
  pooling, its resource formulas and explicit reservation/guard/COW settings.
  `release_profile_attests_pooling_and_explicit_memory_settings` supplements the
  fixed identity, mismatch, CPU, fresh-store, allocator, rejection and FUEL
  assertions. Adoption requires separate owner approval and independent review.
- **SDK-003 — current distribution and compatibility.** Source/SDK parity,
  compact-map/account vectors, EventLog canonical-data and malformed-input
  cases, isolated-FUEL admission, and the packaged lifecycle bind the selected
  accepted source. `release-notes.md` describes the incompatible state/profile
  boundary separately from guest API compatibility.
- **SDK-004 — stand correctness.** Existing exact block-start event-read,
  failed-batch burn and exhausted-pool fallback assertions plus the complete
  standard-contract E2E are mandatory. The release uses the selected node tree;
  it does not reproduce these owners inside the SDK.
- **SDK-005 — provenance.** Pinned runtime lock, clean exact source exports,
  compiler/linker identities, resolved dependency/features, actual binary hashes
  and deterministic archive construction are recorded by the manual release
  tooling. Package verification independently rehashes its complete contents.
  Native link arguments force the pinned rust-lld through its recorded driver
  shim; independent regressions reject a different or missing linker.
- **SDK-006 — packaged behavior.** `scripts/tests/packaged_lifecycle.py` builds
  all four public examples using the shipped builder, verifies emitted hashes,
  deploys and calls real code, exercises events, batch patches, contract errors,
  FUEL exhaustion, authorized/unauthorized upgrade and populated-state replay.
  An independent state-prefix decoder checks account images and the event-ring
  root. Invalid profiles must reject before committing real execution. Run the
  historical bundle separately; reject its populated state under the candidate.
- **SDK-007 — guest parity.** Distributed guest files match the selected node
  source. Canonical I/O and the seven audited ownership/copy/output assertions
  remain mandatory. All public examples are rebuilt; old contracts stay pinned.
- **SDK-008 — compiler enforcement.** Builder rejects mismatched rustc/Cargo,
  missing locks and unreviewed compiler wrappers before compilation. Existing
  reproducibility, export and heap assertions remain intact; regression tests
  explicitly pin their fixture dependencies before invoking the builder.
- **SDK-009 — platform and dependencies.** Binary ELF dependencies and required
  GLIBC versions are measured from the actual artifacts. Notes declare the
  Linux x86-64-v3 requirement, exact Rust toolchain, installation/build tools,
  actual CLI output formats and unrun production CPU/performance gates.
- **SDK-010 — license scope.** Guest SDK keeps MIT. Host binaries carry the
  exact node owner license with its original scope; resolved dependencies carry
  their own license expressions, source identities and notice texts. Packaging
  grants no additional rights and must fail if required notices are missing.
  `test_license_inputs.py` binds omitted notices to exact upstream commits and
  checks crate archive/source hashes independently against the runtime lock.
  It also requires unchanged Rust 1.93.0 standard-library/vendor and toolchain
  notices from the pinned sysroot; the installer binds their compiler identity.

Retain every failed gate with its cause. Zero matched tests, unavailable evidence,
changed inputs after a gate, and an empty-state smoke are not release passes.

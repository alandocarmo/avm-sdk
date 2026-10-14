# Distribution 0.2.0 / guest SDK 0.1.1

This release is a new immutable snapshot. Its tag and `BUILD-MANIFEST.json`
bind complete node and SDK commits; earlier tags and assets remain unchanged.
The guest patch preserves the public Rust API, canonical input/output, ACAB,
wire format, exports and ownership rules. It incorporates the accepted bounded
input-copy and direct-output paths. Rebuilding can change WASM hashes, selected
code identity and FUEL usage. Installation never rebuilds or upgrades contracts;
publication, selection, authorization and migration remain explicit operations.

## Runtime compatibility and reset guidance

Distribution 0.2.0 has a different runtime/developer-state boundary from the
guest patch. Since the preceding public release:

- Account-state root profile 2 is CompactV1; profile 1 preserves legacy account
  roots. Library and event-ring maps use CompactV1. Account images and AccountHash
  remain unchanged, but roots/proofs cannot be exchanged across profiles. The
  added configuration field changes config_hash even with account profile 1.
- EventLog uses List(Data) of canonical standalone AvmEvent bytes. Inner events
  and proof leaves remain unchanged; aggregate/schema bytes and commitments
  change. Current schema identity is
  `3737122b7e6b58c56f9622500aaf71c3abe6b6432ea726f3958bfb4b94ffde98`.
- Producer-local epoch cancellation is enabled. The revised engine profile
  attests pooling, memory reservation, zero guard size and disabled memory COW.
  Pool capacity remains runtime configuration. Fresh stores, isolation, FUEL,
  deterministic output and rejection precedence remain enforced; cancellation
  cannot publish partial effects.
- Isolated-message admission follows count/byte limits without truncating a
  valid prefix by cumulative paid FUEL allowance.

Use the exact shipped profile and its manifest hash. Never substitute old
profile bytes, reuse compiled caches across identities, or edit state headers
to force acceptance. A proposed profile is experimental until its separate
documentation revision receives owner approval, independent review and adoption
into avm-dev. An experimental profile is not publishable as accepted canon.

There is no approved populated developer-state migration. Keep the old binary,
profile and state together for historical replay. Create a fresh state with
this release, fund the derived addresses and redeploy explicitly. This is local
stand reset guidance, not authorization to reset a production chain or upgrade
user contracts. Historical and current roots/FUEL are not expected to be equal.

## Security and correctness corrections

The installer verifies outer/inner SHA-256 inventories, full-commit/tag identity,
profile, lock, receipt and actual binary hashes before activation. Traversal,
links, devices, duplicate/unlisted files and oversized archives reject. Existing
release directories are immutable; activation atomically replaces `current`.
Unrelated commands are preserved. SHA-256 supplies integrity under the GitHub
release/HTTPS trust boundary, not a separate publisher signature. Historical v1
packages lack the required v2 provenance and are not silently accepted.

The stand includes merged block-start event reads, failed-batch burn accounting
and exhausted-pool fallback settlement. The builder checks full Rust/Cargo 1.93.0
commits, uses the corresponding rust-lld, rejects compiler wrappers, and requires
a reviewed Cargo.lock. It compiles twice with --locked. The three output artifacts
remain unchanged; `target/avm-build-provenance.txt` separately records compiler,
linker, flags and lock identities. New projects must explicitly generate/review
their lock before building. No alternate compiler or runtime fallback is used.

Host builds bind clean source heads, a committed runtime lock, actual Cargo
artifact records, flags, linker and features. Sorted tar entries, normalized
ownership/modes/timestamps and deterministic gzip make packaging reproducible.
The SDK Actions publisher is replaced by manual gates; the previous tests are
retained. Publication requires independent review and green exact-head evidence.

## Platform and license scopes

Host commands require Linux x86-64-v3 and the GNU libc loader: AVX/AVX2, BMI1/2,
CMPXCHG16B, F16C, FMA, LZCNT, MOVBE, POPCNT and the declared SSE features.
`BUILD-MANIFEST.json.platform` gives each actual binary's measured minimum GLIBC
symbol version and DT_NEEDED libraries. This is not a musl/static, ARM, macOS or
Windows distribution. Installation uses Bash, Python 3.11+ and gh; the guide
also uses jq. Contract builds require exact Rust/Cargo 1.93.0, its wasm32 target
and rust-lld, plus the locked dependency cache for offline operation. Native
host-source build dependencies are not required to use the shipped binaries.
Host-source reproduction also requires access to the named private node commit;
the public bundle records that identity and recipe but does not make the node
repository public. Guest source and its build inputs are public.

Guest/public SDK material retains MIT in LICENSE.md. Host binaries retain the
exact owner grant in `licenses/Acki-Nacki-Node-LICENSE.md`, including its original
production restrictions and change-license provisions. This release creates no
new binary grant. THIRD-PARTY-NOTICES.json and bundled texts identify actual
compiled dependencies (including build dependencies) and their own scopes.

## Qualification and remaining limits

The release qualification receipt records actual exact heads, argv, exits,
matched test counts and log hashes. Required gates include AVM preflight,
format/check/clippy, canonical I/O/ownership, builder reproducibility/heap/export
and compiler negatives, allocator/free/FUEL precedence, cancellation/isolation,
compact roots/proof negatives, EventLog vectors/rejections, isolated admission,
stand corrections and the full standard-contract E2E. See the
[finding-to-diff-to-assertion ledger](audit-resolution.md).

The packaged gate independently builds all four public examples with the
shipped builder and exercises the shipped stand: admission/deploy/call, events,
batch patches, errors/FUEL, explicit upgrades and populated replay. It verifies
artifact hashes, account images and all three retained roots. Invalid profile,
old-state/new-profile, package mutation and compiler mismatch negatives must
reject. Post-publication downloads are independently checked. Empty-state smoke
and the presence of a source test do not establish any of these results.

This SDK campaign does not establish the production Intel/AMD/masked-feature/
newer-CPU matrix or registered workload performance. Finite functional checks do
not prove throughput, deadline compliance or production readiness. The stand
uses fixed development identities; examples are regression/learning fixtures,
not audited applications. Actual passed results accompany the public release;
this source document defines their scope without claiming unexecuted gates.

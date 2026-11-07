# External message fee

One contract, two methods:

- `probe.record` stores the SHELL balance the account sees, which is its
  balance after this message's own reserve was taken;
- `probe.refuse` always refuses with status 1400 and changes no state.

`repro.sh` deploys the probe with 100,000,000 SHELL on a fresh local stand,
keeps the block time fixed so storage rent does not move the balance, and has
the stand's `user` key send `probe.refuse` with a growing declared
`fuel_fee_shells`. The `developer` key reads the balance before and after each
refused call. The `user` key has no account and no balance.

```bash
bash examples/external-fee-repro/repro.sh \
  --builder /path/to/avm-contract-build \
  --stand /path/to/avm-local-stand \
  --profile /path/to/wasmtime-release-profile.v1 \
  --out /path/to/new-results
```

`jq` is required. Exit status 1 means every refused call cost the destination
its declared reserve plus 1 SHELL; 0 means at least one did not. On SDK 0.5.1
(`avm-dev-e2c6abe2bbea-sdk-961760f8cd79`):

```text
identity     declared        status       receipt        cost
user         1               1400         2              2
user         100             1400         101            101
user         10000           1400         10001          10001
user         1000000         1400         1000001        1000001
user         98978860        1400         98978861       98978861

account balance after the refused calls: 9 of 100000000 SHELL
```

The refused call is included (`status: ok`, VM status 1400 in
`raw_executor_output`), and the receipt charges the destination the whole
declared reserve plus 1. The reserve is not bounded by what the message FUEL
cap can buy (about 1,032 SHELL at the 0.5.1 defaults). This is the local
stand. `runtime-limits.md` says its ordinary `call` rolls back an error and does
not demonstrate network failure settlement; a contract-level refusal like this
one is not rolled back: it is included and charged.

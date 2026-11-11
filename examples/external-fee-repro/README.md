# External message fee

One contract, two methods:

- `probe.record` stores the SHELL balance the account sees, which is its
  balance after this message's own reserve was taken;
- `probe.refuse` always refuses with status 1400 and changes no state.

The account data is the observed balance (16 bytes) followed by the owner key
(`owner_key_offset = 16`); `init` writes the deploy's `owner_id` there.

`repro.sh` deploys the probe with 100,000,000 SHELL on a fresh local stand,
with the stand's `developer` key as owner, and keeps the block time fixed so
storage rent does not move the balance. The stand's `user` key, a stranger to
the account, sends `probe.refuse` with a growing declared `fuel_fee_shells`;
the owner reads the balance before and after each call. The `user` key has no
account and no balance. This version needs SDK 0.6.0 or later.

```bash
bash examples/external-fee-repro/repro.sh \
  --builder /path/to/avm-contract-build \
  --stand /path/to/avm-local-stand \
  --profile /path/to/wasmtime-release-profile.v1 \
  --out /path/to/new-results
```

`jq` is required. Exit status 0 means every stranger's call cost the
destination nothing; 1 means every one cost the declared reserve plus 1 SHELL;
2 means any other result.

On SDK 0.6.0 (`avm-dev-a86afc46fa2f-sdk-198a7edb5323`) each stranger's call is
dropped at admission:

```text
identity     declared        outcome                receipt        cost
user         1               AccountKeyNotAllowed   -              0
user         100             AccountKeyNotAllowed   -              0
user         10000           AccountKeyNotAllowed   -              0
user         1000000         AccountKeyNotAllowed   -              0
user         99988965        AccountKeyNotAllowed   -              0

account balance after the stranger calls: 99988975 of 100000000 SHELL
```

On SDK 0.5.1 (`avm-dev-e2c6abe2bbea-sdk-961760f8cd79`), before owner keys, the
same calls were included, refused by the contract and charged to the
destination ([gosh-sh/avm-sdk#36](https://github.com/gosh-sh/avm-sdk/issues/36)):

```text
identity     declared        status       receipt        cost
user         1               1400         2              2
user         100             1400         101            101
user         10000           1400         10001          10001
user         1000000         1400         1000001        1000001
user         98978860        1400         98978861       98978861

account balance after the refused calls: 9 of 100000000 SHELL
```

This script measures only the stranger's calls. In a separate token-wallet
probe on SDK 0.6.0, the owner still paid its own calls, the declared reserve
plus 1 SHELL, also for a call the contract refused.

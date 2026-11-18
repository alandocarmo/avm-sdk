# Internal deploy whose init refuses, with SHELL attached

A reproduction for the local stand of SDK 0.6.5 (`avm-dev-d72c855b2eae-sdk-d89a8e7c92c2`).

One contract is parent and child at once. Its `init` accepts constructor data `00` and refuses `01` with status
1400. `probe.spawn` (owner key) sends an internal deploy of the same code, into the parent's DApp, with a callback.
`probe.result` records the callback's status and the SHELL it returned.

| Mode | Constructor data | Deploy carries SHELL | Observed |
|---|---|---|---|
| 2 | `00` (accepts) | yes | callback 0, child created |
| 1 | `01` (refuses) | no (address funded first) | callback 1400 |
| 0 | `01` (refuses) | yes | the parent's call fails as a whole: `local_pipeline_transition_invalid: owner transition differs from the accepted staged effects`; no callback |

Expected in mode 0: the same as mode 1. The deploy fails, the parent's call commits, and the callback brings status
1400 and the SHELL back. A factory cannot tell "the child refused its constructor data" from "my own call failed",
and its own state change (here, nothing) is rolled back with it.

```bash
./repro.sh --out /tmp/init-refusal
```

The script needs `avm-contract-build`, `avm-local-stand` and `jq` from one distribution. It exits 0 when mode 0
answers its callback like mode 1, 1 when the parent's call fails as a whole, and 2 on any other result.

Output on SDK 0.6.5:

```text
== mode 2: the init accepts, the deploy carries SHELL
{"status":"ok","error":null,"status_code":null}
   callbacks=1 status=0 shell_returned=0; child created: yes
== mode 1: the init refuses, no SHELL in the deploy (address funded first)
{"status":"ok","error":null,"status_code":null}
   callbacks=2 status=1400 shell_returned=0
== mode 0: the init refuses, the deploy carries SHELL
{"status":"error","error":"local_pipeline_transition_invalid: owner transition differs from the accepted staged effects","status_code":null}
   callbacks=2 status=1400 shell_returned=0
RESULT: the parent's call failed as a whole (local_pipeline_transition_invalid)
```

#!/usr/bin/env bash
# What does one signed external message cost the account it is sent to?
# A stranger's key sends probe.refuse, which the contract refuses (status
# 1400); the deployer's key sends probe.record, which stores the SHELL balance
# the account sees. The refused call's cost is read from those balances.
#
# Requires avm-contract-build, avm-local-stand and jq, from one distribution.
# Exit status: 0 when no refused call cost the account more than its FUEL,
# 1 when every refused call cost the account its declared reserve + 1 SHELL.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sdk_home="${AVM_SDK_HOME:-${HOME}/.local/share/avm-sdk}"
build="${AVM_CONTRACT_BUILD:-avm-contract-build}"
stand="${AVM_LOCAL_STAND:-avm-local-stand}"
profile="${AVM_WASMTIME_PROFILE:-${sdk_home}/current/profiles/wasmtime-release-profile.v1}"
work="${AVM_EXTERNAL_FEE_WORK:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --builder|--stand|--profile|--out)
      [[ $# -ge 2 && -n $2 ]] || { echo "missing value for $1" >&2; exit 2; }
      case "$1" in
        --builder) build="$2" ;; --stand) stand="$2" ;;
        --profile) profile="$2" ;; --out) work="$2" ;;
      esac
      shift 2 ;;
    --help)
      echo "usage: repro.sh [--builder COMMAND] [--stand COMMAND] [--profile FILE] [--out DIRECTORY]"
      exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ -z $work ]]; then work="$(mktemp -d)"; echo "output: $work"; fi
mkdir -p "$work"
[[ ! -e $work/probe.state ]] || { echo "choose a fresh --out directory" >&2; exit 2; }
state="$work/probe.state"
artifacts="$here/target/avm"
time=1800000000
funding=100000000

fail() { echo "external-fee repro failed: $*" >&2; exit 2; }
run() { "$stand" "$1" --state "$state" --wasmtime-profile "$profile" "${@:2}"; }
le_hex() { printf "%0$(($2 * 2))x" "$1" | fold -w2 | tac | tr -d '\n'; }
# Low 8 bytes of a little-endian u128 (the balances here stay below 2^63).
le_dec() { local h="${1:0:16}" r="" i; for ((i = 14; i >= 0; i -= 2)); do r+="${h:i:2}"; done; echo $((16#$r)); }
clock() { echo --block-time "$time" --created-at "$time" --expire-at "$((time + 3600))" --fuel-fee-shells "$1"; }

"$build" build --manifest-path "$here/Cargo.toml" >"$work/build.log"
run init --dapp-id "$(printf 'e5%.0s' {1..32})" >"$work/init.json"

deploy=(deploy --wasm "$artifacts/contract.wasm" --acab "$artifacts/contract.acab"
  --descriptor "$artifacts/contract.descriptor" --salt-hex 65787465726e616c2d666565 --init-json null)
# shellcheck disable=SC2046
run "${deploy[@]}" $(clock 10000) --quote-address >"$work/quote.json"
address="$(jq -r .address "$work/quote.json")"
run fund-user --address "$address" --shells "$funding" >/dev/null
# shellcheck disable=SC2046
run "${deploy[@]}" $(clock 10000) >"$work/deploy.json"
[[ "$(jq -r .status "$work/deploy.json")" == ok ]] || fail "deploy"

calls=0
# The block time stays fixed, so storage rent does not move the balances.
send() {
  local identity="$1" method="$2" fee="$3" out="$work/call-$((calls++)).json"
  # shellcheck disable=SC2046
  run call --address "$address" --method "$method" --args-json null --identity "$identity" \
    --extra-currencies '' $(clock "$fee") >"$out" || true
  echo "$out"
}
record() {
  local out; out="$(send developer probe.record 1)"
  [[ "$(jq -r .status "$out")" == ok && "$(jq -r '.raw_executor_output[8:16]' "$out")" == 00000000 ]] ||
    fail "probe.record in ${out##*/}"
  le_dec "$(jq -r .raw_state "$out")"
}

# Each record sees the balance after its own reserve was taken, so two
# consecutive records give the cost of one record.
seen="$(record)"
next="$(record)"
reader=$((seen - next))
seen=$next
printf 'account funded with %s SHELL; one probe.record (reserve 1) costs %s\n\n' "$funding" "$reader"
printf '%-12s %-15s %-12s %-14s %-12s\n' identity declared status receipt cost
reproduced=0
total=0
for declared in 1 100 10000 1000000 near; do
  [[ $declared == near ]] && declared=$((seen - reader - 10))
  out="$(send user probe.refuse "$declared")"
  [[ "$(jq -r .status "$out")" == ok ]] || fail "refused call was not included: ${out##*/}"
  [[ "$(jq -r '.raw_executor_output[8:16]' "$out")" == "$(le_hex 1400 4)" ]] || fail "expected status 1400 in ${out##*/}"
  receipt="$(jq -r .receipt.charged_shells "$out")"
  after="$(record)"
  cost=$((seen - after - reader))
  printf '%-12s %-15s %-12s %-14s %-12s\n' user "$declared" 1400 "$receipt" "$cost"
  total=$((total + 1))
  [[ $cost -eq $((declared + 1)) ]] && reproduced=$((reproduced + 1))
  seen=$after
done
printf '\naccount balance after the refused calls: %s of %s SHELL\n' "$seen" "$funding"
run replay >"$work/replay.json"

if [[ $reproduced -eq $total ]]; then
  echo "REPRODUCED: each refused external message cost the destination its declared reserve + 1 SHELL"
  exit 1
fi
echo "NOT REPRODUCED: $reproduced of $total refused calls cost the declared reserve + 1 SHELL"

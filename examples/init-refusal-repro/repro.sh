#!/usr/bin/env bash
# What does the parent see when the init of its internal deploy refuses?
# The probe (src/lib.rs) is deployed externally and sends internal deploys of
# its own code, in its DApp, with a callback:
#   mode 2 (salt 02): the init accepts, the deploy carries SHELL;
#   mode 1 (salt 01): the init refuses (1400), no SHELL (address funded first);
#   mode 0 (salt 03): the init refuses (1400), the deploy carries SHELL.
# Expected for modes 1 and 0: the deploy fails, the parent's call commits, and
# the callback brings status 1400 (and, in mode 0, the SHELL back).
#
# Requires avm-contract-build, avm-local-stand and jq, from one distribution.
# Exit status: 0 when mode 0 answers its callback like mode 1, 1 when the
# parent's call fails as a whole (local_pipeline_transition_invalid), 2 on any
# other result.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sdk_home="${AVM_SDK_HOME:-${HOME}/.local/share/avm-sdk}"
build="${AVM_CONTRACT_BUILD:-avm-contract-build}"
stand="${AVM_LOCAL_STAND:-avm-local-stand}"
profile="${AVM_WASMTIME_PROFILE:-${sdk_home}/current/profiles/wasmtime-release-profile.v1}"
work="${AVM_INIT_REFUSAL_WORK:-}"
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

fail() { echo "init-refusal repro failed: $*" >&2; exit 2; }
run() { "$stand" "$1" --state "$state" --wasmtime-profile "$profile" "${@:2}"; }
clock() { time=$((time + 10)); echo --block-time "$time" --created-at "$time" --expire-at "$((time + 3600))" --fuel-fee-shells "$1"; }
# A little-endian u32 at byte offset $2 of the hex string $1.
u32_at() { local h="${1:$(($2 * 2)):8}"; echo $((16#${h:6:2}${h:4:2}${h:2:2}${h:0:2})); }
# The low 8 bytes of a little-endian u128 at byte offset $2 (SHELL here < 2^63).
u64_at() { local h="${1:$(($2 * 2)):16}" r="" i; for ((i = 14; i >= 0; i -= 2)); do r+="${h:i:2}"; done; echo $((16#$r)); }
has_code() { [[ "$(run inspect --account "$1" | jq -r '.selected_code_id // empty')" =~ [1-9a-f] ]]; }

"$build" build --manifest-path "$here/Cargo.toml" >"$work/build.log"
run init >"$work/init.json"
owner="$(jq -r .identities.developer "$work/init.json")"

# The parent: an external deploy, the first contract of its own DApp.
parent=(--wasm "$artifacts/contract.wasm" --acab "$artifacts/contract.acab" --descriptor "$artifacts/contract.descriptor"
  --salt-hex 00 --init-json '"0x00"' --owner-id "$owner")
run deploy "${parent[@]}" --quote-address $(clock 10000) >"$work/parent-quote.json"
P="$(jq -r .address "$work/parent-quote.json")"
run fund-user --address "$P" --shells 1000000000000 >/dev/null
run deploy "${parent[@]}" $(clock 10000) >"$work/parent.json"
[[ "$(jq -r .status "$work/parent.json")" == ok ]] || fail "parent deploy: $(cat "$work/parent.json")"
dapp="${P%%:*}"
run inspect >"$work/registry.json"
selection="$(jq -r '.prepared_selection_ids[0]' "$work/registry.json")"
package="$(jq -r --arg k "02$selection" '.registry_entries[] | select(.key == $k) | .value[64:128]' "$work/registry.json")"
[[ $package =~ ^[0-9a-f]{64}$ ]] || fail "package id not found"

# The child's account id for a salt and constructor byte: the quote of an
# external deploy over the published code has the same account id.
child() {
  run deploy --package-id "$package" --selected-code-id "$selection" --salt-hex "$1" --init-json "\"0x$2\"" \
    --owner-id "$owner" --quote-address $(clock 10000) | jq -r '.address | split(":")[1]'
}
spawn() {
  local out
  out="$(run call --address "$P" --method probe.spawn --args-json "\"0x$1\"" --identity developer \
    --extra-currencies '' $(clock 100000) 2>"$work/spawn-$1.err" || true)"
  echo "$out" >"$work/spawn-$1.json"
  echo "$out"
}
callback() {
  local raw; raw="$(run inspect --account "$P" | jq -r .raw_state)"
  echo "callbacks=$((16#${raw:368:2})) status=$(u32_at "$raw" 164) shell_returned=$(u64_at "$raw" 168)"
}

echo "== mode 2: the init accepts, the deploy carries SHELL"
spawn 0202 | jq -c '{status, error, status_code}' || true
c2="$dapp:$(child 02 00)"
echo "   $(callback); child created: $(has_code "$c2" && echo yes || echo no)"

echo "== mode 1: the init refuses, no SHELL in the deploy (address funded first)"
c1="$dapp:$(child 01 01)"
run fund-user --address "$c1" --shells 1000000000 >/dev/null
spawn 0101 | jq -c '{status, error, status_code}' || true
echo "   $(callback)"

echo "== mode 0: the init refuses, the deploy carries SHELL"
out0="$(spawn 0003)"
echo "$out0" | jq -c '{status, error, status_code}'
echo "   $(callback)"

raw="$(run inspect --account "$P" | jq -r .raw_state)"
if [[ "$(u32_at "$raw" 164)" == 1400 && $((16#${raw:368:2})) == 3 ]]; then
  echo "RESULT: mode 0 answered its callback with 1400"; exit 0
elif grep -q local_pipeline_transition_invalid <<<"$out0"; then
  echo "RESULT: the parent's call failed as a whole (local_pipeline_transition_invalid)"; exit 1
fi
echo "RESULT: unexpected; see $work"; exit 2

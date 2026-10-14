#!/usr/bin/env python3
"""Black-box release gate. Only bundle files and public CLI inputs are used.

Run on the qualification pod with an isolated writable copy of the verified
bundle. Output goes to the supplied evidence directory, never to source Git.
No missing binary, missing case, or empty test selection is a success.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

import blake3 as reference_blake3


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blake3(data):
    return reference_blake3.blake3(data).digest()


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def read_state(path):
    """Independent read-only decoder of the published developer-state prefix."""
    data = path.read_bytes()
    assert data[:8] == b"AVMLSF01"
    position = 308  # magic/version/config/DApp/identities/roots/clock/fees/counters
    for width in (33, 32, 32):
        count = u32(data, position)
        position += 4
        for _ in range(count):
            position += width
            size = u32(data, position)
            position += 4 + size
    count = u32(data, position)
    position += 4
    for _ in range(count):
        position += 64 + 16 + 8
        count_extra = u32(data, position)
        position += 4 + count_extra * 20
    count = u32(data, position)
    position += 4
    accounts = {}
    for _ in range(count):
        address = data[position:position + 64]
        position += 64
        account_hash = data[position:position + 32]
        position += 32 + 8 + 4 + 16 + 1
        size = u32(data, position)
        position += 4
        image = data[position:position + size]
        position += size
        assert blake3(image) == account_hash
        extras = int.from_bytes(image[49:51], "little")
        start = 51 + 20 * extras
        state_size = u32(image, start)
        assert start + 4 + state_size == len(image)
        accounts[address.hex()[:64] + ":" + address.hex()[64:]] = {
            "selected_code_id": image[1:33].hex(),
            "image": image.hex(),
            "data": image[start + 4:],
        }
    nonces = u32(data, position)
    position += 4 + nonces * 104
    assert u32(data, position) == 0, "successful public operations must drain the queue"
    position += 4
    receipts = u32(data, position)
    position += 4 + receipts * 136
    count = u32(data, position)
    position += 4
    assert count == receipts
    transcript = []
    for _ in range(count):
        size = u32(data, position)
        position += 4
        wire = data[position:position + size]
        assert len(wire) == size
        transcript.append(wire)
        position += size
    return accounts, data[204:236].hex(), transcript


def read_accounts(path):
    return read_state(path)[:2]


def wasm_facts(data, batch):
    """Independent section decoder; no builder/admission implementation is imported."""
    def leb(position):
        value = 0
        for shift in range(0, 35, 7):
            byte = data[position]
            position += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, position
        raise AssertionError("oversized WASM integer")

    assert data[:8] == b"\0asm\1\0\0\0"
    position, exports, memory = 8, {}, None
    while position < len(data):
        section = data[position]
        size, position = leb(position + 1)
        end = position + size
        assert end <= len(data) and section != 0, "custom/truncated section survived admission"
        if section == 2:
            count, _ = leb(position)
            assert count == 0, "imports are forbidden"
        if section == 5:
            count, cursor = leb(position)
            flags, cursor = leb(cursor)
            minimum, cursor = leb(cursor)
            maximum, cursor = leb(cursor)
            assert count == 1 and flags == 1 and cursor == end
            assert 1 <= minimum <= maximum == 512
            memory = {"minimum_bytes": minimum * 65536, "maximum_bytes": maximum * 65536}
        if section == 7:
            count, cursor = leb(position)
            for _ in range(count):
                length, cursor = leb(cursor)
                name = data[cursor:cursor + length].decode()
                cursor += length
                kind = data[cursor]
                index, cursor = leb(cursor + 1)
                assert name not in exports
                exports[name] = {"kind": kind, "index": index}
            assert cursor == end
        position = end
    required = {"memory", "avm_guest_alloc", "avm_guest_free", "init", "process_message"}
    if batch:
        required.add("process_batch")
    assert set(exports) == required and memory is not None
    assert exports["memory"] == {"kind": 2, "index": 0}
    assert all(value["kind"] == 0 for key, value in exports.items() if key != "memory")
    return {"memory": memory, "exports": exports}


class Journey:
    def __init__(self, bundle, evidence, label):
        self.bundle = bundle
        self.evidence = evidence / label
        self.evidence.mkdir(parents=True)
        self.state = self.evidence / "developer.state"
        self.profile = bundle / "profiles/wasmtime-release-profile.v1"
        self.sequence = 0
        self.time = 1_800_000_000

    def run(self, command, expected=0, **options):
        argv = [str(self.bundle / "bin/avm-local-stand"), command,
                "--state", str(self.state), "--wasmtime-profile", str(self.profile)]
        for key, value in options.items():
            argv.extend(["--" + key.replace("_", "-"), str(value)])
        process = subprocess.run(argv, capture_output=True)
        self.sequence += 1
        stem = self.evidence / f"{self.sequence:03}-{command}"
        stem.with_suffix(".stdout").write_bytes(process.stdout)
        stem.with_suffix(".stderr").write_bytes(process.stderr)
        stem.with_suffix(".argv.json").write_text(json.dumps(argv))
        assert process.returncode == expected, (argv, process.returncode, process.stderr.decode(), process.stdout.decode())
        value = json.loads(process.stdout)
        expected_status = "ok" if expected == 0 else "error"
        if expected == 2 and value.get("error") == "funding_required":
            expected_status = "precondition"
        assert value["status"] == expected_status, value
        return value

    def timing(self, fee=1):
        self.time += 1
        return dict(block_time=self.time, created_at=self.time,
                    expire_at=self.time + 3600, fuel_fee_shells=fee)

    def deploy(self, example, command="deploy", **extra):
        artifacts = self.bundle / "examples" / example / "target/avm"
        options = dict(wasm=artifacts / "contract.wasm", acab=artifacts / "contract.acab",
                       descriptor=artifacts / "contract.descriptor", salt_hex=example.encode().hex(),
                       init_json="null", **self.timing(), **extra)
        before = self.state.read_bytes()
        missing = self.run(command, expected=2, **options)
        assert missing["error"] == "funding_required"
        assert self.state.read_bytes() == before, "unfunded deploy must have no state effects"
        address = missing["address"]
        self.run("fund-user", address=address, shells=100000)
        deployed = self.run(command, **options)
        assert int(deployed["receipt"]["charged_shells"]) > 0
        assert address in read_accounts(self.state)[0]
        return address

    def call(self, address, method, args=None, **extra):
        return self.run("call", address=address, method=method, args_json=json.dumps(args),
                        identity="user", value_main=extra.pop("value_main", 0),
                        extra_currencies=extra.pop("extra_currencies", ""),
                        **self.timing(), **extra)

    def replay(self):
        inspected = self.run("inspect")
        accounts, ring = read_accounts(self.state)
        before = self.state.read_bytes()
        replayed = self.run("replay")
        for key in ("registry_root", "account_state_root", "cumulative_fee_shells", "accepted_input_count"):
            assert replayed[key] == inspected[key], key
        assert replayed["receipt_count"] == len(inspected["receipts"]) > 0
        assert read_accounts(self.state) == (accounts, ring)
        assert self.state.read_bytes() == before
        (self.evidence / "roots.json").write_text(json.dumps({
            "account_state_root": inspected["account_state_root"],
            "registry_root": inspected["registry_root"], "internal_event_ring_root": ring,
            "receipts": inspected["receipts"], "cumulative_fee_shells": inspected["cumulative_fee_shells"],
        }, indent=2))


def run(bundle, evidence, incompatible_state=None, wrong_rustc=None):
    evidence.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((bundle / "BUILD-MANIFEST.json").read_bytes())
    current = manifest["schema"] == "gosh.avm-sdk.release.v2"
    if current:
        assert incompatible_state is not None, "candidate qualification requires a populated historical state"
        assert wrong_rustc is not None, "candidate qualification requires a real different compiler"
        version = subprocess.run([str(wrong_rustc), "-vV"], capture_output=True, check=True)
        (evidence / "wrong-rustc-version.txt").write_bytes(version.stdout)
        assert b"release: " in version.stdout and b"release: 1.93.0\n" not in version.stdout
        negative = bundle / "examples/compiler-mismatch"
        shutil.copytree(bundle / "examples/contract-a-v1", negative, ignore=shutil.ignore_patterns("target"))
        env = os.environ.copy()
        env["RUSTC"] = str(wrong_rustc)
        result = subprocess.run([str(bundle / "bin/avm-contract-build"), "build", "--manifest-path", str(negative / "Cargo.toml")], env=env, capture_output=True)
        (evidence / "compiler-mismatch.stderr").write_bytes(result.stderr)
        assert result.returncode != 0 and b"required rustc 1.93.0" in result.stderr
        assert not (negative / "target").exists()
    if incompatible_state is not None:
        before = incompatible_state.read_bytes()
        command = [str(bundle / "bin/avm-local-stand"), "inspect", "--state", str(incompatible_state),
                   "--wasmtime-profile", str(bundle / "profiles/wasmtime-release-profile.v1")]
        rejection = subprocess.run(command, capture_output=True)
        (evidence / "old-state-new-profile.stdout").write_bytes(rejection.stdout)
        (evidence / "old-state-new-profile.stderr").write_bytes(rejection.stderr)
        assert rejection.returncode == 2
        assert json.loads(rejection.stdout)["status"] == "error"
        assert incompatible_state.read_bytes() == before
    artifacts, admission = {}, {}
    for example in ("contract-a-v1", "contract-a-v2", "contract-b", "contract-batch-patch"):
        root = bundle / "examples" / example
        process = subprocess.run([str(bundle / "bin/avm-contract-build"), "build", "--manifest-path", str(root / "Cargo.toml")], capture_output=True)
        (evidence / f"{example}-build.stdout").write_bytes(process.stdout)
        (evidence / f"{example}-build.stderr").write_bytes(process.stderr)
        assert process.returncode == 0, process.stderr.decode()
        directory = root / "target/avm"
        assert sorted(p.name for p in directory.iterdir()) == ["contract.acab", "contract.descriptor", "contract.wasm"]
        values = {p.name: p.read_bytes() for p in directory.iterdir()}
        expected = [("contract.wasm", blake3(values["contract.wasm"])),
                    ("contract.acab", blake3(b"AVM_ABI" + values["contract.acab"])),
                    ("contract.descriptor", blake3(values["contract.descriptor"]))]
        assert process.stdout.decode().splitlines() == [f"{name} {digest.hex()}" for name, digest in expected]
        assert values["contract.wasm"][:8] == b"\0asm\1\0\0\0"
        assert values["contract.acab"][:4] == b"ACAB"
        assert len(values["contract.descriptor"]) == 66
        artifacts[example] = {name: sha(data) for name, data in values.items()}
        admission[example] = wasm_facts(values["contract.wasm"], example == "contract-batch-patch")
    assert artifacts["contract-a-v1"]["contract.acab"] == artifacts["contract-a-v2"]["contract.acab"]
    assert artifacts["contract-a-v1"]["contract.descriptor"] == artifacts["contract-a-v2"]["contract.descriptor"]
    assert artifacts["contract-a-v1"]["contract.wasm"] != artifacts["contract-a-v2"]["contract.wasm"]
    (evidence / "artifacts.json").write_text(json.dumps(artifacts, indent=2))
    (evidence / "admission.json").write_text(json.dumps(admission, indent=2))

    version = Journey(bundle, evidence, "version-upgrade")
    version.run("init", dapp_id="11" * 32)
    address = version.deploy("contract-a-v1")
    call = version.call(address, "causal.a.version")
    assert bytes.fromhex(call["raw_state"])[16] == 1
    assert bytes.fromhex(call["raw_executor_output"])[:4] == b"ACMO"
    assert u32(bytes.fromhex(call["raw_executor_output"]), 4) == 0
    assert call["state_decode_error"] is None
    before_oog = version.state.read_bytes()
    exhausted = version.run("call", expected=2, address=address, method="causal.a.version",
                            args_json="null", identity="user", value_main=0, extra_currencies="",
                            **version.timing(fee=0))
    assert "VM status 31" in exhausted["error"], exhausted
    assert version.state.read_bytes() == before_oog, "out-of-FUEL external call must not publish effects"
    upgraded = version.deploy("contract-a-v2", command="publish-version")
    selected = read_accounts(version.state)[0][upgraded]["selected_code_id"]
    before = read_accounts(version.state)[0][address]["data"]
    # Authorization is exercised by real WASM, not merely CLI argument parsing.
    before_selection = read_accounts(version.state)[0][address]["selected_code_id"]
    rejected = version.run("upgrade", expected=2, address=address, selected_code_id=selected,
                migration_payload_hex=b"avm-a-v2-migrate-state!!".hex(),
                identity="user", **version.timing())
    assert "did not switch" in rejected["error"]
    assert read_accounts(version.state)[0][address]["data"] == before
    assert read_accounts(version.state)[0][address]["selected_code_id"] == before_selection
    rejected = version.run("upgrade", expected=2, address=address, selected_code_id=selected,
                migration_payload_hex=b"avm-a-v2-migrate-state!?".hex(),
                identity="package-owner", **version.timing())
    assert "did not switch" in rejected["error"]
    assert read_accounts(version.state)[0][address]["data"] == before
    assert read_accounts(version.state)[0][address]["selected_code_id"] == before_selection
    version.run("upgrade", address=address, selected_code_id=selected,
                migration_payload_hex=b"avm-a-v2-migrate-state!!".hex(),
                identity="package-owner", **version.timing())
    assert read_accounts(version.state)[0][address]["selected_code_id"] == selected
    assert bytes.fromhex(version.call(address, "causal.a.version")["raw_state"])[16] == 2
    version.replay()

    for destination_example, callback in [("contract-b", True), ("contract-batch-patch", False)]:
        journey = Journey(bundle, evidence, destination_example)
        journey.run("init", dapp_id="11" * 32)
        sender = journey.deploy("contract-a-v1")
        receiver = journey.deploy(destination_example, command="publish-version")
        ring_before = read_accounts(journey.state)[1]
        events_before = journey.run("events")["events"]
        method = "causal.b.receive" if callback else "causal.batch.patch"
        body = bytes.fromhex(receiver.replace(":", "")) + blake3(method.encode())[:8]
        body += blake3(b"causal.a.callback")[:8] if callback else bytes(8)
        journey.call(sender, "causal.a.forward", "0x" + body.hex(), value_main=100, extra_currencies="7:40")
        accounts, ring = read_accounts(journey.state)
        state = accounts[receiver]["data"]
        if callback:
            assert int.from_bytes(state[:8], "little") == 1
            sender_state = accounts[sender]["data"]
            assert int.from_bytes(sender_state[8:16], "little") == 1, "requested callback was not delivered"
            assert u32(sender_state, 16) == 0 and u32(sender_state, 20) == 16
            assert sender_state[24:40] == bytes([0x99]) * 16
            events = journey.run("events")
            assert events["count"] == len(events_before) + 2 == len(events["events"])
            assert events["events"][:len(events_before)] == events_before
            assert ring != ring_before
        else:
            assert len(state) == 65540 and u32(state, 0) == 65536
            assert int.from_bytes(state[4:12], "little") == 1
            assert state[12:] == bytes(65528)
            failed = journey.call(receiver, "causal.batch.patch")
            assert u32(bytes.fromhex(failed["raw_executor_output"]), 4) == 1401
            assert read_accounts(journey.state)[0][receiver]["data"] == state
        journey.replay()

    # Build the public receiver's existing failure switches with the packaged
    # builder. The original four builds above stay unmodified and independently
    # identified. No private fixture or prebuilt WASM is substituted.
    negative = bundle / "examples/contract-b-failure"
    shutil.copytree(bundle / "examples/contract-b", negative, ignore=shutil.ignore_patterns("target"))
    env = os.environ.copy()
    env["AVM_E2E_FAILURE_FLOW"] = "1"
    built = subprocess.run([str(bundle / "bin/avm-contract-build"), "build", "--manifest-path", str(negative / "Cargo.toml")], env=env, capture_output=True)
    (evidence / "failure-build.stdout").write_bytes(built.stdout)
    (evidence / "failure-build.stderr").write_bytes(built.stderr)
    assert built.returncode == 0, built.stderr.decode()
    failure_wasm = (negative / "target/avm/contract.wasm").read_bytes()
    assert sha(failure_wasm) != artifacts["contract-b"]["contract.wasm"]
    wasm_facts(failure_wasm, False)
    (evidence / "failure-artifacts.json").write_text(json.dumps({p.name: sha(p.read_bytes()) for p in (negative / "target/avm").iterdir()}, indent=2))
    for label, method, handler, status in [
        ("trap", "causal.b.drain", (0xc2c6c51bc610249d).to_bytes(8, "little"), 30),
        ("out-of-fuel", "causal.b.drain", blake3(b"causal.a.version")[:8], 31),
        ("invalid-event", "causal.b.receive", (0xc2c6c51bc610249d).to_bytes(8, "little"), 44),
    ]:
        journey = Journey(bundle, evidence, label)
        journey.run("init", dapp_id="11" * 32)
        sender = journey.deploy("contract-a-v1")
        receiver = journey.deploy("contract-b-failure", command="publish-version")
        before_accounts, before_ring, before_wires = read_state(journey.state)
        before_events = journey.run("events")["events"]
        body = bytes.fromhex(receiver.replace(":", "")) + blake3(method.encode())[:8] + handler
        called = journey.call(sender, "causal.a.forward", "0x" + body.hex(), value_main=100, extra_currencies="7:40")
        assert u32(bytes.fromhex(called["raw_executor_output"]), 4) == 0
        accounts, ring, wires = read_state(journey.state)
        assert len(wires) == len(before_wires) + 3
        delivered, callback = wires[-2:]
        assert delivered[64:128] == bytes.fromhex(sender.replace(":", ""))
        assert delivered[128:192] == bytes.fromhex(receiver.replace(":", ""))
        assert callback[:4] == b"\0\1\1\0" and len(callback) == 312
        assert callback[8:16] == handler
        assert callback[16:32] == (37).to_bytes(16, "little")
        assert callback[64:128] == delivered[128:192] and callback[128:192] == delivered[64:128]
        assert callback[192:224] == blake3(delivered)
        assert callback[224:288] == delivered[64:128]
        assert u32(callback, 288) == status
        assert callback[292:] == (7).to_bytes(4, "little") + (11).to_bytes(16, "little")
        assert accounts[receiver]["data"] == before_accounts[receiver]["data"]
        assert accounts[receiver]["selected_code_id"] == before_accounts[receiver]["selected_code_id"]
        assert accounts[sender]["data"] == (1).to_bytes(8, "little") + bytes(64)
        assert ring == before_ring and journey.run("events")["events"] == before_events
        journey.replay()

    invalid = Journey(bundle, evidence, "invalid-profile")
    invalid.profile = invalid.evidence / "invalid.v1"
    invalid.profile.write_bytes((bundle / "profiles/wasmtime-release-profile.v1").read_bytes() + b"unapproved=true\n")
    # Current init is allowed to reject early; if it accepts, actual deploy must reject.
    argv = [str(bundle / "bin/avm-local-stand"), "init", "--state", str(invalid.state),
            "--wasmtime-profile", str(invalid.profile), "--dapp-id", "11" * 32]
    process = subprocess.run(argv, capture_output=True)
    if process.returncode == 0:
        root = bundle / "examples/contract-a-v1/target/avm"
        options = dict(wasm=root / "contract.wasm", acab=root / "contract.acab",
                       descriptor=root / "contract.descriptor", salt_hex="01", init_json="null", **invalid.timing())
        rejection = invalid.run("deploy", expected=2, **options)
        if rejection.get("error") == "funding_required":
            invalid.run("fund-user", address=rejection["address"], shells=100000)
            before = invalid.state.read_bytes()
            rejection = invalid.run("deploy", expected=2, **options)
            assert invalid.state.read_bytes() == before
        assert "profile" in rejection["error"].lower(), rejection
    else:
        assert process.returncode == 2
    (evidence / "passed.json").write_text(json.dumps({"passed": True, "bundle": str(bundle)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--incompatible-state", type=Path)
    parser.add_argument("--wrong-rustc", type=Path)
    arguments = parser.parse_args()
    run(arguments.bundle.resolve(), arguments.evidence.resolve(), arguments.incompatible_state, arguments.wrong_rustc)

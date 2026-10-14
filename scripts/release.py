#!/usr/bin/env python3
"""Verify release contents before extraction and atomically activate an immutable tag."""

import argparse
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile
import uuid

ROOT = "avm-sdk-linux-x86_64"
ARCHIVE = ROOT + ".tar.gz"
BINARIES = ("avm-contract-build", "avm-local-stand")
TAG = re.compile(r"avm-dev-([0-9a-f]{12})-sdk-([0-9a-f]{12})\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
MAX_FILES = 20000
MAX_BYTES = 256 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def json_object(data):
    result = json.loads(data, object_pairs_hook=unique_pairs)
    require(isinstance(result, dict), "expected a JSON object")
    return result


def safe_path(name):
    path = PurePosixPath(name)
    require(name and "\\" not in name and "\x00" not in name, "unsafe archive path")
    require(not path.is_absolute() and all(p not in ("", ".", "..") for p in name.split("/")), "unsafe archive path")
    require(str(path) == name, "noncanonical archive path")
    return path


def checksums(data):
    result = {}
    for line in data.decode("ascii").splitlines():
        require(len(line) > 66 and line[64:66] == "  ", "invalid checksum line")
        value, name = line[:64], line[66:]
        require(SHA.fullmatch(value), "invalid SHA-256")
        safe_path(name)
        require(name not in result, "duplicate checksum path")
        result[name] = value
    require(result, "empty checksum list")
    return result


def verify_payload(files, tag):
    match = TAG.fullmatch(tag)
    require(match, "invalid immutable release tag")
    require("SHA256SUMS" in files, "missing inner checksum list")
    expected = checksums(files["SHA256SUMS"])
    require(set(expected) == set(files) - {"SHA256SUMS"}, "inner checksum inventory differs from payload")
    for name, value in expected.items():
        require(digest(files[name]) == value, f"inner checksum mismatch: {name}")
    required = {"BUILD-MANIFEST.json", "provenance/build.json", "provenance/runtime.Cargo.lock", "provenance/cargo-artifacts.jsonl",
                "profiles/wasmtime-release-profile.v1", "LICENSE.md",
                "licenses/Acki-Nacki-Node-LICENSE.md", "THIRD-PARTY-NOTICES.json"}
    required.update("bin/" + name for name in BINARIES)
    require(required <= files.keys(), "missing required release payload")
    manifest = json_object(files["BUILD-MANIFEST.json"])
    require(manifest.get("schema") == "gosh.avm-sdk.release.v2", "unsupported release manifest schema")
    source, sdk = manifest.get("ackinacki_commit", ""), manifest.get("sdk_commit", "")
    require(COMMIT.fullmatch(source) and COMMIT.fullmatch(sdk), "manifest requires full source and SDK commits")
    require(source.startswith(match[1]) and sdk.startswith(match[2]), "manifest/tag commit mismatch")
    require(manifest.get("release_tag") == tag, "manifest release tag mismatch")
    for field, name in {
        "wasmtime_profile_sha256": "profiles/wasmtime-release-profile.v1",
        "runtime_lock_sha256": "provenance/runtime.Cargo.lock",
        "build_receipt_sha256": "provenance/build.json",
        "third_party_notices_sha256": "THIRD-PARTY-NOTICES.json",
    }.items():
        require(manifest.get(field) == digest(files[name]), f"manifest identity mismatch: {field}")
    receipt = json_object(files["provenance/build.json"])
    require(receipt.get("schema") == "gosh.avm-sdk.build.v1", "unsupported build receipt")
    require(receipt.get("source_commit") == source and receipt.get("sdk_commit") == sdk, "build/source commit mismatch")
    require(receipt.get("profile_sha256") == manifest["wasmtime_profile_sha256"], "build/profile mismatch")
    require(receipt.get("lock_sha256") == manifest["runtime_lock_sha256"], "build/lock mismatch")
    binaries = {"bin/" + name: digest(files["bin/" + name]) for name in BINARIES}
    require(receipt.get("binaries") == binaries, "built binary identity mismatch")
    artifact_bytes = files["provenance/cargo-artifacts.jsonl"]
    require(digest(artifact_bytes) == receipt.get("compiler_artifacts_sha256"), "Cargo build record identity mismatch")
    artifact_records(artifact_bytes)
    notices = json_object(files["THIRD-PARTY-NOTICES.json"])
    require(notices.get("packages"), "missing dependency license inventory")
    for package in notices["packages"]:
        require(package.get("texts"), "missing dependency license text")
        for text in package["texts"]:
            name = text["file"]
            require(name.startswith("licenses/") and name in files, "license text is absent")
            require(digest(files[name]) == text["sha256"], "license text identity mismatch")
    return manifest


def artifact_records(data):
    records = [json_object(line) for line in data.splitlines() if line.strip()]
    require(records and records[-1].get("reason") == "build-finished" and records[-1].get("success") is True, "Cargo build did not finish successfully")
    result = {}
    for name in BINARIES:
        matches = [row for row in records if row.get("reason") == "compiler-artifact"
                   and row.get("target", {}).get("name") == name and row.get("executable")]
        require(len(matches) == 1 and matches[0]["target"].get("kind") == ["bin"], "missing or ambiguous binary build record")
        require(Path(matches[0]["executable"]).name == name, "Cargo executable name mismatch")
        result[name] = matches[0]
    return result


def verify_archive(archive, checksum, tag):
    """Check every hash, path and type before returning any extractable bytes."""
    archive, checksum = Path(archive), Path(checksum)
    require(TAG.fullmatch(tag), "invalid immutable release tag")
    require(archive.name == ARCHIVE, "unexpected archive name")
    require(archive.stat().st_size <= MAX_BYTES, "compressed archive exceeds limit")
    require(checksum.stat().st_size <= 4096, "outer checksum file exceeds limit")
    outer = checksums(checksum.read_bytes())
    require(set(outer) == {ARCHIVE}, "outer checksum must name exactly this archive")
    require(file_digest(archive) == outer[ARCHIVE], "outer archive checksum mismatch")
    # Bound the complete tar stream before tarfile interprets a PAX/long-name
    # header, whose declared metadata length is otherwise parsed internally.
    with gzip.open(archive, "rb") as compressed:
        expanded = compressed.read(MAX_BYTES + 1)
    require(len(expanded) <= MAX_BYTES, "expanded tar stream exceeds limit")
    files, seen, total = {}, set(), 0
    try:
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as source:
            members = []
            for member in source:
                require(len(members) < MAX_FILES, "archive member count exceeds limit")
                name = member.name.rstrip("/") if member.isdir() else member.name
                path = safe_path(name)
                require(path.parts[0] == ROOT, "archive member outside bundle root")
                require(name not in seen, "duplicate archive member")
                seen.add(name)
                require(member.isfile() or member.isdir(), "links and special archive members are forbidden")
                require(not member.issparse() and not member.pax_headers, "extended archive metadata is forbidden")
                require(not member.mode & 0o7000, "privileged archive permissions are forbidden")
                require(member.size >= 0, "negative archive member size")
                total += member.size
                require(total <= MAX_BYTES, "expanded archive exceeds limit")
                if member.isfile():
                    require(len(path.parts) > 1, "bundle root must be a directory")
                members.append(member)
            # No tar extraction primitive is used. All member metadata passed first.
            for member in members:
                if member.isfile():
                    with source.extractfile(member) as stream:
                        data = stream.read(member.size + 1)
                    require(len(data) == member.size, "truncated archive member")
                    files[str(PurePosixPath(member.name).relative_to(ROOT))] = data
    except (tarfile.TarError, EOFError) as error:
        raise ValueError(f"invalid release archive: {error}") from error
    for name in files:
        require(not any(str(parent) in files for parent in PurePosixPath(name).parents), "file/directory collision")
    verify_payload(files, tag)
    return files


def verify_tree(root, expected):
    actual = {}
    require(root.is_dir() and not root.is_symlink(), "existing release is not a real directory")
    for path in root.rglob("*"):
        require(not path.is_symlink(), "existing release contains a link")
        if path.is_file():
            actual[path.relative_to(root).as_posix()] = file_digest(path)
        else:
            require(path.is_dir(), "existing release contains a special file")
    require(actual == {name: digest(data) for name, data in expected.items()}, "immutable release already exists with different contents")


def atomic_link(target, destination):
    temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def install_archive(archive, checksum, tag, sdk_home, bin_dir):
    files = verify_archive(archive, checksum, tag)
    sdk_home, bin_dir = Path(sdk_home).absolute(), Path(bin_dir).absolute()
    sdk_home.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(sdk_home / ".install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = sdk_home / "current"
        require(not current.exists() or current.is_symlink(), "current is a user-owned directory or file")
        previous = {}
        for name in BINARIES:
            command = bin_dir / name
            target = str(current / "bin" / name)
            if command.is_symlink():
                require(os.readlink(command) == target, f"refusing to replace unrelated command: {command}")
                previous[command] = os.readlink(command)
            else:
                require(not command.exists(), f"refusing to replace user command: {command}")
                previous[command] = None
        releases = sdk_home / "releases"
        require(not releases.is_symlink(), "release directory cannot be a symlink")
        releases.mkdir(exist_ok=True)
        destination = releases / tag
        root = destination / ROOT
        if destination.exists() or destination.is_symlink():
            require(not destination.is_symlink(), "existing release cannot be a symlink")
            verify_tree(root, files)
        else:
            with tempfile.TemporaryDirectory(prefix=".stage-", dir=releases) as temporary:
                staged = Path(temporary) / ROOT
                staged.mkdir()
                for name, data in files.items():
                    path = staged / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        stream.write(data)
                    executable = name in {"bin/" + b for b in BINARIES} or name.endswith(".sh")
                    path.chmod(0o755 if executable else 0o644)
                verify_tree(staged, files)
                os.rename(temporary, destination)
        bin_dir.mkdir(parents=True, exist_ok=True)
        changed = []
        try:
            for command in previous:
                atomic_link(str(current / "bin" / command.name), command)
                changed.append(command)
            atomic_link(str(root), current)
        except OSError:
            for command in reversed(changed):
                if previous[command] is None:
                    command.unlink(missing_ok=True)
                else:
                    atomic_link(previous[command], command)
            raise
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify", "install"])
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sdk-home", type=Path)
    parser.add_argument("--bin-dir", type=Path)
    args = parser.parse_args()
    if args.command == "verify":
        files = verify_archive(args.archive, args.checksum, args.tag)
        print(json.dumps({"verified": True, "tag": args.tag, "files": len(files), "sha256": file_digest(args.archive)}))
    else:
        require(args.sdk_home and args.bin_dir, "installation paths are required")
        root = install_archive(args.archive, args.checksum, args.tag, args.sdk_home, args.bin_dir)
        print(f"installed verified {args.tag} under {root}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"avm-sdk: {error}") from error

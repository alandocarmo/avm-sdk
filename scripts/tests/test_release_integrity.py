"""Independent hostile-package fixtures; never use the user's home or commands."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "release", Path(__file__).resolve().parents[1] / "release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
ROOT = "avm-sdk-linux-x86_64"
SOURCE = "1" * 40
SDK = "2" * 40
TAG = f"avm-dev-{SOURCE[:12]}-sdk-{SDK[:12]}"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def payload():
    files = {
        "bin/avm-contract-build": b"builder fixture\n",
        "bin/avm-local-stand": b"stand fixture\n",
        "profiles/wasmtime-release-profile.v1": b"profile fixture\n",
        "provenance/runtime.Cargo.lock": b"lock fixture\n",
        "LICENSE.md": b"SDK MIT license fixture\n",
        "licenses/Acki-Nacki-Node-LICENSE.md": b"Owner grant fixture\n",
        "licenses/toolchain/COPYRIGHT-library.html": b"Rust and standard-library vendor grants\n",
    }
    files["THIRD-PARTY-NOTICES.json"] = json_bytes({"packages": [{"name": "fixture", "texts": [{
        "file": "licenses/Acki-Nacki-Node-LICENSE.md",
        "sha256": digest(files["licenses/Acki-Nacki-Node-LICENSE.md"]),
    }]}], "rust_toolchain": {"rustc_verbose": "rustc exact fixture", "texts": [{
        "file": "licenses/toolchain/COPYRIGHT-library.html",
        "sha256": digest(files["licenses/toolchain/COPYRIGHT-library.html"]),
    }]}})
    files["provenance/cargo-artifacts.jsonl"] = b"".join(json_bytes({
        "reason": "compiler-artifact", "target": {"name": name, "kind": ["bin"]},
        "executable": f"/qualification-target/release/{name}",
    }) for name in ("avm-contract-build", "avm-local-stand")) + json_bytes({"reason": "build-finished", "success": True})
    receipt = {
        "schema": "gosh.avm-sdk.build.v1",
        "source_commit": SOURCE,
        "sdk_commit": SDK,
        "profile_sha256": digest(files["profiles/wasmtime-release-profile.v1"]),
        "lock_sha256": digest(files["provenance/runtime.Cargo.lock"]),
        "compiler_artifacts_sha256": digest(files["provenance/cargo-artifacts.jsonl"]),
        "rustc_verbose": "rustc exact fixture",
        "binaries": {name: digest(data) for name, data in files.items() if name.startswith("bin/")},
    }
    files["provenance/build.json"] = json_bytes(receipt)
    files["BUILD-MANIFEST.json"] = json_bytes({
        "schema": "gosh.avm-sdk.release.v2",
        "release_tag": TAG,
        "ackinacki_commit": SOURCE,
        "sdk_commit": SDK,
        "wasmtime_profile_sha256": receipt["profile_sha256"],
        "runtime_lock_sha256": receipt["lock_sha256"],
        "build_receipt_sha256": digest(files["provenance/build.json"]),
        "third_party_notices_sha256": digest(files["THIRD-PARTY-NOTICES.json"]),
    })
    return files


def write_archive(directory, files=None, extra=(), corrupt_inner=False):
    files = payload() if files is None else files.copy()
    sums = "".join(f"{digest(data)}  {name}\n" for name, data in sorted(files.items()))
    files["SHA256SUMS"] = sums.encode()
    if corrupt_inner:
        files["bin/avm-local-stand"] += b"tampered"
    archive = directory / f"{ROOT}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for name, data in files.items():
            member = tarfile.TarInfo(f"{ROOT}/{name}")
            member.size = len(data)
            member.mode = 0o755 if name.startswith("bin/") else 0o644
            output.addfile(member, io.BytesIO(data))
        for member, data in extra:
            output.addfile(member, io.BytesIO(data))
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest(archive.read_bytes())}  {archive.name}\n")
    return archive, checksum


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sdk_home = self.root / "sdk"
        self.bin_dir = self.root / "bin"
        self.sdk_home.mkdir()
        self.previous = self.sdk_home / "previous"
        self.previous.mkdir()
        (self.sdk_home / "current").symlink_to(self.previous, target_is_directory=True)

    def install(self, archive, checksum, tag=TAG):
        return release.install_archive(archive, checksum, tag, self.sdk_home, self.bin_dir)

    def reject(self, archive, checksum, tag=TAG):
        with self.assertRaises((ValueError, OSError)):
            self.install(archive, checksum, tag)
        self.assertEqual((self.sdk_home / "current").resolve(), self.previous)
        self.assertFalse((self.sdk_home / "releases" / tag).exists())
        self.assertFalse(self.bin_dir.exists() and any(self.bin_dir.iterdir()))

    def test_valid_install_is_verified_and_idempotent(self):
        archive, checksum = write_archive(self.root)
        installed = self.install(archive, checksum)
        self.assertEqual((self.sdk_home / "current").resolve(), installed.resolve())
        self.assertEqual((self.bin_dir / "avm-local-stand").read_bytes(), b"stand fixture\n")
        self.assertEqual(self.install(archive, checksum), installed)

    def test_missing_outer_checksum_rejects_before_activation(self):
        archive, checksum = write_archive(self.root)
        checksum.unlink()
        self.reject(archive, checksum)

    def test_outer_tampering_rejects_before_activation(self):
        archive, checksum = write_archive(self.root)
        archive.write_bytes(archive.read_bytes() + b"changed")
        self.reject(archive, checksum)

    def test_inner_tampering_with_valid_outer_checksum_rejects(self):
        self.reject(*write_archive(self.root, corrupt_inner=True))

    def test_decompressed_archive_limit_applies_before_tar_metadata_parsing(self):
        archive, checksum = write_archive(self.root)
        # The compressed fixture fits; its tar headers and data exceed this cap.
        limit = archive.stat().st_size + 512
        with patch.object(release, "MAX_BYTES", limit), patch.object(release.tarfile, "open") as parser:
            self.reject(archive, checksum)
            parser.assert_not_called()

    def test_checksum_filename_cannot_select_another_file(self):
        archive, checksum = write_archive(self.root)
        checksum.write_text(f"{digest(archive.read_bytes())}  ../escape\n")
        self.reject(archive, checksum)

    def test_manifest_tag_must_bind_both_full_commits(self):
        files = payload()
        manifest = json.loads(files["BUILD-MANIFEST.json"])
        manifest["ackinacki_commit"] = "3" * 40
        files["BUILD-MANIFEST.json"] = json_bytes(manifest)
        self.reject(*write_archive(self.root, files))

    def test_receipt_must_bind_actual_binary_despite_valid_inner_checksums(self):
        files = payload()
        files["bin/avm-local-stand"] = b"unrelated but checksummed binary"
        self.reject(*write_archive(self.root, files))

    def test_toolchain_notices_cannot_be_omitted_or_bound_to_another_compiler(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch):
                files = payload()
                notices = json.loads(files["THIRD-PARTY-NOTICES.json"])
                if mismatch:
                    notices["rust_toolchain"]["rustc_verbose"] = "different compiler"
                else:
                    del notices["rust_toolchain"]
                files["THIRD-PARTY-NOTICES.json"] = json_bytes(notices)
                manifest = json.loads(files["BUILD-MANIFEST.json"])
                manifest["third_party_notices_sha256"] = digest(files["THIRD-PARTY-NOTICES.json"])
                files["BUILD-MANIFEST.json"] = json_bytes(manifest)
                self.reject(*write_archive(self.root, files))

    def test_receipt_source_and_lock_are_bound_independently(self):
        for field, value in [("source_commit", "3" * 40), ("lock_sha256", "0" * 64)]:
            with self.subTest(field=field):
                files = payload()
                receipt = json.loads(files["provenance/build.json"])
                receipt[field] = value
                files["provenance/build.json"] = json_bytes(receipt)
                manifest = json.loads(files["BUILD-MANIFEST.json"])
                manifest["build_receipt_sha256"] = digest(files["provenance/build.json"])
                files["BUILD-MANIFEST.json"] = json_bytes(manifest)
                self.reject(*write_archive(self.root, files))

    def test_receipt_cannot_reuse_another_cargo_artifact_log(self):
        files = payload()
        files["provenance/cargo-artifacts.jsonl"] = b"{}\n"
        self.reject(*write_archive(self.root, files))

    def test_empty_artifact_log_is_not_build_provenance_even_when_rehashed(self):
        files = payload()
        files["provenance/cargo-artifacts.jsonl"] = b""
        receipt = json.loads(files["provenance/build.json"])
        receipt["compiler_artifacts_sha256"] = digest(b"")
        files["provenance/build.json"] = json_bytes(receipt)
        manifest = json.loads(files["BUILD-MANIFEST.json"])
        manifest["build_receipt_sha256"] = digest(files["provenance/build.json"])
        files["BUILD-MANIFEST.json"] = json_bytes(manifest)
        self.reject(*write_archive(self.root, files))

    def test_unsafe_paths_types_and_duplicates_reject_before_extraction(self):
        cases = [
            ("../escape", tarfile.REGTYPE, ""),
            ("/absolute", tarfile.REGTYPE, ""),
            (f"{ROOT}/../escape", tarfile.REGTYPE, ""),
            (f"{ROOT}/bin/link", tarfile.SYMTYPE, "../../../escape"),
            (f"{ROOT}/bin/link", tarfile.LNKTYPE, f"{ROOT}/LICENSE.md"),
            (f"{ROOT}/device", tarfile.CHRTYPE, ""),
            (f"{ROOT}/LICENSE.md", tarfile.REGTYPE, ""),
            (f"{ROOT}/unlisted", tarfile.REGTYPE, ""),
        ]
        for name, kind, link in cases:
            with self.subTest(name=name, kind=kind):
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = link
                self.reject(*write_archive(self.root, extra=[(member, b"")]))
                self.assertFalse((self.root / "escape").exists())

    def test_tag_cannot_escape_release_directory(self):
        archive, checksum = write_archive(self.root)
        self.reject(archive, checksum, "../../escape")

    def test_existing_release_is_immutable(self):
        archive, checksum = write_archive(self.root)
        installed = self.install(archive, checksum)
        before = (installed / "bin/avm-local-stand").read_bytes()
        files = payload()
        files["LICENSE.md"] += b"changed"
        archive, checksum = write_archive(self.root, files)
        with self.assertRaises(ValueError):
            self.install(archive, checksum)
        self.assertEqual((installed / "bin/avm-local-stand").read_bytes(), before)

    def test_existing_user_command_is_preserved(self):
        self.bin_dir.mkdir()
        command = self.bin_dir / "avm-local-stand"
        command.write_bytes(b"user-owned")
        with self.assertRaises(ValueError):
            self.install(*write_archive(self.root))
        self.assertEqual(command.read_bytes(), b"user-owned")
        self.assertEqual((self.sdk_home / "current").resolve(), self.previous)


if __name__ == "__main__":
    unittest.main()

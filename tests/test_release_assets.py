import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "release_assets", Path(__file__).resolve().parents[1] / "scripts/release-assets.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseAssetsTests(unittest.TestCase):
    def test_publication_keeps_the_signed_copr_rpm(self):
        with tempfile.TemporaryDirectory() as root:
            verified = Path(root) / "verified"
            output = Path(root) / "output"
            verified.mkdir()
            output.mkdir()
            binary = "t3code-0.1.10-1.fc45.x86_64.rpm"
            (verified / binary).write_bytes(b"local RPM")
            (output / binary).write_bytes(b"signed COPR RPM")
            (verified / "t3code-0.1.10-1.fc45.src.rpm").write_bytes(b"source RPM")
            (verified / "build-provenance.json").write_text("{}")
            (verified / release.FFMPEG_SOURCE).write_bytes(b"FFmpeg sources")
            (output / "copr-build.json").write_text('{"id": 123}')
            plan = {"version": "0.1.10", "revision": 1, "commit": "a" * 40}
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "2"}):
                with patch.object(release, "save_asset"), patch.object(release, "gh") as github:
                    release.publish("owner/t3code-rpm", {"id": 2}, plan, verified, output)
            self.assertEqual((output / binary).read_bytes(), b"signed COPR RPM")
            self.assertIn(release.digest(output / binary), (output / "SHA256SUMS").read_text())
            self.assertFalse(github.call_args.kwargs["data"]["draft"])

    def test_old_draft_without_ffmpeg_provenance_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / "toolchain.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "predates"):
                release.verify_ffmpeg_source(directory)

    def test_uploaded_asset_cannot_be_overwritten_with_different_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "SHA256SUMS"
            path.write_text("changed")
            existing = {"name": path.name, "state": "uploaded", "digest": "sha256:" + "a" * 64}
            with patch.object(release, "gh") as github:
                with self.assertRaisesRegex(ValueError, "Existing release asset differs"):
                    release.save_asset("owner/t3code-rpm", {"assets": [existing]}, path)
            github.assert_not_called()


if __name__ == "__main__":
    unittest.main()

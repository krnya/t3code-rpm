from hashlib import sha256
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile, ZipInfo

spec = importlib.util.spec_from_file_location(
    "prepare_electron", Path(__file__).resolve().parents[1] / "scripts/prepare_electron.py"
)
electron = importlib.util.module_from_spec(spec)
spec.loader.exec_module(electron)


class ElectronPreparationTests(unittest.TestCase):
    def test_replacement_preserves_other_files_and_permissions(self):
        linux_x64_elf = b"\x7fELF\x02\x01" + bytes(12) + b"\x3e\x00"
        with tempfile.TemporaryDirectory() as directory:
            source, alternate, result = (Path(directory) / name for name in ("in", "ffmpeg", "out"))
            executable = ZipInfo("electron")
            executable.create_system = 3
            executable.external_attr = 0o100755 << 16
            with ZipFile(source, "w") as runtime:
                runtime.writestr(executable, b"runtime")
                runtime.writestr("libffmpeg.so", b"original")
            with ZipFile(alternate, "w") as codecs:
                codecs.writestr("libffmpeg.so", linux_x64_elf)
            self.assertEqual(electron.replace_library(source, alternate, result),
                             sha256(linux_x64_elf).hexdigest())
            with ZipFile(result) as runtime:
                self.assertEqual(runtime.read("electron"), b"runtime")
                self.assertEqual(runtime.getinfo("electron").external_attr >> 16, 0o100755)
                self.assertEqual(runtime.read("libffmpeg.so"), linux_x64_elf)


if __name__ == "__main__":
    unittest.main()

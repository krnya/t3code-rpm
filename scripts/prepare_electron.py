"""Prepare Electron's upstream runtime without the proprietary codec build."""
from copy import copy
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from zipfile import ZipFile


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, path, digest=None):
    if path.is_file() and (digest is None or sha256(path) == digest):
        return
    partial = path.with_suffix(path.suffix + ".partial")
    subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "4",
         "--retry-all-errors", "--proto", "=https", "--proto-redir", "=https",
         "--output", str(partial), url], check=True,
    )
    if digest is not None and sha256(partial) != digest:
        raise ValueError(f"Official {path.name} checksum did not match")
    partial.replace(path)


def replace_library(runtime, alternate, output):
    with ZipFile(runtime) as original, ZipFile(alternate) as codecs, ZipFile(output, "w") as cleaned:
        member = "libffmpeg.so"
        if original.namelist().count(member) != 1 or codecs.namelist() != [member]:
            raise ValueError("Unexpected layout of the Electron or FFmpeg archive")
        with codecs.open(member) as stream:
            header = stream.read(20)
            if not header.startswith(b"\x7fELF\x02\x01") or header[18:20] != b"\x3e\x00":
                raise ValueError("The replacement is not a Linux x86_64 ELF library")
        for item in original.infolist():
            source = codecs.open(member) if item.filename == member else original.open(item)
            with source, cleaned.open(copy(item), "w") as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
        with codecs.open(member) as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()


def tar_member_text(archive, suffix):
    matches = [entry for entry in archive.getmembers() if entry.name.partition("/")[2] == suffix and entry.isfile()]
    if len(matches) != 1:
        raise ValueError(f"Expected one {suffix} in the Electron source")
    with archive.extractfile(matches[0]) as stream:
        return stream.read().decode()


def prepare_electron(electron, sources, cache, fetch):
    cache.mkdir(parents=True, exist_ok=True)
    base = f"https://github.com/electron/electron/releases/download/v{electron}"
    runtime_name = f"electron-v{electron}-linux-x64.zip"
    ffmpeg_name = f"ffmpeg-v{electron}-linux-x64.zip"
    manifest = fetch(f"{base}/SHASUMS256.txt")

    def official_release(name):
        matches = re.findall(rf"^([0-9a-f]{{64}}) [ *]{re.escape(name)}$", manifest, re.M)
        if len(matches) != 1:
            raise ValueError(f"No unique official checksum for {name}")
        path = cache / name
        download(f"{base}/{name}", path, matches[0])
        return path, matches[0]

    runtime, runtime_sha = official_release(runtime_name)
    alternate, alternate_sha = official_release(ffmpeg_name)
    electron_ref = subprocess.check_output(
        ["git", "ls-remote", "https://github.com/electron/electron.git",
         f"refs/tags/v{electron}", f"refs/tags/v{electron}^{{}}"], text=True,
    ).splitlines()
    commits = [line.split()[0] for line in electron_ref]
    if not commits or not re.fullmatch(r"[0-9a-f]{40}", commits[-1]):
        raise ValueError("Could not resolve Electron's source tag")
    electron_commit = commits[-1]
    electron_source = cache / f"electron-{electron_commit}.tar.gz"
    download(f"https://codeload.github.com/electron/electron/tar.gz/{electron_commit}", electron_source)
    with tarfile.open(electron_source, "r:gz") as archive:
        electron_deps = tar_member_text(archive, "DEPS")
        args = tar_member_text(archive, "build/args/ffmpeg.gn")
    if not re.search(r"^proprietary_codecs\s*=\s*false\s*$", args, re.M) or not re.search(
        r'^ffmpeg_branding\s*=\s*"Chromium"\s*$', args, re.M
    ):
        raise ValueError("Electron's alternate FFmpeg build flags changed")
    chromium = re.search(r"'chromium_version':\s*'([0-9.]+)'", electron_deps)
    if chromium is None:
        raise ValueError("Could not resolve Chromium's version from Electron DEPS")
    chromium = chromium[1]
    chromium_deps = fetch(f"https://raw.githubusercontent.com/chromium/chromium/{chromium}/DEPS")
    ffmpeg_commit = re.search(r"'ffmpeg_revision':\s*'([0-9a-f]{40})'", chromium_deps)
    if ffmpeg_commit is None:
        raise ValueError("Could not resolve FFmpeg's revision from Chromium DEPS")
    ffmpeg_commit = ffmpeg_commit[1]
    ffmpeg_source = cache / f"chromium-ffmpeg-{ffmpeg_commit}.tar.gz"
    download(
        "https://chromium.googlesource.com/chromium/third_party/ffmpeg/"
        f"+archive/{ffmpeg_commit}.tar.gz", ffmpeg_source,
    )
    with tarfile.open(ffmpeg_source, "r:gz") as archive:
        if not archive.getmember("COPYING.LGPLv2.1").isfile():
            raise ValueError("Chromium FFmpeg source is missing its LGPL notice")

    cleaned = sources / "electron-linux-x64.zip"
    lib_sha = replace_library(runtime, alternate, cleaned)
    notice = sources / "FFmpeg-NOTICE"
    notice.write_text(
        "T3 Code uses the dynamically linked FFmpeg library from Electron's\n"
        "Chromium-branded Linux x64 build. This library is licensed under\n"
        "LGPL-2.1-or-later. See LICENSES.chromium.html for the license text.\n\n"
        f"Electron v{electron} source revision: {electron_commit}\n"
        f"Chromium version: {chromium}\n"
        f"Chromium FFmpeg source revision: {ffmpeg_commit}\n\n"
        "The matching ffmpeg-corresponding-source.tar.gz is provided with the\n"
        "release and in the SRPM. It contains the FFmpeg source, the Electron\n"
        "sources with FFmpeg patches and build flags, and Chromium's DEPS.\n"
    )
    chromium_deps_file = cache / f"chromium-{chromium}-DEPS"
    chromium_deps_file.write_text(chromium_deps)
    corresponding = sources / "ffmpeg-corresponding-source.tar.gz"
    with tarfile.open(corresponding, "w:gz", compresslevel=6) as archive:
        for path in (ffmpeg_source, electron_source, chromium_deps_file, notice):
            archive.add(path, arcname=path.name)
    return {
        "copr_profile": 1,
        "electron_version": electron,
        "electron_sha256": runtime_sha,
        "electron_rpm_sha256": sha256(cleaned),
        "electron_source_commit": electron_commit,
        "chromium_version": chromium,
        "ffmpeg_release_sha256": alternate_sha,
        "ffmpeg_library_sha256": lib_sha,
        "ffmpeg_source_commit": ffmpeg_commit,
        "ffmpeg_source_sha256": sha256(corresponding),
    }

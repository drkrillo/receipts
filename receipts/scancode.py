"""Running ScanCode, and telling the user how to install it when it is missing."""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time


logger = logging.getLogger(__name__)

class ScanCodeUnavailable(RuntimeError):
    """ScanCode is required but missing or unusable."""


# --------------------------------------------------------------------------- #
# Locating ScanCode (and the libmagic it hard-requires)
# --------------------------------------------------------------------------- #

def _venv_hint() -> tuple[str, str]:
    """(activate command, pip path) for this platform."""
    if sys.platform == "win32":
        return (r".venv\Scripts\activate", r".venv\Scripts\pip")
    return ("source .venv/bin/activate", ".venv/bin/pip")


def scancode_install_hint() -> str:
    """Copy-pasteable install instructions for THIS machine."""
    activate, _ = _venv_hint()
    py = "py -m venv .venv" if sys.platform == "win32" else "python3 -m venv .venv"
    lines = [
        "ScanCode Toolkit is required for license detection, but was not found.",
        "",
        "Install it:",
        f"    {py}",
        f"    {activate}",
        '    pip install "scancode-toolkit>=32.0,<33.0"',
    ]
    if sys.platform == "darwin":
        lines += ["", "On macOS also install libmagic (ScanCode's bundled copy has no",
                  "Apple Silicon build):", "    brew install libmagic"]
    elif sys.platform.startswith("linux"):
        lines += ["", "On Debian/Ubuntu you may first need:",
                  "    sudo apt install python3-dev bzip2 xz-utils zlib1g "
                  "libxml2-dev libxslt1-dev libpopt0"]
    lines += ["",
              "If ScanCode is installed elsewhere, point receipts at it:",
              ("    set RECEIPTS_SCANCODE=C:\\path\\to\\scancode.exe"
               if sys.platform == "win32"
               else "    export RECEIPTS_SCANCODE=/path/to/scancode")]
    return "\n".join(lines)


def _libmagic_install_hint() -> str:
    """OS-specific instructions for the libmagic ScanCode needs."""
    if sys.platform == "darwin":
        cmds = ["    brew install libmagic"]
        why = ("ScanCode's bundled libmagic has no Apple Silicon build, so it must "
               "come from the system.")
    elif sys.platform.startswith("linux"):
        cmds = ["    sudo apt install libmagic1        # Debian/Ubuntu",
                "    sudo dnf install file-libs        # Fedora/RHEL",
                "    sudo pacman -S file               # Arch"]
        why = "Install the one matching your distribution."
    else:
        cmds = ["    pip install --force-reinstall typecode-libmagic"]
        why = "ScanCode normally bundles libmagic on this platform; reinstall it."
    return "\n".join([
        "ScanCode is installed but cannot run: libmagic is missing.",
        "", "Install it:", *cmds, "", why, "",
        "Already installed somewhere unusual? Point ScanCode at it:",
        "    export TYPECODE_LIBMAGIC_PATH=/path/to/libmagic.so   # or .dylib",
        "    export TYPECODE_LIBMAGIC_DB_PATH=/path/to/magic.mgc",
    ])


def _diagnose(stderr: str) -> str:
    """Turn a ScanCode crash into instructions the user can act on."""
    if "NoMagicLibError" in stderr or "libmagic" in stderr.lower():
        return _libmagic_install_hint()
    tail = "\n  ".join((stderr or "").strip().splitlines()[-4:])
    return ("ScanCode failed to run.\n\n"
            f"Its error was:\n  {tail}\n\n" + scancode_install_hint())


# ScanCode aborts unless it can load libmagic; its bundled plugin ships no macOS
# arm64 binary, so point it at a system copy when the caller hasn't.
_LIBMAGIC_CANDIDATES = (
    ("/opt/homebrew/lib/libmagic.dylib", "/opt/homebrew/share/misc/magic.mgc"),
    ("/usr/local/lib/libmagic.dylib", "/usr/local/share/misc/magic.mgc"),
    ("/usr/lib/x86_64-linux-gnu/libmagic.so.1", "/usr/share/misc/magic.mgc"),
    ("/usr/lib/aarch64-linux-gnu/libmagic.so.1", "/usr/share/misc/magic.mgc"),
)


@functools.lru_cache(maxsize=1)
def scancode_path() -> str | None:
    env = os.environ.get("RECEIPTS_SCANCODE")
    if env:
        return env if os.path.isfile(env) and os.access(env, os.X_OK) else None
    return shutil.which("scancode")


@functools.lru_cache(maxsize=1)
def _libmagic_env() -> dict[str, str]:
    env = dict(os.environ)
    if env.get("TYPECODE_LIBMAGIC_PATH"):
        return env
    import glob
    for lib, db in _LIBMAGIC_CANDIDATES:
        if not os.path.exists(lib):
            continue
        if not os.path.exists(db):
            found = (glob.glob("/opt/homebrew/Cellar/libmagic/*/share/misc/magic.mgc")
                     or glob.glob("/usr/local/Cellar/libmagic/*/share/misc/magic.mgc"))
            db = found[0] if found else ""
        if db:
            env["TYPECODE_LIBMAGIC_PATH"] = lib
            env["TYPECODE_LIBMAGIC_DB_PATH"] = db
            break
    return env


@functools.lru_cache(maxsize=1)
def _version_stderr() -> str:
    """ScanCode's own error output from `--version`, used to diagnose failures."""
    exe = scancode_path()
    if not exe:
        return ""
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True,
                              timeout=180, env=_libmagic_env())
        return proc.stderr or proc.stdout or ""
    except Exception as exc:
        return str(exc)


@functools.lru_cache(maxsize=1)
def scancode_version() -> str:
    exe = scancode_path()
    if not exe:
        return ""
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True,
                              timeout=180, env=_libmagic_env())
        for line in proc.stdout.splitlines():
            if "ScanCode version" in line:
                return line.strip()
        # No version banner => scancode is present but broken (typically
        # NoMagicLibError). Report unusable rather than inventing "unknown":
        # a half-working detector silently degrades every finding to
        # NOASSERTION, which reads as a result instead of a failure.
        return ""
    except Exception:
        return ""


def require_scancode() -> str:
    """Return the scancode path or raise with install instructions."""
    exe = scancode_path()
    if not exe:
        raise ScanCodeUnavailable(scancode_install_hint())
    if not scancode_version():
        raise ScanCodeUnavailable(
            f"ScanCode was found at {exe}\nbut failed to run.\n\n"
            + _diagnose(_version_stderr()))
    return exe


def detector_name() -> str:
    return "scancode"


# --------------------------------------------------------------------------- #
# Running ScanCode
# --------------------------------------------------------------------------- #

def _run_scancode(target: str, out_json: str) -> dict:
    """Run ScanCode and return its JSON, or RAISE."""
    exe = require_scancode()
    cmd = [exe, "--license", "--copyright", "--quiet", "--json-pp", out_json, target]
    logger.debug("scancode scan: %s", target)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                              env=_libmagic_env())
    except Exception as exc:
        raise ScanCodeUnavailable(
            f"failed to execute ScanCode: {exc}\n\n" + scancode_install_hint())
    if proc.returncode != 0 or not os.path.isfile(out_json):
        raise ScanCodeUnavailable(_diagnose(proc.stderr or proc.stdout or ""))
    try:
        with open(out_json, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise ScanCodeUnavailable(f"ScanCode produced unreadable JSON: {exc}")
    logger.debug("scancode scan done in %.1fs", time.monotonic() - t0)
    return data


def _scan_text(text: str) -> dict | None:  # None only when ScanCode saw no file
    """Scan a single license text; returns ScanCode's file entry."""
    with tempfile.TemporaryDirectory(prefix="receipts-sc-") as td:
        src = os.path.join(td, "LICENSE")
        with open(src, "w", encoding="utf-8") as f:
            f.write(text)
        data = _run_scancode(src, os.path.join(td, "out.json"))
    files = [f for f in data.get("files", []) if f.get("type", "file") == "file"]
    return files[0] if files else None


def scan_files(files: dict[str, str]) -> dict[str, dict]:
    """Scan many files in ONE ScanCode invocation."""
    if not files:
        return {}
    logger.info("scanning %d file header(s) with ScanCode (one batch)", len(files))
    with tempfile.TemporaryDirectory(prefix="receipts-scb-") as td:
        root = os.path.join(td, "tree")
        mapping: dict[str, str] = {}
        for i, (label, content) in enumerate(files.items()):
            # Flatten to avoid deep paths, keep the extension (ScanCode uses it).
            ext = os.path.splitext(label)[1]
            name = f"f{i:05d}{ext}"
            dest = os.path.join(root, name)
            os.makedirs(root, exist_ok=True)
            with open(dest, "w", encoding="utf-8", errors="replace") as f:
                f.write(content)
            mapping[name] = label
        data = _run_scancode(root, os.path.join(td, "out.json"))
    out: dict[str, dict] = {}
    for entry in data.get("files", []):
        if entry.get("type") != "file":
            continue
        label = mapping.get(os.path.basename(entry.get("path", "")))
        if label:
            out[label] = entry
    return out



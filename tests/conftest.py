"""Real git repositories, built commit by commit, for the functional tests.

Every temporal claim this tool makes is a statement about a commit graph, and
the mocked tests could not see the difference between asking "is this commit an
ancestor of the licence change" and "does it descend from it" — the two agree on
a straight line and diverge on every branch. These fixtures build the branch.

ScanCode is 4-8 seconds per detection, so the tests that are about git stub it
with `fake_detector`. The tests that are about detection run the real thing and
are marked `slow`.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Repo:
    """A real git repository you can write a history into."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        os.makedirs(self.path, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        self._clock = 0

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(["git", "-C", self.path, *args],
                              capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}\n{proc.stderr}")
        return proc.stdout.strip()

    def commit(self, message: str, files: dict[str, str | None] | None = None,
               date: str | None = None) -> str:
        """Write *files* (None deletes) and commit. Returns the commit sha."""
        for path, body in (files or {}).items():
            full = os.path.join(self.path, path)
            if body is None:
                if os.path.exists(full):
                    os.remove(full)
                continue
            os.makedirs(os.path.dirname(full) or self.path, exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(body)
        self.git("add", "-A")
        # Fixed, ordered timestamps: a test that depends on wall-clock ordering
        # passes or fails by how fast the machine is.
        self._clock += 1
        stamp = date or f"2020-{1 + self._clock // 28:02d}-{1 + self._clock % 28:02d}T12:00:00+00:00"
        env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        proc = subprocess.run(
            ["git", "-C", self.path, "commit", "-q", "--allow-empty", "-m", message],
            capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def branch(self, name: str, at: str | None = None) -> None:
        self.git("checkout", "-q", "-b", name, *( [at] if at else [] ))

    def checkout(self, ref: str) -> None:
        self.git("checkout", "-q", ref)

    def merge(self, ref: str, message: str = "merge") -> str:
        env = dict(os.environ,
                   GIT_AUTHOR_DATE="2020-06-01T12:00:00+00:00",
                   GIT_COMMITTER_DATE="2020-06-01T12:00:00+00:00")
        proc = subprocess.run(
            ["git", "-C", self.path, "merge", "-q", "--no-ff",
             "--allow-unrelated-histories", "-m", message, ref],
            capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)
        return self.head()

    def orphan(self, name: str, message: str, files: dict[str, str]) -> str:
        """A root commit with no ancestor in common with anything else."""
        self.git("checkout", "-q", "--orphan", name)
        self.git("rm", "-rq", "--cached", ".", check=False)
        for f in os.listdir(self.path):
            if f != ".git":
                full = os.path.join(self.path, f)
                if os.path.isfile(full):
                    os.remove(full)
        return self.commit(message, files)


@pytest.fixture
def repo(tmp_path):
    """An empty repository. Call `.commit(...)` to give it a history."""
    return Repo(tmp_path / "origin")


@pytest.fixture
def repo_factory(tmp_path):
    """Make as many repositories as a test needs."""
    made: list[Repo] = []

    def _make(name: str) -> Repo:
        r = Repo(tmp_path / name)
        made.append(r)
        return r
    return _make


def clone(src: Repo, dest, branch: str = "main", at: str | None = None) -> Repo:
    """A real fork: one branch, taken at one point.

    `--single-branch` matters. A plain clone fetches every branch, so a fork
    "taken from the release branch" would still hold the mainline's later
    commits and the test would be asserting nothing.
    """
    subprocess.run(["git", "clone", "-q", "--single-branch", "--branch", branch,
                    src.path, str(dest)],
                   capture_output=True, text=True, check=True)
    r = Repo.__new__(Repo)
    r.path, r._clock = str(dest), 200
    r.git("config", "user.email", "fork@example.com")
    r.git("config", "user.name", "Fork")
    r.git("config", "commit.gpgsign", "false")
    if at:
        # Anything after `at` becomes unreachable from every ref, which is what
        # "the fork left here" means to `git rev-list --all`.
        r.git("reset", "--hard", "-q", at)
        r.git("remote", "remove", "origin")
    return r


# --------------------------------------------------------------------------- #
# A detector that answers in microseconds.
# --------------------------------------------------------------------------- #
MIT = "MIT License\n\nCopyright (c) 2020 Origin Authors\nPermission is granted."
BSD = "BSD 3-Clause\n\nCopyright (c) 2020 Origin Authors\nRedistribution ok."
APACHE = "Apache License 2.0\n\nCopyright 2020 Origin Authors\n"
PROPRIETARY = "All rights reserved. Commercial use requires a licence."

_MARKS = ((("mit license",), "MIT"),
          (("bsd 3-clause",), "BSD-3-Clause"),
          (("apache license",), "Apache-2.0"),
          (("all rights reserved",), "proprietary-license"))


@pytest.fixture
def fake_detector(monkeypatch):
    """Substitute ScanCode with a keyword match, and record what it was asked."""
    from receipts import license as rl
    from receipts.models import LicenseState

    seen: list[str] = []

    def _detect(text: str) -> LicenseState:
        seen.append(text)
        if not text or not text.strip():
            return LicenseState(spdx_id="NONE", confidence=1.0, detector="fake")
        low = text.lower()
        for needles, spdx in _MARKS:
            if any(n in low for n in needles):
                return LicenseState(spdx_id=spdx, governing=spdx, confidence=1.0,
                                    full_expression=spdx, detector="fake")
        return LicenseState(spdx_id="NOASSERTION", confidence=0.0, detector="fake")

    monkeypatch.setattr(rl, "detect_text", _detect)
    return seen

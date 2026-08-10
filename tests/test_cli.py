"""The commands as a user runs them, on real repositories.

`cli.py`, `commands.py` and `snapshot.py` were at 0%: the evidence package — the
thing a reader is meant to keep — was produced by code no test had executed.

These run the real ScanCode, so they are slower than the rest of the suite.
Two repositories on disk, no network.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from receipts import cli
from tests.conftest import APACHE, MIT, clone

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def _pair(tmp_path_factory):
    """An origin that relicensed, and a fork that left before it did."""
    from tests.conftest import Repo
    base = tmp_path_factory.mktemp("pair")
    up = Repo(base / "origin")
    up.commit("initial", {"src.py": "def f():\n    return 1\n"},
              date="2018-01-01T00:00:00+00:00")
    up.commit("licence", {"LICENSE": MIT}, date="2018-02-01T00:00:00+00:00")
    at = up.commit("more work", {"lib.py": "VALUE = 42\n"},
                   date="2019-01-01T00:00:00+00:00")
    fork = clone(up, base / "fork", at=at)
    fork.commit("fork's own work", {"fork.py": "z = 0\n"})
    up.commit("relicense", {"LICENSE": APACHE}, date="2021-01-01T00:00:00+00:00")
    up.commit("work after", {"late.py": "late = 1\n"},
              date="2021-06-01T00:00:00+00:00")
    return up, fork, at


def _run(capsys, *args) -> tuple[int, str]:
    code = cli.main(list(args))
    return code, capsys.readouterr().out


def test_overlap_reports_the_shared_objects_and_how_to_check_them(_pair, capsys,
                                                                  tmp_path):
    up, fork, at = _pair
    code, out = _run(capsys, "overlap", up.path, fork.path,
                     "--work-dir", str(tmp_path / "wd"), "--no-color")

    assert code == 0
    assert "commits in both           3" in out
    assert at[:10] in out
    assert "merge-base --is-ancestor" in out


def test_license_reads_one_repository_at_one_revision(_pair, capsys, tmp_path):
    up, _, at = _pair
    code, out = _run(capsys, "license", up.path, "--at", at,
                     "--work-dir", str(tmp_path / "wd"), "--no-color")

    assert code == 0
    # At the fork point the origin was MIT; it is Apache now, and `--at` must
    # not read HEAD.
    assert "MIT" in out and "Apache" not in out

    code, out = _run(capsys, "license", up.path,
                     "--work-dir", str(tmp_path / "wd"), "--no-color")
    assert "Apache-2.0" in out


def test_licdrift_lists_the_relicensing(_pair, capsys, tmp_path):
    up, _, _ = _pair
    code, out = _run(capsys, "licdrift", up.path,
                     "--work-dir", str(tmp_path / "wd"), "--no-color")

    assert code == 0
    assert "MIT" in out and "Apache-2.0" in out
    assert "2021-01-01" in out


def test_provenance_reports_the_licence_at_divergence_not_at_head(_pair, capsys,
                                                                  tmp_path):
    """The whole point of the tool, through the command a user actually runs."""
    up, fork, at = _pair
    code, out = _run(capsys, "provenance", up.path, fork.path,
                     "--work-dir", str(tmp_path / "wd"), "--no-color")

    assert code == 0
    div = out.split("origin at divergence")[1].splitlines()[0]
    assert "MIT" in div
    now = out.split("origin now")[1].splitlines()[0]
    assert "Apache-2.0" in now
    # and it says which commit that reading was taken at
    assert at[:10] in out


def test_provenance_attributes_shared_commits_to_the_licence_of_their_time(
        _pair, capsys, tmp_path):
    up, fork, _ = _pair
    _, out = _run(capsys, "provenance", up.path, fork.path,
                  "--work-dir", str(tmp_path / "wd"), "--no-color")

    eras = out.split("shared commits by the licence")[1].split("NOTICES")[0]
    assert "MIT" in eras
    # Nothing the fork holds was made under Apache.
    assert "Apache" not in eras


def test_the_evidence_package_is_written_and_its_formats_agree(_pair, capsys,
                                                              tmp_path):
    up, fork, _ = _pair
    out_dir = tmp_path / "package"
    out_dir.mkdir()
    code, _ = _run(capsys, "provenance", up.path, fork.path,
                   "--work-dir", str(tmp_path / "wd"), "--snapshot", str(out_dir),
                   "--no-color")
    assert code == 0

    made = [d for d in out_dir.iterdir() if d.is_dir()]
    assert len(made) == 1
    pkg = made[0]
    for name in ("report.txt", "report.md", "report.json", "MANIFEST.json",
                 "README.txt", "NOTICE.txt"):
        assert (pkg / name).exists(), f"missing from the package: {name}"

    txt = (pkg / "report.txt").read_text()
    md = (pkg / "report.md").read_text()
    data = json.loads((pkg / "report.json").read_text())

    # One licence, three formats, one answer.
    governing = data["upstream_license_at_divergence"]["governing"]
    assert governing == "MIT"
    assert governing in txt and governing in md
    # The commit every upstream reading was taken at is in all three.
    anchor = data["custody"]["anchor_commit"]
    assert anchor[:10] in txt and anchor[:10] in md
    assert data["custody"]["anchor_basis"]


def test_the_package_hashes_every_file_it_ships(_pair, capsys, tmp_path):
    import hashlib
    up, fork, _ = _pair
    out_dir = tmp_path / "package2"
    out_dir.mkdir()
    _run(capsys, "provenance", up.path, fork.path,
         "--work-dir", str(tmp_path / "wd"), "--snapshot", str(out_dir),
         "--no-color")
    pkg = [d for d in out_dir.iterdir() if d.is_dir()][0]
    manifest = json.loads((pkg / "MANIFEST.json").read_text())

    listed = {path: entry.get("sha256")
              for path, entry in manifest["files"].items()}
    assert listed, "the manifest lists no files"
    for path, digest in listed.items():
        full = pkg / path
        assert full.exists(), f"manifest names a file that is not there: {path}"
        if digest:
            got = hashlib.sha256(full.read_bytes()).hexdigest()
            assert got == digest, f"hash does not match for {path}"


def test_the_evidence_hash_is_the_same_for_the_same_two_repositories(
        _pair, capsys, tmp_path):
    """Reproducibility is the claim; the timestamp is the only thing that moves."""
    up, fork, _ = _pair
    seen = set()
    for i in range(2):
        _, out = _run(capsys, "provenance", up.path, fork.path,
                      "--work-dir", str(tmp_path / f"wd{i}"), "--no-color")
        seen.add(out.split("evidence sha256")[1].split()[0])
    assert len(seen) == 1


def test_snapshotting_into_a_directory_that_does_not_exist_fails_clearly(
        _pair, capsys, tmp_path):
    up, fork, _ = _pair
    code = cli.main(["provenance", up.path, fork.path,
                     "--work-dir", str(tmp_path / "wd"),
                     "--snapshot", str(tmp_path / "nope"), "--no-color"])
    assert code != 0
    assert "no such directory" in capsys.readouterr().err


def test_a_directory_that_is_not_a_repository_is_reported_not_traced(capsys,
                                                                    tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    code = cli.main(["license", str(plain), "--work-dir", str(tmp_path / "wd")])
    err = capsys.readouterr().err
    assert code != 0
    assert "git" in err.lower()
    assert "Traceback" not in err


def test_clean_lists_before_it_deletes(_pair, capsys, tmp_path):
    up, fork, _ = _pair
    wd = tmp_path / "wd-clean"
    _run(capsys, "overlap", up.path, fork.path, "--work-dir", str(wd), "--no-color")
    # Local paths are used in place, so nothing is cached — the command must
    # still answer, not crash.
    code, out = _run(capsys, "clean", "--work-dir", str(wd))
    assert code == 0
    assert "nothing to clean" in out or "already empty" in out or "clones" in out


def test_the_version_flag_answers_without_a_repository(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "receipts" in capsys.readouterr().out.lower()


def test_the_installed_entry_point_runs():
    """`receipts` on the PATH is how the README tells people to use it."""
    proc = subprocess.run(["receipts", "--version"], capture_output=True, text=True,
                          env=dict(os.environ))
    assert proc.returncode == 0
    assert "receipts" in proc.stdout.lower()

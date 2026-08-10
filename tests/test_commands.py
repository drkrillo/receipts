"""The remaining commands, and the narrative `timeline` builds.

`commands.py` is the layer between the parser and the analysis, and the place
where a command can quietly do something outward-facing. The tests that matter
here are the ones that assert nothing was sent.
"""

from __future__ import annotations

import json

import pytest

from receipts import cli
from receipts import preserve as pres
from receipts import timeline as tl
from receipts import trace as tr
from tests.conftest import APACHE, MIT


def _out(capsys, *args) -> tuple[int, str, str]:
    code = cli.main(list(args))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# --------------------------------------------------------------------------- #
# timeline — one repository's history as events
# --------------------------------------------------------------------------- #
def test_the_history_opens_with_the_repository_being_created(repo, fake_detector):
    repo.commit("first", {"a.py": "a = 1\n"}, date="2015-04-05T00:00:00+00:00")
    repo.commit("licence", {"LICENSE": MIT}, date="2016-01-01T00:00:00+00:00")

    hist = tl.history(repo.path, ref=repo.path, licence_only=True)
    kinds = [e.kind for e in hist.events]
    assert kinds[0] == "created"
    assert hist.events[0].date == "2015-04-05"
    assert "licence" in kinds


def test_every_event_carries_the_command_that_confirms_it(repo, fake_detector):
    repo.commit("first", {"a.py": "a = 1\n"})
    repo.commit("licence", {"LICENSE": MIT})
    repo.commit("relicense", {"LICENSE": APACHE})

    hist = tl.history(repo.path, ref=repo.path, licence_only=True)
    assert hist.events, "no events at all"
    for e in hist.events:
        assert e.verify.startswith("git "), f"{e.kind} has no verify command"


def test_several_roots_are_reported_as_a_rewritten_or_grafted_history(repo,
                                                                     fake_detector):
    repo.commit("ours", {"a.py": "a = 1\n"})
    repo.orphan("theirs", "their root", {"lib/b.py": "b = 1\n"})
    repo.checkout("main")
    repo.merge("theirs", "vendor their history")

    hist = tl.history(repo.path, ref=repo.path, licence_only=True)
    assert any(e.kind == "rewritten" for e in hist.events)


def test_a_repository_with_no_licence_file_says_so_rather_than_guessing(repo,
                                                                       fake_detector):
    repo.commit("code only", {"a.py": "a = 1\n"})

    hist = tl.history(repo.path, ref=repo.path, licence_only=True)
    assert not [e for e in hist.events if e.kind == "licence"]
    assert hist.to_dict()["events"]


def test_the_timeline_command_prints_and_can_write_a_package(repo, capsys, tmp_path,
                                                             fake_detector):
    repo.commit("first", {"a.py": "a = 1\n"})
    repo.commit("licence", {"LICENSE": MIT})
    out_dir = tmp_path / "pkg"
    out_dir.mkdir()

    code, out, _ = _out(capsys, "timeline", repo.path, "--licence-only",
                        "--work-dir", str(tmp_path / "wd"),
                        "--snapshot", str(out_dir), "--no-color")
    assert code == 0
    assert "repository created" in out
    pkg = [d for d in out_dir.iterdir() if d.is_dir()][0]
    assert (pkg / "timeline.json").exists()
    data = json.loads((pkg / "timeline.json").read_text())
    assert data["events"][0]["kind"] == "created"


def test_the_licdrift_package_carries_all_three_formats(repo, capsys, tmp_path,
                                                        fake_detector):
    repo.commit("licence", {"LICENSE": MIT})
    repo.commit("relicense", {"LICENSE": APACHE})
    out_dir = tmp_path / "pkg"
    out_dir.mkdir()

    code, _, _ = _out(capsys, "licdrift", repo.path,
                      "--work-dir", str(tmp_path / "wd"),
                      "--snapshot", str(out_dir), "--no-color")
    assert code == 0
    pkg = [d for d in out_dir.iterdir() if d.is_dir()][0]
    for name in ("licdrift.txt", "licdrift.md", "licdrift.json"):
        assert (pkg / name).exists()
    data = json.loads((pkg / "licdrift.json").read_text())
    assert [t["after"] for t in data["transitions"]] == ["MIT", "Apache-2.0"]


# --------------------------------------------------------------------------- #
# trace
# --------------------------------------------------------------------------- #
def test_trace_urls_prints_one_per_line_so_it_can_be_piped(repo, capsys, tmp_path,
                                                           monkeypatch):
    repo.commit("first", {"a.py": "a = 1\n"})
    monkeypatch.setattr(tr, "require_gh", lambda: None)
    monkeypatch.setattr(tr, "holders_of_history", lambda root: [
        tr.Holder(name="org/one", how="history"),
        tr.Holder(name="org/two", how="history")])
    monkeypatch.setattr(tr, "archive_status", lambda url, token="": "")

    code, out, _ = _out(capsys, "trace", repo.path, "--urls",
                        "--work-dir", str(tmp_path / "wd"))
    assert code == 0
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines == ["https://github.com/org/one", "https://github.com/org/two"]


def test_trace_without_gh_explains_instead_of_crashing(repo, capsys, tmp_path,
                                                       monkeypatch):
    repo.commit("first", {"a.py": "a = 1\n"})

    def _no_gh():
        raise tr.GitHubCLIUnavailable("gh is not installed: brew install gh")
    monkeypatch.setattr(tr, "require_gh", _no_gh)

    code, _, err = _out(capsys, "trace", repo.path,
                        "--work-dir", str(tmp_path / "wd"))
    assert code != 0
    assert "gh" in err
    assert "Traceback" not in err


# --------------------------------------------------------------------------- #
# preserve
# --------------------------------------------------------------------------- #
def test_preserve_only_reads(capsys, monkeypatch):
    """There is no way to make this command write to the archive.

    `--submit` was here. It asked Software Heritage to keep a public,
    permanent copy of a repository that is usually not the user's, and it
    fired by accident once. The archive already crawls the public forges, and
    anyone who wants a repository saved can ask for it under their own name.
    """
    monkeypatch.setattr(pres, "_get", lambda path: (404, None))
    monkeypatch.setattr(
        pres.urllib.request, "urlopen",
        lambda *a, **k: pytest.fail("preserve made an HTTP request of its own"))

    code, out, _ = _out(capsys, "preserve", "https://github.com/org/a", "--no-color")
    assert code == 0
    assert "NOT ARCHIVED" in out
    assert "archive.softwareheritage.org" in out


def test_the_submit_flag_no_longer_exists(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["preserve", "https://github.com/org/a", "--submit"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_preserve_reads_urls_from_stdin(capsys, monkeypatch):
    import io
    import sys
    monkeypatch.setattr(pres, "_get", lambda path: (404, None))
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        "https://github.com/org/a\n# a comment\n\nhttps://github.com/org/b\n"))

    code, out, _ = _out(capsys, "preserve", "-", "--no-color")
    assert code == 0
    assert "org/a" in out and "org/b" in out
    assert "# a comment" not in out


def test_preserve_with_nothing_on_stdin_is_an_error_not_a_silent_success(
        capsys, monkeypatch):
    import io
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n\n"))

    code, _, err = _out(capsys, "preserve", "-", "--no-color")
    assert code == 2
    assert "no repository URLs" in err


# --------------------------------------------------------------------------- #
# clean
# --------------------------------------------------------------------------- #
def test_clean_lists_first_and_deletes_only_with_yes(capsys, tmp_path):
    from tests.conftest import Repo
    wd = tmp_path / "wd"
    wd.mkdir()
    Repo(wd / "org__repo").commit("a", {"a.py": "a = 1\n"})

    code, out, _ = _out(capsys, "clean", "--work-dir", str(wd))
    assert code == 0
    assert "org__repo" in out and "re-run with --yes" in out
    assert (wd / "org__repo").exists(), "listing deleted something"

    code, out, _ = _out(capsys, "clean", "--work-dir", str(wd), "--yes")
    assert code == 0
    assert "removed 1" in out
    assert not wd.exists()

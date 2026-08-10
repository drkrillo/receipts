"""`trace` and `preserve`, with the network replaced by canned answers.

Both reach outside the machine — `gh api` for who else holds a root commit,
Software Heritage for whether a repository is archived — so neither had ever
run under the suite. What is worth testing is not the HTTP: it is that a failed
call is reported as a failed call, never as a finding, and that nothing is sent
anywhere without being asked for.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from receipts import preserve as pres
from receipts import trace as tr


class _Resp:
    """Enough of an HTTP response for `urlopen` as these modules use it."""

    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------- #
# preserve
# --------------------------------------------------------------------------- #
def test_an_archived_repository_reports_its_swhid(monkeypatch):
    def _get(path):
        if "visits" in path:
            return 200, [{"date": "2026-01-02T03:04:05Z", "status": "full",
                          "snapshot": "abc123"}]
        return 200, {"url": "https://github.com/org/repo"}
    monkeypatch.setattr(pres, "_get", _get)

    st = pres.status("https://github.com/org/repo")
    assert st.archived is True
    assert st.swhid.startswith("swh:1:")
    assert st.last_visit.startswith("2026-01-02")


def test_a_repository_the_archive_does_not_know_is_reported_as_not_archived(
        monkeypatch):
    monkeypatch.setattr(pres, "_get", lambda path: (404, None))

    st = pres.status("https://github.com/org/never-seen")
    assert st.archived is False
    assert not st.error, "a 404 is an answer, not a failure"


def test_a_network_failure_is_an_error_not_a_finding(monkeypatch):
    """"not archived" and "we could not ask" must never look the same.

    They did: `_get` returns code 0 when the request never completed, `status`
    folded that in with a 404, and the line the reader sees next offers to
    submit the repository to a public, permanent archive.
    """
    def _boom(*a, **k):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(pres.urllib.request, "urlopen", _boom)

    st = pres.status("https://github.com/org/repo")
    assert st.archived is False
    assert st.error, "a failed request was reported as a clean 'not archived'"


def test_a_repository_that_could_not_be_checked_is_not_offered_for_submission(
        monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(pres.urllib.request, "urlopen", _boom)

    result = pres.preserve(["https://github.com/org/a"])
    assert result.results[0].error
    assert result.unarchived == []


def test_the_module_has_no_way_to_write_to_the_archive():
    """`submit()` was removed; nothing here may POST."""
    import inspect
    src = inspect.getsource(pres)
    assert "def submit" not in src
    assert "origin/save" not in src
    assert 'method="POST"' not in src


def test_reporting_lists_what_is_missing_without_acting_on_it(monkeypatch):
    monkeypatch.setattr(pres, "_get", lambda path: (404, None))

    result = pres.preserve(["https://github.com/org/a", "https://github.com/org/b"])
    assert len(result.unarchived) == 2


# --------------------------------------------------------------------------- #
# trace
# --------------------------------------------------------------------------- #
def test_the_root_commit_comes_from_the_repository_not_the_api(repo):
    repo.commit("first", {"a.py": "a = 1\n"})
    root = repo.git("rev-list", "--max-parents=0", "HEAD")
    repo.commit("second", {"b.py": "b = 1\n"})

    assert tr.root_commit(repo.path) == root


def test_holders_of_history_reports_every_repository_the_search_returns(monkeypatch):
    monkeypatch.setattr(tr, "_gh", lambda path: {"items": [
        {"repository": {"full_name": "org/one", "fork": False,
                        "html_url": "https://github.com/org/one"}},
        {"repository": {"full_name": "org/two", "fork": True,
                        "html_url": "https://github.com/org/two"}}]})

    holders = tr.holders_of_history("a" * 40)
    assert [h.name for h in holders] == ["org/one", "org/two"]
    # GitHub's fork flag is recorded, never trusted: of 189 repositories holding
    # gogs/gogs's root commit, it declares none.
    assert [h.declared_fork for h in holders] == [False, True]
    assert {h.how for h in holders} == {"history"}


def test_a_failed_search_yields_nothing_rather_than_an_empty_finding(monkeypatch):
    monkeypatch.setattr(tr, "_gh", lambda path: None)
    assert tr.holders_of_history("a" * 40) == []


def test_gh_is_required_before_anything_is_searched(monkeypatch):
    monkeypatch.setattr(tr.subprocess, "run",
                        lambda *a, **k: pytest.fail("ran a search without gh"))
    monkeypatch.setattr(tr, "_gh", lambda path: None)
    assert tr.holders_of_history("a" * 40) == []


def test_a_missing_gh_says_how_to_install_it(monkeypatch):
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    with pytest.raises(tr.GitHubCLIUnavailable) as exc:
        tr.require_gh()
    msg = str(exc.value)
    assert "gh" in msg and ("install" in msg.lower() or "brew" in msg.lower())


def test_the_env_file_is_read_without_exporting_anything_else(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("RECEIPTS_SWH_TOKEN=secret-value\n# a comment\nNOT_A_PAIR\n")
    monkeypatch.delenv("RECEIPTS_SWH_TOKEN", raising=False)

    tr.load_env(str(env))
    assert tr.swh_token() == "secret-value"


def test_a_missing_env_file_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.delenv("RECEIPTS_SWH_TOKEN", raising=False)
    tr.load_env(str(tmp_path / "nothing-here"))
    assert tr.swh_token() == ""


def test_trace_reports_holders_and_the_archive_state_together(repo, monkeypatch):
    repo.commit("first", {"a.py": "print('hello world, this is a long line')\n"})
    monkeypatch.setattr(tr, "require_gh", lambda: None)
    monkeypatch.setattr(tr, "holders_of_history", lambda root: [
        tr.Holder(name="org/one", how="history", declared_fork=False)])
    monkeypatch.setattr(tr, "archive_status", lambda url, token="": "archived")

    result = tr.trace(repo.path, origin_url="https://github.com/org/origin")
    assert [h.name for h in result.holders] == ["org/one"]
    assert result.root_commit == repo.git("rev-list", "--max-parents=0", "HEAD")
    assert result.archived == "archived"

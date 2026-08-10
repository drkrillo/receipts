"""The temporal layer against real commit graphs.

This is the part of the tool nothing else does, and it was the part with no test
that ran git. Each test here builds the history it describes, so the assertion
and the evidence are the same object.
"""

from __future__ import annotations

from receipts import gitobjects as git
from receipts import overlap as ov
from receipts import timeline as tl
from tests.conftest import APACHE, BSD, MIT, PROPRIETARY, clone


def _relicensing_history(repo):
    """NONE -> MIT -> BSD -> Apache, with a release branch off the MIT era."""
    repo.commit("initial", {"src.py": "x = 1\n"}, date="2018-01-01T00:00:00+00:00")
    mit = repo.commit("add a licence", {"LICENSE": MIT},
                      date="2018-02-01T00:00:00+00:00")
    repo.commit("work under MIT", {"a.py": "a = 1\n"},
                date="2018-06-01T00:00:00+00:00")
    repo.branch("release-1.x")
    repo.commit("backport, still MIT", {"b.py": "b = 1\n"},
                date="2019-03-01T00:00:00+00:00")
    repo.checkout("main")
    bsd = repo.commit("relicense to BSD", {"LICENSE": BSD},
                      date="2020-01-01T00:00:00+00:00")
    repo.commit("work under BSD", {"c.py": "c = 1\n"},
                date="2020-06-01T00:00:00+00:00")
    apache = repo.commit("relicense to Apache", {"LICENSE": APACHE},
                         date="2021-01-01T00:00:00+00:00")
    repo.commit("work under Apache", {"d.py": "d = 1\n"},
                date="2021-06-01T00:00:00+00:00")
    return mit, bsd, apache


def test_timeline_reports_each_relicensing_once(repo, fake_detector):
    mit, bsd, apache = _relicensing_history(repo)

    got = [(t.commit, t.before, t.after) for t in tl.timeline(repo.path)]
    assert got == [(mit, "NONE", "MIT"),
                   (bsd, "MIT", "BSD-3-Clause"),
                   (apache, "BSD-3-Clause", "Apache-2.0")]
    assert [t.date[:10] for t in tl.timeline(repo.path)] == [
        "2018-02-01", "2020-01-01", "2021-01-01"]


def test_a_licence_rename_is_not_reported_as_losing_the_licence(repo, fake_detector):
    """One commit is one state, however many files it touches."""
    add = repo.commit("add", {"LICENSE": MIT})
    repo.commit("rename the file", {"LICENSE": None, "LICENSE.md": MIT})

    assert [(t.commit, t.before, t.after) for t in tl.timeline(repo.path)] == [
        (add, "NONE", "MIT")]


def test_deleting_the_licence_is_reported_when_nothing_replaces_it(repo, fake_detector):
    add = repo.commit("add", {"LICENSE": MIT})
    gone = repo.commit("remove the licence", {"LICENSE": None})

    assert [(t.commit, t.before, t.after) for t in tl.timeline(repo.path)] == [
        (add, "NONE", "MIT"), (gone, "MIT", "NONE")]


def test_deleting_one_licence_file_while_another_remains_is_not_a_loss(repo,
                                                                      fake_detector):
    """redis deleted COPYING and created LICENSE.txt in the same commit."""
    repo.commit("two files", {"COPYING": MIT, "LICENSE": MIT})
    repo.commit("drop the old name", {"COPYING": None})

    assert [(t.before, t.after) for t in tl.timeline(repo.path)] == [("NONE", "MIT")]


def test_a_relicensing_only_on_an_abandoned_branch_is_surfaced(repo, fake_detector):
    """The finding no other tool makes: a grant reachable from no branch tip.

    A project that rewrites history leaves licence states behind. Reading the
    mainline alone reports the wrong licence with full confidence.
    """
    repo.commit("add", {"LICENSE": MIT})
    repo.branch("abandoned")
    hidden = repo.commit("quietly relicense", {"LICENSE": PROPRIETARY})
    repo.checkout("main")
    repo.commit("carry on under MIT", {"x.py": "x = 1\n"})

    states = tl.orphaned_states(repo.path, {"MIT", "NONE"})
    assert [(c, s) for c, _, s in states] == [(hidden, "proprietary-license")]


def test_a_state_the_mainline_also_records_is_not_called_orphaned(repo,
                                                                 fake_detector):
    """mapbox-gl-js relicensed to proprietary ON ITS MAINLINE and was flagged."""
    repo.commit("add", {"LICENSE": MIT})
    repo.branch("abandoned")
    repo.commit("relicense here first", {"LICENSE": PROPRIETARY})
    repo.checkout("main")
    repo.commit("and on the mainline too", {"LICENSE": PROPRIETARY})

    known = {t.after for t in tl.timeline(repo.path)}
    assert tl.orphaned_states(repo.path, known) == []


# --------------------------------------------------------------------------- #
# Shared commits crossed with the origin's licence history.
# --------------------------------------------------------------------------- #
def test_a_fork_is_attributed_the_licence_in_force_when_each_commit_was_made(
        repo, tmp_path, fake_detector):
    """The claim the tool exists to make, end to end on a real graph."""
    mit, bsd, apache = _relicensing_history(repo)
    # The fork leaves during the BSD era, before the Apache relicensing.
    fork = clone(repo, tmp_path / "fork", at=bsd)
    fork.commit("our own work", {"fork.py": "f = 1\n"})

    shared = git.all_commits(repo.path) & git.all_commits(fork.path)
    eras = ov.shared_by_licence_era(repo.path, shared, tl.timeline(repo.path))

    assert [(e["licence"], e["commits"]) for e in eras] == [
        ("NONE", 1), ("MIT", 2), ("BSD-3-Clause", 1)]
    # Nothing the fork holds was made under Apache, and the tool must not say so.
    assert "Apache-2.0" not in [e["licence"] for e in eras]
    # every era cites the commit that opened it
    assert eras[1]["from_commit"] == mit and eras[2]["from_commit"] == bsd


def test_commits_on_a_release_branch_keep_the_licence_of_where_it_left(
        repo, tmp_path, fake_detector):
    """A branch commit is later in time than a relicensing it never saw.

    Attributing by ancestry of the change instead of descent from it put these
    in an "unattributable" row — 58% of what gogs shares with gitea.
    """
    _relicensing_history(repo)
    fork = clone(repo, tmp_path / "fork", branch="release-1.x")

    shared = git.all_commits(repo.path) & git.all_commits(fork.path)
    eras = ov.shared_by_licence_era(repo.path, shared, tl.timeline(repo.path))

    # The 2019 backport is newer than nothing yet, but it never saw BSD (2020).
    assert [(e["licence"], e["commits"]) for e in eras] == [("NONE", 1), ("MIT", 3)]
    assert sum(e["commits"] for e in eras) == len(shared)


def test_every_shared_commit_lands_in_exactly_one_era(repo, tmp_path,
                                                      fake_detector):
    _relicensing_history(repo)
    fork = clone(repo, tmp_path / "fork")
    fork.commit("fork work", {"f.py": "f = 1\n"})
    shared = git.all_commits(repo.path) & git.all_commits(fork.path)

    eras = ov.shared_by_licence_era(repo.path, shared, tl.timeline(repo.path))
    assert sum(e["commits"] for e in eras) == len(shared)
    assert all(e["commits"] > 0 for e in eras)


def test_a_grafted_root_claims_no_licence_rather_than_the_first_one(
        repo, tmp_path, fake_detector):
    """Neither ancestor nor descendant of any licence commit: say nothing."""
    _relicensing_history(repo)
    repo.orphan("vendored", "another project's root", {"lib/x.py": "x = 1\n"})
    orphan_root = repo.head()
    repo.checkout("main")
    repo.merge("vendored", "vendor a whole history")

    shared = git.all_commits(repo.path)
    eras = ov.shared_by_licence_era(repo.path, shared, tl.timeline(repo.path))

    blank = [e for e in eras if not e["licence"]]
    assert blank and blank[0]["commits"] >= 1
    assert sum(e["commits"] for e in eras) == len(shared)
    assert orphan_root in shared


# --------------------------------------------------------------------------- #
# The anchor: which commit stands for "the upstream when the fork left".
# --------------------------------------------------------------------------- #
def test_the_anchor_is_the_newest_shared_commit_on_the_upstream_mainline(
        repo, tmp_path, fake_detector):
    _relicensing_history(repo)
    fork = clone(repo, tmp_path / "fork")
    fork.commit("fork work", {"f.py": "f = 1\n"})

    shared = git.all_commits(repo.path) & git.all_commits(fork.path)
    common, _ = ov.most_recent_common_commit(fork.path, shared)
    anchor, basis = ov.anchor_commit(repo.path, fork.path, common)

    assert anchor == repo.head()
    assert git.is_ancestor(repo.path, anchor)
    assert "mainline" in basis


def test_an_anchor_off_the_mainline_is_replaced_and_the_basis_says_so(
        repo, tmp_path, fake_detector):
    """The newest shared commit can sit on a branch the fork pushed back.

    Reading the licence there once reported the upstream as unlicensed and
    exculpated the party that had removed it.
    """
    _relicensing_history(repo)
    mainline_tip = repo.head()
    # A branch of the upstream that the fork also holds, but which HEAD does not
    # reach — a contributed branch, or a fetched PR ref.
    repo.branch("contributed", at=mainline_tip)
    off = repo.commit("on a branch nobody merged", {"z.py": "z = 1\n"},
                      date="2022-01-01T00:00:00+00:00")
    repo.checkout("main")
    fork = clone(repo, tmp_path / "fork", branch="contributed")

    shared = git.all_commits(repo.path) & git.all_commits(fork.path)
    common, _ = ov.most_recent_common_commit(fork.path, shared)
    assert common == off and not git.is_ancestor(repo.path, off)

    anchor, basis = ov.anchor_commit(repo.path, fork.path, common)
    assert anchor == mainline_tip
    assert git.is_ancestor(repo.path, anchor)
    assert "not on it" in basis


def test_with_no_shared_history_the_anchor_falls_back_to_a_date(
        repo_factory, fake_detector):
    """Aligning by date must understate, never overstate, the upstream state."""
    up, other = repo_factory("up"), repo_factory("other")
    up.commit("early", {"a.py": "a = 1\n"}, date="2015-01-01T00:00:00+00:00")
    early = up.head()
    up.commit("later", {"b.py": "b = 1\n"}, date="2022-01-01T00:00:00+00:00")
    other.commit("unrelated", {"c.py": "c = 1\n"}, date="2016-01-01T00:00:00+00:00")

    anchor, basis = ov.anchor_commit(up.path, other.path, None)
    assert anchor == early
    assert "when the suspect's history begins" in basis


def test_a_vendored_dependency_licence_is_not_the_project_relicensing(repo,
                                                                     fake_detector):
    """Akka vendored protobuf in 2015 and added `COPYING.protobuf` at the root.

    It starts with COPYING, so it read as the canonical licence file, and
    `licdrift` reported Akka relicensing from Apache-2.0 to protobuf and back
    six months later — putting 544 shared commits under a licence Akka never
    adopted.
    """
    repo.commit("licence", {"LICENSE": APACHE})
    repo.commit("vendor a dependency",
                {"COPYING.protobuf": BSD, "lib/dep.py": "d = 1\n"})
    repo.commit("drop the dependency", {"COPYING.protobuf": None})

    assert [(t.before, t.after) for t in tl.timeline(repo.path)] == [
        ("NONE", "Apache-2.0")]


def test_the_project_licence_is_still_read_when_only_a_bundled_one_is_touched(
        repo, fake_detector):
    """The state after a commit is the project's own licence file, whichever
    file the commit happened to touch."""
    repo.commit("licence", {"LICENSE": APACHE})
    repo.commit("bundle", {"COPYING.protobuf": BSD})
    relicense = repo.commit("actually relicense", {"LICENSE": MIT})

    assert [(t.commit, t.after) for t in tl.timeline(repo.path)][-1] == (
        relicense, "MIT")


def test_a_bundled_licence_in_unreachable_history_is_not_a_hidden_grant(repo,
                                                                       fake_detector):
    """valkey vendors libraries; two of their licence files were reported as
    states the project had granted and left off its mainline."""
    repo.commit("licence", {"LICENSE": MIT})
    repo.branch("abandoned")
    repo.commit("vendor a library here", {"deps/hiredis/COPYING": BSD})
    repo.checkout("main")

    assert tl.orphaned_states(repo.path, {"MIT", "NONE"}) == []


def test_a_licence_kept_in_a_subdirectory_is_still_the_project_licence(repo,
                                                                      fake_detector):
    """nginx kept its licence at `docs/text/LICENSE` for twenty years.

    A git pathspec matches from the root, so `LICENSE*` never saw it: the report
    stated BSD-2-Clause at the divergence commit and, four lines below, that all
    9,208 shared commits were made with no licence file at all.
    """
    added = repo.commit("licence, in the docs tree", {"docs/text/LICENSE": BSD})
    repo.commit("work", {"src.c": "int main(void) { return 0; }\n"})

    assert [(t.commit, t.after, t.file) for t in tl.timeline(repo.path)] == [
        (added, "BSD-3-Clause", "docs/text/LICENSE")]


def test_a_licences_directory_is_auxiliary_not_the_grant(repo, fake_detector):
    """`LICENSES/` is the REUSE folder: several licences that coexist."""
    repo.commit("licence", {"LICENSE": MIT})
    repo.commit("add REUSE files", {"LICENSES/BSD-3-Clause.txt": BSD,
                                    "LICENSES/Apache-2.0.txt": APACHE})

    assert [(t.before, t.after) for t in tl.timeline(repo.path)] == [("NONE", "MIT")]


def test_the_anchor_ignores_upstream_commits_the_fork_only_stores(repo, tmp_path,
                                                                  fake_detector):
    """A fork's clone keeps the upstream's other branches.

    openbao/openbao holds 2,195 Vault commits its own line never reaches. One of
    them, made seven months after HashiCorp relicensed, was picked as the anchor
    — and the report stated that OpenBao's code came from Vault under BUSL-1.1.
    Zero post-BUSL Vault mainline commits are ancestors of openbao's HEAD.
    """
    repo.commit("initial", {"src.py": "x = 1\n"}, date="2018-01-01T00:00:00+00:00")
    repo.commit("licence", {"LICENSE": MIT}, date="2018-02-01T00:00:00+00:00")
    left_here = repo.commit("last permissive commit", {"a.py": "a = 1\n"},
                            date="2023-05-26T00:00:00+00:00")
    repo.commit("relicense", {"LICENSE": PROPRIETARY},
                date="2023-08-10T00:00:00+00:00")
    after = repo.commit("work under the new terms", {"b.py": "b = 1\n"},
                        date="2024-03-18T00:00:00+00:00")

    # The fork left at `left_here` but keeps a copy of the upstream's branch.
    fork = clone(repo, tmp_path / "fork", at=left_here)
    fork.git("fetch", "-q", repo.path, f"{after}:refs/heads/upstream-main")
    fork.commit("our own work", {"fork.py": "f = 1\n"})

    assert after in git.all_commits(fork.path)
    assert after not in git.ancestors(fork.path, "HEAD")

    anchor = ov.newest_shared_on_mainline(repo.path, fork.path)
    assert anchor == left_here, "the anchor crossed the relicensing"

    shared = git.all_commits(repo.path) & git.all_commits(fork.path)
    on_line = shared & git.ancestors(fork.path, "HEAD")
    eras = ov.shared_by_licence_era(repo.path, on_line, tl.timeline(repo.path))
    assert "proprietary-license" not in [e["licence"] for e in eras]
    assert [(e["licence"], e["commits"]) for e in eras] == [("NONE", 1), ("MIT", 2)]

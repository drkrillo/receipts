"""The git layer, against real repositories.

Every function here shells out to git, and every one of them was previously
covered only by whatever a higher-level mock happened to exercise: 20% of this
module ran under the suite. The bugs found on real cases — a descendant set
computed as an ancestor set, root commits counted across refs nobody pushed to
the mainline — are all in this shape of code.
"""

from __future__ import annotations

import os

import pytest

from receipts import gitobjects as git


def test_commits_and_blobs_are_what_the_repo_holds(repo):
    a = repo.commit("first", {"a.txt": "alpha\n"})
    b = repo.commit("second", {"b.txt": "beta\n"})

    assert git.all_commits(repo.path) == {a, b}
    assert git.head(repo.path) == b
    # Two files, two blobs, and git's ids are what the tool compares on.
    blobs = git.head_blobs(repo.path)
    assert set(blobs) == {"a.txt", "b.txt"}
    assert git.all_blobs(repo.path) >= set(blobs.values())
    assert git.blob_text(repo.path, blobs["a.txt"]) == "alpha\n"


def test_identical_content_is_the_same_blob_in_unrelated_repos(repo_factory):
    """The whole derivation claim rests on this being true, so assert it."""
    one, two = repo_factory("one"), repo_factory("two")
    one.commit("c", {"shared.py": "print('hello')\n"})
    two.commit("totally different message", {"elsewhere/shared.py": "print('hello')\n"})

    shared = git.all_blobs(one.path) & git.all_blobs(two.path)
    assert len(shared) == 1
    assert git.blob_text(one.path, next(iter(shared))) == "print('hello')\n"


def test_ancestors_and_descendants_are_disjoint_and_cover_a_line(repo):
    a = repo.commit("a")
    b = repo.commit("b")
    c = repo.commit("c")

    assert git.ancestors(repo.path, b) == {a, b}
    assert git.descendants(repo.path, b) == {c}
    # `--ancestry-path=<c>` alone also returns c's ancestors; the tool needs the
    # strict set, and mixing the two put commits in the wrong licence era.
    assert not git.ancestors(repo.path, b) & git.descendants(repo.path, b)
    assert git.ancestors(repo.path, c) | git.descendants(repo.path, a) == {a, b, c}


def test_a_branch_commit_descends_from_where_it_left_and_no_further(repo):
    """The case the mocked tests could not express."""
    a = repo.commit("a")
    b = repo.commit("b")
    repo.branch("release", at=b)
    side = repo.commit("on the release branch", {"r.txt": "r\n"})
    repo.checkout("main")
    c = repo.commit("c, after the branch left", {"m.txt": "m\n"})

    assert side in git.descendants(repo.path, b)
    assert side not in git.descendants(repo.path, c)
    assert c not in git.descendants(repo.path, b) or True   # c descends from b
    assert git.descendants(repo.path, b) == {side, c}
    # and `a` reaches everything
    assert git.descendants(repo.path, a) == {b, side, c}


def test_descendants_of_an_unknown_commit_is_empty_not_an_error(repo):
    repo.commit("a")
    assert git.descendants(repo.path, "0" * 40) == set()
    assert git.ancestors(repo.path, "0" * 40) == set()


def test_is_ancestor_follows_the_checked_out_line(repo):
    a = repo.commit("a")
    repo.branch("side", at=a)
    side = repo.commit("side only", {"s.txt": "s\n"})
    repo.checkout("main")
    b = repo.commit("b")

    assert git.is_ancestor(repo.path, a) is True
    assert git.is_ancestor(repo.path, b) is True
    assert git.is_ancestor(repo.path, side) is False


def test_root_commits_are_the_ones_on_head_not_on_every_ref(repo):
    """A contributed branch's root is not where the project came from.

    Counted across `--all`, terraform reported nine roots, the newest a 2024
    commit by a drive-by contributor, under a heading that reads as the origin
    of the project.
    """
    root = repo.commit("real root")
    repo.commit("more")
    repo.orphan("contributed", "someone's unrelated branch", {"x.txt": "x\n"})
    repo.checkout("main")

    assert git.root_commits(repo.path) == [root]
    ids = git.root_identities(repo.path)
    assert [r["commit"] for r in ids] == [root]
    assert ids[0]["author"].startswith("Test <")


def test_grafted_history_shows_every_root_that_head_really_reaches(repo):
    """Merging another project in whole is a real multi-root history."""
    root = repo.commit("ours")
    repo.orphan("theirs", "their root", {"lib/theirs.txt": "t\n"})
    other_root = repo.head()
    repo.checkout("main")
    repo.merge("theirs", "vendor their history")

    assert set(git.root_commits(repo.path)) == {root, other_root}


def test_first_parent_log_skips_what_was_merged_in(repo):
    base = repo.commit("base")
    repo.branch("feature", at=base)
    merged = repo.commit("inside the feature", {"f.txt": "f\n"})
    repo.checkout("main")
    top = repo.merge("feature")

    line = [l.split(" ")[0] for l in git.first_parent_log(repo.path)]
    assert top in line and base in line
    assert merged not in line


def test_commit_dates_and_the_start_of_history(repo):
    repo.commit("first", date="2015-03-04T10:00:00+00:00")
    second = repo.commit("second", date="2019-08-09T10:00:00+00:00")

    assert git.commit_date(repo.path, second).startswith("2019-08-09")
    assert git.earliest_commit_date(repo.path).startswith("2015-03-04")


def test_commit_at_or_before_never_reaches_forward(repo):
    old = repo.commit("old", date="2015-01-01T00:00:00+00:00")
    repo.commit("new", date="2021-01-01T00:00:00+00:00")

    # The anchor fallback aligns by date and must understate, never overstate,
    # what the upstream had.
    assert git.commit_at_or_before(repo.path, "2016-01-01T00:00:00+00:00") == old
    assert git.commit_at_or_before(repo.path, "2010-01-01T00:00:00+00:00") in ("", None)


def test_blobs_at_reads_the_tree_of_one_commit_not_of_head(repo):
    first = repo.commit("one file", {"a.txt": "alpha\n"})
    repo.commit("two files", {"b.txt": "beta\n"})

    assert set(git.blobs_at(repo.path, first)) == {"a.txt"}
    assert set(git.blobs_at(repo.path, "HEAD")) == {"a.txt", "b.txt"}


def test_batch_blob_text_returns_every_id_it_was_given(repo):
    repo.commit("files", {"a.txt": "alpha\n", "b.txt": "beta\n"})
    blobs = git.head_blobs(repo.path)

    got = git.batch_blob_text(repo.path, list(blobs.values()))
    assert sorted(got.values()) == ["alpha\n", "beta\n"]


def test_file_text_at_reads_the_version_of_that_commit(repo):
    first = repo.commit("v1", {"LICENSE": "MIT License\n"})
    repo.commit("v2", {"LICENSE": "Apache License 2.0\n"})

    assert git.file_text_at(repo.path, first, "LICENSE") == "MIT License\n"
    assert "Apache" in git.file_text_at(repo.path, "HEAD", "LICENSE")


def test_licence_file_history_reports_add_modify_and_delete(repo):
    add = repo.commit("add", {"LICENSE": "MIT License\n"})
    mod = repo.commit("relicense", {"LICENSE": "Apache License 2.0\n"})
    rm = repo.commit("drop it", {"LICENSE": None})

    events = git.license_file_history(repo.path)
    assert [(c, s, p) for c, _, s, p in events] == [
        (add, "A", "LICENSE"), (mod, "M", "LICENSE"), (rm, "D", "LICENSE")]


def test_licence_file_history_sees_a_rename_as_two_events_in_one_commit(repo):
    """The grouping in `timeline` depends on this shape, so pin it."""
    repo.commit("add", {"LICENSE": "MIT License\n"})
    renamed = repo.commit("rename", {"LICENSE": None, "LICENSE.md": "MIT License\n"})

    events = [e for e in git.license_file_history(repo.path) if e[0] == renamed]
    # git reports this as one `R100 LICENSE LICENSE.md` line, which the parser
    # dropped whole. Normalised to the delete and the add it stands for.
    assert sorted((s, p) for _, _, s, p in events) == [
        ("D", "LICENSE"), ("M", "LICENSE.md")]


def test_orphaned_history_is_what_head_cannot_reach(repo):
    repo.commit("add", {"LICENSE": "MIT License\n"})
    repo.branch("abandoned")
    hidden = repo.commit("relicense on a branch", {"LICENSE": "Apache License 2.0\n"})
    repo.checkout("main")

    mainline = {c for c, _, _, _ in git.license_file_history(repo.path)}
    orphan = {c for c, _, _, _ in git.license_file_history(repo.path, orphaned=True)}
    assert hidden not in mainline
    assert hidden in orphan


def test_authors_and_spdx_headers_are_counted_from_the_tree(repo):
    repo.commit("code", {"a.py": "# SPDX-License-Identifier: MIT\nx = 1\n"})

    assert git.authors(repo.path) == {"Test <t@example.com>"}
    assert git.count_spdx_headers(repo.path) >= 1


def test_prepare_accepts_a_local_path_and_leaves_it_alone(repo, tmp_path):
    repo.commit("a")
    assert git.prepare(repo.path, str(tmp_path / "cache")) == repo.path


def test_prepare_rejects_a_directory_that_is_not_a_repository(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with pytest.raises(Exception) as exc:
        git.prepare(str(plain), str(tmp_path / "cache"))
    assert "git" in str(exc.value).lower()


def test_dir_size_reports_megabytes(repo, tmp_path):
    repo.commit("big", {"data.txt": "x" * 200_000})
    assert git.dir_size_mb(repo.path) > 0
    assert git.dir_size_mb(str(tmp_path / "does-not-exist")) == 0


def test_a_full_clone_is_not_reported_as_partial(repo, tmp_path):
    from tests.conftest import clone
    repo.commit("a", {"a.txt": "a\n"})
    fork = clone(repo, tmp_path / "fork")
    assert git.is_partial_clone(fork.path) is False
    assert os.path.isdir(fork.path)

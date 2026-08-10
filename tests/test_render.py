"""What each output format is allowed to say.

Nothing in this file ran under the suite before: `terminal.py` was at 0%, and
three of the false statements found on real cases lived in it — a `yes` that
meant "an anchor exists" under a heading that read "this commit is on the
mainline", a key long enough to collide with its own value, an attribution
label on the wrong side of an arrow.

The three formats are not three copies of one document. The terminal is the
working surface: every line is a number or a hash, and the commands that check
them are on screen. The Markdown is the document handed to someone else, so it
carries the method as well as the result. The JSON is the machine record and
carries everything.
"""

from __future__ import annotations

import json

import pytest

from receipts import report as rep
from receipts import terminal as term
from receipts.models import (AttributionDiff, Custody, LicenseState, Overlap,
                             Report)


def flat(text: str) -> str:
    """Output with its line wrapping removed, for asserting on sentences."""
    return " ".join(text.split())


def _state(spdx, governing=None, file="LICENSE"):
    return LicenseState(spdx_id=spdx, governing=governing or spdx, confidence=1.0,
                        source_file=file, detector="scancode",
                        full_expression=spdx)


def _report(**over):
    o = over.pop("overlap", None) or Overlap(
        total_commits_a=200, total_commits_b=300, shared_commits=150,
        shared_pct_of_upstream=75.0, shared_pct_of_suspect=50.0,
        common_commit="a" * 40, common_commit_date="2020-01-01T00:00:00+00:00",
        shared_blobs=900, distinctive_shared_blobs=40)
    base = dict(
        upstream="https://github.com/org/origin",
        suspect="https://github.com/org/fork",
        overlap=o,
        upstream_license_at_divergence=_state("BSD-3-Clause"),
        upstream_license_now=_state("SSPL-1.0 AND AGPL-3.0-only", "AGPL-3.0-only"),
        suspect_license_now=_state("BSD-3-Clause"),
        licence_eras=[{"licence": "NONE", "commits": 5,
                       "from_commit": "", "from_date": ""},
                      {"licence": "BSD-3-Clause", "commits": 145,
                       "from_commit": "b" * 40, "from_date": "2009-03-22"}],
        attribution=AttributionDiff(
            upstream_authors=[], suspect_authors=[],
            upstream_authors_present_in_suspect=True,
            upstream_notice_present=True, suspect_notice_present=True,
            notice_stripped=False, files_compared=121,
            files_header_replaced=1, files_header_dropped=0,
            header_examples=["src/x.h: 'Copyright 2011 Origin' (the upstream "
                             "project) -> 'Copyright Fork Contributors.'"]),
        confidence="high", derivation_likely=True, derivation_needs_review="",
        license_differs_from_upstream=False,
        observations=[], open_questions=["Who holds copyright in the upstream code?"],
        summary="",
        custody=Custody(
            generated_at="2026-01-01T00:00:00+00:00", tool_version="0.1.0",
            git_version="git version 2.50.1", upstream_ref="origin",
            suspect_ref="fork", upstream_head="c" * 40, suspect_head="d" * 40,
            evidence_sha256="e" * 64, license_detector="scancode",
            license_detector_version="32.5.0",
            anchor_commit="a" * 40,
            anchor_basis="most recent shared commit on the upstream's mainline"),
        upstream_roots=[{"commit": "f" * 40, "date": "2009-03-22T00:00:00+00:00",
                         "author": "A <a@x>", "committer": "A <a@x>"}],
        unattributed=[])
    base.update(over)
    return Report(**base)


# --------------------------------------------------------------------------- #
# The terminal
# --------------------------------------------------------------------------- #
def test_the_terminal_report_states_the_commit_every_reading_was_taken_at():
    out = term.render_report(_report(), colour=False)
    assert "read at" in out and "a" * 10 in out
    assert "most recent shared commit on the upstream's mainline" in flat(out)


def test_the_licence_comparison_names_which_two_licences_it_compared():
    """"same licence: yes" with three licences on screen says nothing."""
    out = term.render_report(_report(), colour=False)
    assert "compared now vs divergence" in out
    assert "same licence" not in out


def test_a_long_key_never_runs_into_its_own_value():
    out = term.render_report(_report(), colour=False)
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("compared now vs divergence"):
            assert "divergence  " in line, f"key collided with value: {line!r}"


def test_the_era_table_totals_the_shared_commits():
    out = term.render_report(_report(), colour=False)
    assert "145" in out and "BSD-3-Clause" in out
    assert "from 2009-03-22" in out


def test_the_terminal_report_carries_no_paragraphs():
    """It is a working surface, not a document. Prose belongs in the Markdown."""
    out = term.render_report(_report(), colour=False)
    body = [l for l in out.splitlines()
            if not l.strip().startswith(("?", "!", "-", "=", "#"))]
    long_prose = [l for l in body if len(l.strip().split()) > 14]
    assert not long_prose, f"prose in the terminal report: {long_prose[:2]}"


def test_the_terminal_report_never_reads_as_a_verdict():
    out = term.render_report(_report(), colour=False).lower()
    for banned in ("violat", "infring", "stole", "illegal", "unauthorized",
                   "derivation likely"):
        assert banned not in out


@pytest.mark.parametrize("anchor,expected", [("a" * 40, "yes"), ("z" * 40, "no")])
def test_overlap_says_yes_only_when_the_shared_commit_is_the_anchor(anchor, expected):
    """`anchor` being set means an anchor EXISTS, not that this commit is it.

    mapbox-gl-js printed `on origin's main line: yes` for a commit and then
    named a different commit as the newest one on the mainline.
    """
    r = _report()
    out = term.render_overlap(
        r.upstream, r.suspect, r.overlap, "high", True, "", anchor,
        "most recent shared commit on the upstream's mainline", 0, colour=False)
    line = [l for l in out.splitlines() if "on origin's main line" in l][0]
    assert line.strip().endswith(expected)


def test_overlap_prints_a_verify_command_for_every_hash_it_names():
    r = _report()
    out = term.render_overlap(r.upstream, r.suspect, r.overlap, "high", True, "",
                              "z" * 40, "a different commit", 0, colour=False)
    verify = out.split("verify")[1]
    assert "a" * 10 in verify and "z" * 10 in verify


def test_overlap_with_no_shared_history_still_says_where_it_read():
    o = Overlap(total_commits_a=100, total_commits_b=100, shared_commits=0,
                shared_pct_of_upstream=0.0, shared_pct_of_suspect=0.0,
                common_commit=None, common_commit_date=None,
                shared_blobs=37, distinctive_shared_blobs=10)
    out = term.render_overlap("origin", "fork", o, "medium", True, "", "z" * 40,
                              "upstream as of 2015-01-01, when the suspect's "
                              "history begins", 0, colour=False)
    assert "upstream read at" in out
    assert "when the suspect's history begins" in flat(out)
    # It must NOT claim a shared commit it does not have.
    assert "newest commit in both" not in out


def test_licdrift_marks_a_file_edit_that_left_the_grant_alone():
    from receipts.models import LicenseTransition
    ts = [LicenseTransition(date="2014-03-17", commit="1" * 40, before="NONE",
                            after="BSD-3-Clause AND MIT", file="LICENSE.txt",
                            before_governing="NONE",
                            after_governing="BSD-3-Clause"),
          LicenseTransition(date="2018-05-29", commit="2" * 40,
                            before="BSD-3-Clause AND MIT", after="BSD-3-Clause",
                            file="LICENSE.txt", before_governing="BSD-3-Clause",
                            after_governing="BSD-3-Clause")]
    out = term.render_licdrift("origin", ts, colour=False)
    assert "grant unchanged, licence file edited" in out
    # and the headline is a pick, which the reader has to be told
    assert "most restrictive of 2 detected" in out


def test_licdrift_says_plainly_when_a_project_has_no_licence_file():
    out = term.render_licdrift("origin", [], colour=False)
    assert "No license file found" in out


def test_an_orphaned_state_is_reported_without_asserting_a_grant_was_made():
    out = term.render_licdrift("origin", [], [("9" * 40, "2021-07-02", "MIT")],
                               colour=False)
    assert "NOT ON THE MAINLINE" in out and "9" * 10 in out
    # It used to conclude: "a grant made here was not revoked by a later change".
    assert "grant made here" not in out
    assert "git merge-base --is-ancestor" in out


# --------------------------------------------------------------------------- #
# The Markdown document
# --------------------------------------------------------------------------- #
def test_markdown_and_terminal_name_the_same_licence():
    """report.txt and report.md ship in the same package and disagreed.

    The Markdown headlined ScanCode's whole expression while the terminal
    headlined the grant: `AGPL-3.0-only` in one file, a nine-term `AND` in the
    other, for the same LICENSE at the same commit.
    """
    r = _report()
    md, txt = rep.render_markdown(r), term.render_report(r, colour=False)
    assert "**upstream now:** AGPL-3.0-only" in md
    assert "AGPL-3.0-only" in txt
    # the full expression is kept as evidence, not as the headline
    assert "SSPL-1.0 AND AGPL-3.0-only" in md


def test_markdown_carries_the_method_the_terminal_has_no_room_for():
    md = rep.render_markdown(_report())
    assert "Where the upstream was read" in md
    assert "merge-base --is-ancestor" in md
    assert "## What is outside this evidence" in md
    # ...and not a roadmap
    assert "v0.2" not in md


def test_markdown_summary_does_not_name_a_direction_of_copying():
    """The same document says two repositories cannot establish direction."""
    line = rep._summarize("origin", "fork", _report().overlap,
                          _state("BSD-3-Clause"), _state("MIT"),
                          _report().attribution, "high", True)
    low = line.lower()
    for banned in ("code from", "is present in", "took", "copied", "derived from"):
        assert banned not in low, f"summary claims a direction: {line}"
    assert "in common" in low


def test_markdown_carries_the_era_table():
    md = rep.render_markdown(_report())
    assert "| commits | licence | in force from |" in md
    assert "| 145 |" in md


# --------------------------------------------------------------------------- #
# The JSON record
# --------------------------------------------------------------------------- #
def test_json_is_complete_and_parses():
    d = json.loads(rep.render_json(_report()))
    assert d["custody"]["anchor_commit"] == "a" * 40
    assert d["custody"]["anchor_basis"]
    assert d["licence_eras"][1]["commits"] == 145
    assert d["overlap"]["shared_commits"] == 150
    assert d["upstream_license_at_divergence"]["governing"] == "BSD-3-Clause"


def test_json_is_stable_across_renders():
    r = _report()
    assert rep.render_json(r) == rep.render_json(r)

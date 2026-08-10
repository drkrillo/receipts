"""Unit tests for the pure logic (no repos needed) — fast + deterministic."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts.models import Overlap                      # noqa: E402
from receipts.overlap import confidence_and_flags  # noqa: E402


def _ov(shared_commits=0, distinctive=0):
    return Overlap(total_commits_a=10, total_commits_b=10,
                   shared_commits=shared_commits,
                   shared_pct_of_upstream=100.0 * shared_commits / 10,
                   shared_pct_of_suspect=100.0 * shared_commits / 10,
                   common_commit=None, common_commit_date=None,
                   distinctive_shared_blobs=distinctive)


def test_confidence_ladder():
    conf, derived, review = confidence_and_flags(_ov(shared_commits=5))
    assert (conf, derived, review) == ("high", True, "")

    conf, derived, review = confidence_and_flags(_ov(distinctive=3))
    assert (conf, derived) == ("medium", True)
    assert "NO shared history" in review

    # One identical file and no shared history is NOT derivation. gitea and
    # synapse share zero commits and one jQuery bundle, and were reported as
    # derived (held-out #3, precision 1.00 -> 0.83).
    conf, derived, review = confidence_and_flags(_ov(distinctive=1))
    assert (conf, derived) == ("none", False)
    assert "too little to call derivation" in review

    assert confidence_and_flags(_ov()) == ("none", False, "")


class _Attr:
    def __init__(self, stripped):
        self.notice_stripped = stripped
        self.upstream_notice_present = not stripped
        self.upstream_authors_present_in_suspect = True
        self.files_compared = 0
        self.files_header_replaced = 0
        self.files_header_dropped = 0

    @property
    def headers_stripped(self):
        return False


class _Lic:
    def __init__(self, spdx):
        self.spdx_id = spdx


def _overlap(shared=5, distinctive=0):
    return Overlap(total_commits_a=10, total_commits_b=10, shared_commits=shared,
                   shared_pct_of_upstream=50.0, shared_pct_of_suspect=50.0,
                   common_commit="abc1234567", common_commit_date="2020-01-01",
                   distinctive_shared_blobs=distinctive)


def test_observations_state_facts_and_never_conclude():
    """The tool reports evidence; the legal call belongs to the reader.

    Guard against re-introducing a verdict: no observation may assert that a
    license was violated, infringed, or that anything is unauthorized.
    """
    from receipts.report import _observe
    obs, questions = _observe(True, _overlap(), _Lic("MIT"), _Lic("GPL-3.0-only"),
                              _Attr(True))
    joined = " ".join(o.text for o in obs).lower()
    for banned in ("violat", "infring", "unauthorized", "illegal", "breach",
                   "not allowed", "must "):
        assert banned not in joined, f"observation reads as a verdict: {banned}"
    # the facts themselves are still stated
    assert any("share" in o.text and "commits" in o.text for o in obs)
    assert any("MIT" in o.text for o in obs)
    assert any("GPL-3.0-only" in o.text for o in obs)
    assert any("copyright holder" in o.text for o in obs)
    # and the legal question is handed to the human, not answered
    assert any("permits distributing" in q for q in questions)


def test_no_derivation_produces_no_license_claims():
    from receipts.report import _observe
    obs, questions = _observe(False, _overlap(shared=0), None, _Lic("MIT"), _Attr(False))
    assert len(obs) == 1 and obs[0].code == "no_derivation"
    assert questions


def test_summarize_prunes_reference_noise():
    """Real ScanCode output is verbose; the headline must stay readable.

    Uses a real-shaped entry so no scancode install is needed.
    """
    from receipts.license import summarize
    entry = {
        "detected_license_expression_spdx": (
            "(LicenseRef-scancode-generic-cla AND SSPL-1.0 AND "
            "LicenseRef-scancode-unknown-license-reference) AND "
            "LicenseRef-scancode-unknown-license-reference AND "
            "LicenseRef-scancode-rsalv2 AND SSPL-1.0"),
        "license_detections": [{"matches": [
            {"score": 99.0, "rule_identifier": "sspl-1.0.RULE",
             "spdx_license_expression": "SSPL-1.0",
             "start_line": 12, "end_line": 340},
        ]}],
    }
    st = summarize(entry)
    # headline: noise references pruned, duplicates collapsed, operator preserved
    # AND: most restrictive first, so the binding term leads. SSPL-1.0 and
    # rsalv2 are both Source-available in ScanCode's own categories, so the
    # named licence takes the tie. Under the hand-written list this
    # replaced, rsalv2 was simply absent, scored "unknown", and jumped the
    # queue — the defect that made `Vim` outrank `Apache-2.0` (held-out #4).
    assert st.spdx_id == "SSPL-1.0 AND LicenseRef-scancode-rsalv2"
    assert st.governing == "SSPL-1.0"
    assert "generic-cla" not in st.spdx_id
    # nothing is hidden: full expression + pruned refs are retained as evidence
    assert "generic-cla" in st.full_expression
    assert any("generic-cla" in x for x in st.also_detected)
    # citable evidence for a DMCA notice
    assert st.evidence and "lines 12-340" in st.evidence[0]
    assert st.detector == "scancode"


def test_summarize_operator_semantics_and_empty():
    from receipts.license import summarize, UNRESOLVED
    # OR with no AND → choice, preserved as OR
    st = summarize({"detected_license_expression_spdx": "MIT OR Apache-2.0",
                    "license_detections": []})
    # OR: the recipient chooses, so order is left as detected.
    assert st.spdx_id == "MIT OR Apache-2.0"
    # The whole choice is the answer. Naming one term states something the file
    # does not: Elasticsearch offers `Elastic-2.0 OR AGPL-3.0-only OR SSPL-1.0`,
    # and reporting AGPL tells a recipient entitled to pick Elastic-2.0 that they
    # are under copyleft.
    assert st.governing == "MIT OR Apache-2.0"
    # nothing detected → unresolved, never a guess
    assert summarize({}).spdx_id == UNRESOLVED


def test_broken_scancode_raises_instead_of_degrading():
    """A broken ScanCode must fail loudly, never yield NOASSERTION.

    Regression guard: when libmagic was missing, scancode crashed, every scan
    returned None and surfaced as NOASSERTION — a *failure* presented to the
    reader as a *finding*. Same defect class as the held-out FPs (BENCHMARK §D.4).
    """
    import subprocess

    class _Crashed:
        returncode = 1
        stdout = ""
        stderr = "typecode.magic2.NoMagicLibError: CRITICAL: libmagic ... not installed"

    # Patched on `receipts.scancode`, where these live: patching the re-export
    # on `receipts.license` would leave the real function reading its own module
    # global and the test would pass without exercising anything.
    from receipts import scancode as sc
    orig_run, orig_path = subprocess.run, sc.scancode_path
    try:
        sc.scancode_path = lambda: "/fake/scancode"          # noqa: E731
        subprocess.run = lambda *a, **k: _Crashed()          # noqa: E731
        sc.scancode_version.cache_clear(); sc._version_stderr.cache_clear()
        raised = False
        try:
            sc._run_scancode("/tmp/x", "/tmp/out.json")
        except sc.ScanCodeUnavailable as exc:
            raised = True
            msg = str(exc)
            # The message must be ACTIONABLE in the terminal, not a stack trace:
            # name the missing piece and give a command to install it.
            assert "libmagic" in msg, msg
            assert ("brew install" in msg or "apt install" in msg
                    or "pip install" in msg), msg
        assert raised, "a crashed ScanCode must raise, not return a soft failure"
    finally:
        subprocess.run, sc.scancode_path = orig_run, orig_path
        sc.scancode_version.cache_clear(); sc._version_stderr.cache_clear()


def test_negligible_shared_history_is_qualified_not_called_a_fork():
    """A two-commit common seed must not be presented as a fork point.

    Regression guard for Emby → Jellyfin (BENCHMARK §E.4): 2 of 15,007 shared
    commits, dated five years before the real fork, were reported as "their
    histories last coincide" with high confidence.
    """
    from receipts.models import Overlap
    from receipts.report import _observe

    def ov(shared, total_up):
        return Overlap(total_commits_a=total_up, total_commits_b=30000,
                       shared_commits=shared,
                       shared_pct_of_upstream=round(100 * shared / total_up, 2),
                       shared_pct_of_suspect=round(100 * shared / 30000, 2),
                       common_commit="abc1234567", common_commit_date="2013-02-15")

    tiny = ov(2, 15007)
    assert tiny.common_history_is_negligible
    # confidence must fall too — existence of shared objects is not enough
    assert confidence_and_flags(tiny)[0] != "high"
    obs, _ = _observe(True, tiny, _Lic("MIT"), _Lic("MIT"), _Attr(False))
    joined = " ".join(o.text for o in obs)
    assert "unlikely to be where the suspect forked" in joined
    assert "2 commits" in joined and "0.01%" in joined      # magnitude is shown
    assert "at that commit" in joined                        # derived claims hedged

    real = ov(3450, 12902)
    assert not real.common_history_is_negligible
    assert confidence_and_flags(real)[0] == "high"
    obs2, _ = _observe(True, real, _Lic("MIT"), _Lic("MIT"), _Attr(False))
    assert not any(o.code == "negligible_shared_history" for o in obs2)


def test_small_header_sample_is_flagged():
    from receipts.models import AttributionDiff
    from receipts.report import _observe
    from receipts.models import Overlap
    o = Overlap(total_commits_a=100, total_commits_b=100, shared_commits=90,
                shared_pct_of_upstream=90.0, shared_pct_of_suspect=90.0,
                common_commit="abc1234567", common_commit_date="2020-01-01")
    small = AttributionDiff(files_compared=3, files_header_dropped=3)
    big = AttributionDiff(files_compared=375, files_header_dropped=315)
    def texts(attr):
        return " ".join(x.text for x in _observe(True, o, _Lic("MIT"), _Lic("MIT"), attr)[0])
    assert "too small to generalize" in texts(small)
    assert "too small to generalize" not in texts(big)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)


def test_restrictiveness_comes_from_scancode_not_a_local_list():
    """Ranking must use ScanCode's curated categories, not an enumeration here.

    The hand-written list this replaced failed the way hand-written lists fail:
    anything missing from it scored "unknown", unknown ranks most restrictive,
    and so an unlisted licence seized the headline. Held-out #4 caught two:
    `Vim` (a real SPDX id, absent from the list) became Neovim's reported
    licence over `Apache-2.0`, and `other-permissive` — whose own name says
    permissive — outranked `Apache-2.0` on Kibana.
    """
    from receipts.license import category_of, restrictiveness

    assert category_of("Vim") == "Copyleft"
    assert category_of("Apache-2.0") == "Permissive"
    assert category_of("LicenseRef-scancode-other-permissive") == "Permissive"
    assert category_of("LicenseRef-scancode-proprietary-license") == "Commercial"

    # A permissive detection never outranks a named permissive licence.
    assert restrictiveness("LicenseRef-scancode-other-permissive") == \
           restrictiveness("Apache-2.0")

    # Commercial outranks everything; unknown still sorts first, deliberately.
    assert restrictiveness("LicenseRef-scancode-proprietary-license") < \
           restrictiveness("GPL-2.0-only") < restrictiveness("MIT")
    assert restrictiveness("NoSuchLicence-9.9") == -1

    # BSL-1.0 is BOOST in SPDX, and is permissive. The Business Source License
    # is BUSL-1.1. Reporting one for the other put "MySQL: BSL-1.0" in a
    # held-out result.
    assert category_of("BSL-1.0") == "Permissive"
    assert category_of("BUSL-1.1") == "Source-available"


def _entry(*matches):
    """A ScanCode-shaped file entry from (start_line, score, length, spdx) tuples."""
    return {
        "detected_license_expression_spdx": " AND ".join(m[3] for m in matches),
        "license_detections": [{"matches": [
            {"start_line": ln, "score": sc, "matched_length": n,
             "spdx_license_expression": ex, "rule_identifier": f"r{i}.RULE"}
            for i, (ln, sc, n, ex) in enumerate(matches)]}],
    }


def test_governing_licence_is_the_first_substantial_grant():
    """A licence file's grant comes first; a third-party inventory follows it.

    MySQL's licence file is the case: the GPL-2.0 grant is at line 18 and the
    enumeration of bundled dependencies begins at line 488. Reading the file for
    its *most restrictive* term returned whichever bundled licence was
    strictest, and MySQL was reported as `BSL-1.0` -- the **Boost** licence.
    """
    from receipts.license import summarize
    st = summarize(_entry(
        (18, 96.2, 330, "GPL-2.0-only WITH LicenseRef-scancode-mysql-linking-exception-2018"),
        (491, 100.0, 12, "BSL-1.0"),          # a bundled Boost licence
        (534, 100.0, 20, "curl"),
    ))
    assert "GPL-2.0" in st.governing
    assert "BSL-1.0" not in st.governing


def test_incidental_mention_is_not_a_grant():
    """A low-confidence one-word match is a mention, not a licence grant.

    Directus's own licence is the "Monospace Sustainable Core License", whose
    abbreviation is `MSCL-1.0-GPL`; ScanCode matches that single token as
    `gpl-1.0-plus` with a score of 50.
    """
    from receipts.license import summarize
    st = summarize(_entry(
        (5, 50.0, 1, "GPL-1.0-or-later"),                            # the abbreviation
        (45, 100.0, 2, "LicenseRef-scancode-proprietary-license"),   # the real grant
    ))
    # `governing` carries one spelling per licence — ScanCode emits the bare
    # key on a match and the LicenseRef form in an expression, and string
    # equality downstream cannot see they are the same licence.
    assert st.governing == "proprietary-license"


def test_licence_with_exception_is_not_pruned_as_an_exception():
    """`X WITH Y-exception` is a licence, not a bare exception reference."""
    from receipts.license import _is_uninformative
    assert not _is_uninformative("GPL-2.0-only WITH Classpath-exception-2.0")
    assert _is_uninformative("LicenseRef-scancode-generic-exception")


def test_proprietary_still_wins_when_it_comes_first():
    """The positional rule must not undo the Mapbox fix."""
    from receipts.license import summarize
    st = summarize(_entry(
        (8, 100.0, 14, "LicenseRef-scancode-proprietary-license"),
        (29, 100.0, 213, "BSD-3-Clause"),
    ))
    # `governing` carries one spelling per licence — ScanCode emits the bare
    # key on a match and the LicenseRef form in an expression, and string
    # equality downstream cannot see they are the same licence.
    assert st.governing == "proprietary-license"


def test_change_license_parameter_is_not_the_grant():
    """A Business Source Licence names the licence it will BECOME, inside itself.

    HashiCorp's Vault carries the BSL, whose parameter block reads

        Change License:  MPL 2.0

    ScanCode matches that with a rule flagged `is_license_tag` -- four words,
    score 100, at line 44 -- while the actual BSL text starts at line 51. Taking
    the first high-scoring detection reported a repository that had left open
    source as `MPL-2.0`: the future licence read as the current one, on the
    single most consequential kind of case this tool exists for (held-out #1).
    """
    from receipts.license import summarize
    st = summarize({
        "detected_license_expression_spdx": "MPL-2.0 AND BUSL-1.1",
        "license_detections": [{"matches": [
            {"start_line": 44, "score": 100.0, "matched_length": 4,
             "spdx_license_expression": "MPL-2.0",
             "rule_identifier": "mpl-2.0_75.RULE"},          # is_license_tag
            {"start_line": 51, "score": 63.7, "matched_length": 335,
             "spdx_license_expression": "BUSL-1.1",
             "rule_identifier": "bsl-1.1_1.RULE"},           # is_license_notice
        ]}]})
    assert "BUSL-1.1" in st.governing
    assert "MPL" not in st.governing


def test_a_reference_can_still_be_the_grant():
    """Excluding references as well as tags was tried, and broke real files.

    Bitwarden's LICENSE.txt reproduces no licence text at all -- it says "Source
    code in this repository is covered by one of two licenses: (i) the GNU AGPL
    v3.0..." -- which ScanCode classifies as a reference. Dropping references
    made the answer fall back to the strictest term in the file.
    """
    from receipts.license import _is_grant_rule
    assert _is_grant_rule("agpl-3.0_309.RULE")        # a reference: kept
    assert not _is_grant_rule("mpl-2.0_75.RULE")      # a tag: excluded
    assert _is_grant_rule(None)                       # unknown: never excluded


def test_licences_are_reported_with_spdx_ids():
    """ScanCode's own keys are not SPDX ids, and one pair is dangerous.

        ScanCode `bsl-1.1`   -> SPDX `BUSL-1.1`   Business Source License
        ScanCode `boost-1.0` -> SPDX `BSL-1.0`    Boost

    A report saying `bsl-1.1` sends a reader looking it up to `BSL-1.0`, the
    opposite kind of licence, one character away. This tool already made that
    exact substitution once, on MySQL.
    """
    from receipts.license import to_spdx
    assert to_spdx("bsl-1.1") == "BUSL-1.1"
    assert to_spdx("boost-1.0") == "BSL-1.0"
    assert to_spdx("bsd-new") == "BSD-3-Clause"
    assert to_spdx("lgpl-2.1-plus") == "LGPL-2.1-or-later"
    # A WITH expression keeps its shape.
    assert to_spdx("gpl-2.0 WITH mysql-linking-exception-2018").startswith(
        "GPL-2.0-only WITH")


def test_exception_compound_inherits_its_licence_category():
    """`X WITH Y` is categorised by X; the compound is not in ScanCode's index.

    MySQL is correct in all three licence cells and still produced three review
    flags saying "ScanCode has no category for GPL-2.0-only WITH ...". Review
    points must name what is actually uncertain -- a caveat on everything is a
    caveat on nothing.
    """
    from receipts.license import category_of, restrictiveness
    assert category_of("GPL-2.0-only") == "Copyleft"
    assert category_of("GPL-2.0-only WITH mysql-linking-exception-2018") == "Copyleft"
    assert restrictiveness("GPL-2.0-only WITH mysql-linking-exception-2018") == \
           restrictiveness("GPL-2.0-only")


def test_a_rejected_token_is_not_an_answer(monkeypatch):
    """A broken credential must not read as a finding.

    Treating every non-200 from Software Heritage as "not archived" meant a
    stale or mistyped token reported an archived repository as unpreserved --
    someone could conclude their code is not backed up and act on it.
    """
    from receipts import trace as tr
    for status, expect in ((401, "UNKNOWN"), (403, "UNKNOWN"), (429, "UNKNOWN"),
                           (0, "UNKNOWN"), (404, "not archived")):
        monkeypatch.setattr(tr, "_swh", lambda p, t="", _s=status: (_s, None))
        assert tr.archive_status("https://example.com/x", "tok").startswith(expect)


def test_manifest_identifies_the_build(tmp_path):
    """`receipts 0.1.0` does not say WHICH build produced the evidence.

    Held-out #6: the Redis -> Valkey package preserves a disputed header that was
    shown false an hour later. Two packages with contradictory findings were
    indistinguishable, because both said only "0.1.0".
    """
    from receipts.snapshot import code_fingerprint
    fp = code_fingerprint()
    assert len(fp) == 64 and fp == code_fingerprint()      # stable within a run


def test_or_choice_is_reported_whole_not_collapsed():
    """A choice of licences is not one licence."""
    from receipts.license import summarize
    st = summarize({"detected_license_expression_spdx":
                    "Elastic-2.0 OR AGPL-3.0-only OR SSPL-1.0",
                    "license_detections": []})
    assert st.governing == "Elastic-2.0 OR AGPL-3.0-only OR SSPL-1.0"
    # AND is the opposite: obligations accumulate, so the most restrictive binds.
    st = summarize({"detected_license_expression_spdx": "MIT AND GPL-3.0-only",
                    "license_detections": []})
    assert st.governing == "GPL-3.0-only"


def test_named_licence_wins_a_tie_against_a_scancode_reference():
    """SSPL-1.0 and RSALv2 are both Source-available, so the category ties.

    The winner was then whichever came first in the expression, which makes the
    headline depend on the order ScanCode happened to emit — the same input
    stating two different licences.
    """
    from receipts.license import summarize
    entry = {"detected_license_expression_spdx":
             "LicenseRef-scancode-rsalv2 AND SSPL-1.0", "license_detections": []}
    assert summarize(entry).governing == "SSPL-1.0"
    entry["detected_license_expression_spdx"] = (
        "SSPL-1.0 AND LicenseRef-scancode-rsalv2")
    assert summarize(entry).governing == "SSPL-1.0"
    # A reference still wins when it is genuinely more restrictive: ScanCode has
    # no short id for what mapbox-gl-js left open source FOR, and dropping the
    # term reported the repository as `BSD-3-Clause AND MIT`.
    entry["detected_license_expression_spdx"] = (
        "LicenseRef-scancode-proprietary-license AND BSD-3-Clause AND MIT")
    assert summarize(entry).governing == "proprietary-license"


def _era_repo(monkeypatch):
    """NONE -> MIT (t1) -> BSD (t2) -> MIT (t3), with a branch off t1 and an orphan.

        r1 - t1 - a1 - t2 - b1 - t3 - m1 - m2      mainline
                   \\
                    x1                             branch that left after t1
        o1                                         unrelated root
    """
    from receipts import overlap as ov
    from receipts.models import LicenseTransition
    desc = {"t1": {"a1", "x1", "t2", "b1", "t3", "m1", "m2"},
            "t2": {"b1", "t3", "m1", "m2"},
            "t3": {"m1", "m2"}}
    monkeypatch.setattr(ov.git, "descendants", lambda repo, c: desc[c])
    monkeypatch.setattr(ov.git, "ancestors", lambda repo, c: {"r1", "t1"})
    return [
        LicenseTransition(date="2014-02-11", commit="t1", before="NONE",
                          after="MIT", file="LICENSE"),
        LicenseTransition(date="2014-03-25", commit="t2", before="MIT",
                          after="BSD-3-Clause", file="LICENSE"),
        LicenseTransition(date="2014-11-05", commit="t3", before="BSD-3-Clause",
                          after="MIT", file="LICENSE"),
    ]


def test_shared_commits_split_by_licence_era(monkeypatch):
    """The licence at a commit is the newest change that commit DESCENDS FROM.

    Asking it the other way round — which commits are ancestors of each change —
    is the same answer only on a strictly linear mainline. It left 58% of what
    gogs shares with gitea unattributed, including the entire final era, which
    is the one the fork was taken under.
    """
    from receipts import overlap as ov
    transitions = _era_repo(monkeypatch)

    eras = ov.shared_by_licence_era("repo", {"r1", "a1", "b1", "m1", "m2"},
                                    transitions)
    assert [(e["licence"], e["commits"]) for e in eras] == [
        ("NONE", 1), ("MIT", 1), ("BSD-3-Clause", 1), ("MIT", 2)]
    # every era after the first cites the commit that opened it
    assert [e["from_date"] for e in eras] == [
        "", "2014-02-11", "2014-03-25", "2014-11-05"]

    # x1 left the mainline after t1 and never saw t2, so it is BSD-era by date
    # and MIT by lineage. Lineage is what the licence file actually says.
    eras = ov.shared_by_licence_era("repo", {"a1", "x1"}, transitions)
    assert [(e["licence"], e["commits"]) for e in eras] == [("MIT", 2)]


def test_commits_outside_every_licence_lineage_claim_nothing(monkeypatch):
    """An orphan root is not an unlicensed commit; the row stays blank."""
    from receipts import overlap as ov
    transitions = _era_repo(monkeypatch)
    eras = ov.shared_by_licence_era("repo", {"m1", "o1"}, transitions)
    assert [(e["licence"], e["commits"]) for e in eras] == [("MIT", 1), ("", 1)]


def test_licence_eras_are_empty_without_transitions():
    from receipts import overlap as ov
    assert ov.shared_by_licence_era("repo", {"c1"}, []) == []
    assert ov.shared_by_licence_era("repo", set(), ["x"]) == []

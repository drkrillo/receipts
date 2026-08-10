"""Report assembly + rendering (human Markdown / JSON) + chain of custody."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import time

from receipts import __version__
from receipts import gitobjects as git
from receipts import license as lic
from receipts import overlap as ov
from receipts.models import Custody, Observation, Overlap, Report

logger = logging.getLogger(__name__)


def overlap_only(a: str, b: str):
    """(Overlap, confidence, derived, review, anchor, basis, anchor_gap_days)."""
    shared, tot_a, tot_b = ov.commit_overlap(a, b)
    div_sha, div_date = ov.most_recent_common_commit(b, shared)
    shared_blobs, tot_blobs_b, distinctive, paths = ov.blob_overlap(a, b)
    overlap = Overlap(
        total_commits_a=tot_a, total_commits_b=tot_b, shared_commits=len(shared),
        shared_on_compared_line=len(shared & git.ancestors(b, "HEAD")),
        shared_pct_of_upstream=round(100 * len(shared) / tot_a, 2) if tot_a else 0.0,
        shared_pct_of_suspect=round(100 * len(shared) / tot_b, 2) if tot_b else 0.0,
        common_commit=div_sha, common_commit_date=div_date,
        total_blobs_b=tot_blobs_b, shared_blobs=shared_blobs,
        distinctive_shared_blobs=distinctive, shared_blob_paths=paths[:50],
    )
    confidence, derived, review = ov.confidence_and_flags(overlap)
    anchor, basis = ov.anchor_commit(a, b, div_sha)
    gap = ov.anchor_gap_days(a, anchor, div_date)
    return overlap, confidence, derived, review, anchor, basis, gap


def provenance(upstream_src: str, suspect_src: str, workdir: str,
               suspect_at: str = "HEAD") -> Report:
    """Full v0.1 provenance analysis of *suspect* against *upstream*."""
    t_start = time.monotonic()
    logger.info("step 1/5: preparing clones")
    a = git.prepare(upstream_src, workdir)   # standalone clones (invariant)
    b = git.prepare(suspect_src, workdir)

    logger.info("step 2/5: comparing git objects")
    shared, tot_a, tot_b = ov.commit_overlap(a, b)
    div_sha, div_date = ov.most_recent_common_commit(b, shared)
    if div_sha:
        logger.info("most recent common commit: %s (%s)", div_sha[:10], (div_date or "")[:10])
    shared_blobs, tot_blobs_b, distinctive, paths = ov.blob_overlap(a, b)
    on_line = shared & git.ancestors(b, "HEAD")

    overlap = Overlap(
        total_commits_a=tot_a, total_commits_b=tot_b,
        shared_commits=len(shared), shared_on_compared_line=len(on_line),
        shared_pct_of_upstream=round(100 * len(shared) / tot_a, 2) if tot_a else 0.0,
        shared_pct_of_suspect=round(100 * len(shared) / tot_b, 2) if tot_b else 0.0,
        common_commit=div_sha, common_commit_date=div_date,
        total_blobs_b=tot_blobs_b, shared_blobs=shared_blobs,
        distinctive_shared_blobs=distinctive, shared_blob_paths=paths[:50],
    )
    confidence, derivation_likely, derivation_review = ov.confidence_and_flags(overlap)
    logger.info("derivation=%s (confidence: %s)", derivation_likely, confidence)

    # Everything about the upstream is read at this anchor — its state when the
    # suspect diverged — never at its current HEAD (see overlap.anchor_commit).
    anchor, anchor_basis = ov.anchor_commit(a, b, div_sha)
    logger.info("temporal anchor: %s (%s)", (anchor or "none")[:10], anchor_basis)

    logger.info("step 3/5: detecting licenses")
    lic_a_now = lic.find_license(a)
    lic_b_now = lic.find_license(b)
    # BUG FIX: only ask what the upstream was "at divergence" when the two
    # repositories actually diverged. Held-out #5 reported `Apache-2.0` at the
    # divergence of grafana -> metabase, which share zero commits: the value came
    # from the date fallback, meant nothing, and read as a fact.
    lic_a_div = lic.find_license(a, anchor) if (anchor and derivation_likely) else None
    # Reference upstream license: at the divergence commit if we have one,
    # else fall back to upstream's current license (derivation found via blobs).
    ref_lic = lic_a_div or lic_a_now
    # "differs" is only meaningful under derivation AND when both sides are
    # resolved — a NOASSERTION (multi-license/unrecognized) can't be compared.
    resolved = "NOASSERTION" not in (ref_lic.spdx_id, lic_b_now.spdx_id)
    raw_differs = resolved and ref_lic.spdx_id != lic_b_now.spdx_id
    license_differs = derivation_likely and raw_differs

    logger.info("step 4/5: comparing attribution")
    # Compare against the upstream license AS IT WAS AT DIVERGENCE — its HEAD may
    # carry a later license whose text names a different author entirely.
    attribution = ov.attribution_diff(
        a, b, lic.license_text(a, lic_a_div) if lic_a_div else None,
        suspect_license_text=lic.license_text(b, lic_b_now),
        upstream_commit=anchor or None)
    # Notices are compared only on blobs whose id is identical in both
    # repositories, so the compared side provably held those exact bytes.
    examined, unattributed = 0, []

    # Only what the compared repository's OWN line of history reaches. Objects
    # in a branch nobody builds from are still objects it holds, but they are
    # not what its code was taken from — and counting them put 1,606 commits
    # that HashiCorp made after relicensing Vault into openbao's era table.
    licence_eras = ov.shared_by_licence_era(a, on_line, lic.timeline(a)) \
        if derivation_likely else []

    roots = git.root_identities(a)
    observations, open_questions = _observe(
        derivation_likely, overlap, lic_a_div or ref_lic, lic_b_now, attribution)
    if derivation_likely:
        open_questions.append(
            "Whether notices survived on files that were edited after copying. "
            "Only byte-identical files are compared here; a file whose contents "
            "changed is outside what git object identity can answer.")

    summary = _summarize(upstream_src, suspect_src, overlap, lic_a_div,
                         lic_b_now, attribution, confidence, derivation_likely)

    custody = Custody(
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        tool_version=__version__, git_version=git.git_version(),
        upstream_ref=upstream_src, suspect_ref=suspect_src,
        upstream_head=git.head(a), suspect_head=git.head(b),
        license_detector=lic.detector_name(),
        license_detector_version=lic.scancode_version(),
        anchor_commit=anchor, anchor_basis=anchor_basis,
    )
    report = Report(
        upstream=upstream_src, suspect=suspect_src, overlap=overlap,
        upstream_license_at_divergence=lic_a_div, upstream_license_now=lic_a_now,
        licence_eras=licence_eras,
        suspect_license_now=lic_b_now, attribution=attribution,
        confidence=confidence, derivation_likely=derivation_likely,
        derivation_needs_review=derivation_review,
        license_differs_from_upstream=license_differs,
        observations=observations, open_questions=open_questions,
        summary=summary, custody=custody,
        upstream_roots=roots,
        unattributed=[{"path": u.path, "upstream_path": u.upstream_path,
                       "shared_lines": u.shared_lines, "notice": u.notice,
                       "sample": list(u.sample)} for u in unattributed],
        unattributed_examined=examined,
    )
    logger.info("step 5/5: sealing evidence")
    report.custody.evidence_sha256 = _evidence_hash(report)
    logger.info("%d observation(s) | analysis completed in %.1fs",
                len(observations), time.monotonic() - t_start)
    return report


def _observe(derivation_likely, overlap, lic_div, lic_now, attr):  # noqa: C901
    """Facts worth stating, and what a human still has to decide."""
    obs: list[Observation] = []
    q: list[str] = []
    o = overlap

    if derivation_likely and overlap.shared_commits:
        q.append(
            "Whether the suspect took this code from the upstream, or both took "
            "it from a third project. Shared history is SYMMETRIC: from two "
            "repositories alone, 'B forked from A', 'A forked from B' and 'both "
            "forked from C' produce the same evidence. MariaDB and Percona "
            "Server share 74,264 commits and neither derives from the other — "
            "both fork MySQL. Establishing direction needs the third repository, "
            "or the projects' own public record.")

    if not derivation_likely:
        obs.append(Observation("no_derivation", 
            f"No shared git objects found ({o.shared_commits} shared commits, "
            f"{o.distinctive_shared_blobs} distinctive shared files)."))
        q.append("Whether code was copied in a way this method cannot see "
                 "(rewritten history AND edited files, or a reimplementation).")
        return obs, q

    if o.shared_commits:
        obs.append(Observation("shared_commits", f"The two repositories share {o.shared_commits:,} commits — "
                   f"{o.shared_pct_of_upstream}% of the upstream's history "
                   f"({o.total_commits_a:,} commits) and "
                   f"{o.shared_pct_of_suspect}% of the suspect's "
                   f"({o.total_commits_b:,})."))
    if o.distinctive_shared_blobs:
        obs.append(Observation("distinctive_blobs", f"{o.distinctive_shared_blobs:,} distinctive files are "
                   f"byte-identical between the two repositories."))
    if o.common_commit:
        obs.append(Observation("common_commit", f"The most recent commit both contain is {o.common_commit[:10]} "
                   f"({(o.common_commit_date or '')[:10]})."))
    if o.common_history_is_negligible:
        # The qualification the Emby → Jellyfin case showed was missing: a fork
        # inherits the upstream's history, so a sliver of shared commits points at
        # an old common ancestor, and everything dated from it inherits that doubt.
        obs.append(Observation("negligible_shared_history", f"That shared history is only {o.shared_commits:,} commits "
                   f"({o.shared_pct_of_upstream}%), so this common commit is "
                   f"unlikely to be where the suspect forked; the license and "
                   f"attribution findings below describe that commit, not "
                   f"necessarily the point at which code was taken."))

    up = lic_div.spdx_id if lic_div else "NONE"
    now = lic_now.spdx_id if lic_now else "NONE"
    at = ("at that commit" if o.common_history_is_negligible
          else "at the point their histories diverge")
    if up == "NONE":
        obs.append(Observation("upstream_no_license", f"No license text was found in the upstream {at}."))
        q.append("Whether the upstream granted permission outside the repository "
                 "(a private agreement, a later license, an employer's terms).")
    elif up == "NOASSERTION":
        obs.append(Observation("upstream_license_unresolved", f"The upstream's license {at} could not be resolved to a single "
                   "identifier (multiple licenses, or unrecognized text)."))
        q.append("What the upstream's terms actually were at that point — read the "
                 "license file at the divergence commit directly.")
    else:
        obs.append(Observation("upstream_license", f"The upstream carried {up} {at}."))
    obs.append(Observation("suspect_license", "The suspect carries "
               + ("no license text." if now == "NONE" else f"{now} today.")))
    if up not in ("NONE", "NOASSERTION") and now not in ("NONE", "NOASSERTION"):
        obs.append(Observation("license_comparison", "The two licenses "
                   + ("differ." if up != now else "are the same.")))
        if up != now:
            q.append(f"Whether {up} permits distributing a derivative under {now}.")

    if attr.notice_stripped:
        obs.append(Observation("notice_stripped", "No copyright holder from the upstream's license appears in the "
                   "suspect's."))
        q.append("Whether the upstream's license required retaining that notice "
                 "(most do, including MIT and Apache-2.0 §4).")
    elif attr.upstream_notice_present:
        obs.append(Observation("notice_retained", "At least one upstream copyright holder still appears in the "
                   "suspect's license."))
    if attr.files_header_replaced or attr.files_header_dropped:
        n = attr.files_header_replaced + attr.files_header_dropped
        how = []
        if attr.files_header_replaced:
            how.append(f"{attr.files_header_replaced} replaced with another holder")
        if attr.files_header_dropped:
            how.append(f"{attr.files_header_dropped} removed entirely")
        small = " — a sample too small to generalize from" if attr.files_compared < 10 else ""
        obs.append(Observation("headers_changed", f"Of {attr.files_compared} shared source files carrying an "
                   f"upstream copyright header, {n} no longer do "
                   f"({', '.join(how)}){small}."))
        q.append("Whether those per-file notices were required by the license, and "
                 "whether their removal was deliberate.")
    elif attr.files_compared:
        obs.append(Observation("headers_intact", f"All {attr.files_compared} shared source files with an upstream "
                   f"copyright header still carry it."))

    q.append("Who holds copyright in the upstream code, and whether they authorized "
             "this use.")
    return obs, q


def _summarize(a, b, o, lic_div, lic_now, attr, conf, likely) -> str:
    """One line of counts. Symmetric, because the evidence is.

    This opened with "Code from {a} is present in {b}", which names a direction
    that the same report's own reviewer questions say two repositories cannot
    establish: `B forked from A`, `A forked from B` and `both forked from C`
    leave identical objects behind.
    """
    if not likely:
        return (f"{a} and {b} share {o.shared_commits} commits and "
                f"{o.distinctive_shared_blobs} distinctive files.")
    parts = []
    if o.shared_commits:
        parts.append(f"{a} and {b} hold {o.shared_commits} of {a}'s "
                     f"{o.total_commits_a} commits in common "
                     f"({o.shared_pct_of_upstream}%)")
    elif o.distinctive_shared_blobs:
        parts.append(f"{a} and {b} hold {o.distinctive_shared_blobs} "
                     f"byte-identical distinctive files and no commits in common")
    if o.common_commit_date:
        label = ("newest commit in both" if o.common_history_is_negligible
                 else "the histories separate at")
        parts.append(f"{label} {o.common_commit[:10]} ({o.common_commit_date[:10]})")
    if lic_div and lic_now:
        parts.append(f"licence at that commit: {lic_div.governing or lic_div.spdx_id}; "
                     f"{b} now: {lic_now.governing or lic_now.spdx_id}")
    if attr.notice_stripped:
        parts.append(f"no copyright holder of {a} appears in {b}'s licence file")
    return ". ".join(parts) + "."


def _evidence_hash(report: Report) -> str:
    payload = {
        "upstream_head": report.custody.upstream_head,
        "suspect_head": report.custody.suspect_head,
        "shared_commits": report.overlap.shared_commits,
        "shared_blob_paths": report.overlap.shared_blob_paths,
        "tool_version": report.custody.tool_version,
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #

def render_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def render_markdown(report: Report) -> str:
    o, c = report.overlap, report.custody
    L = []
    L.append("# RECEIPTS — provenance report\n")
    L.append(f"`receipts v{c.tool_version}` · method: commit-SHA + license · "
             f"evidence SHA-256 `{c.evidence_sha256[:16]}…`\n")
    L.append(f"- **Upstream (origin):** {report.upstream}")
    L.append(f"- **Suspect:** {report.suspect}\n")
    # Said once, here — the same banner the terminal shows. It used to appear in
    # the summary heading, in the observations heading and twice under Limits.
    L.append("> **Evidence, not a verdict.** These are facts about git objects "
             "and licence files; whether they amount to infringement is a human "
             "judgement. Permission granted outside the repository is not "
             "visible here.\n")
    L.append(f"## Summary\n{report.summary}\n")

    L.append("## Hard evidence (identical git objects — not similarity)")
    L.append(f"- {o.shared_commits}/{o.total_commits_a} shared commit SHAs "
             f"({o.shared_pct_of_upstream}% of upstream, "
             f"{o.shared_pct_of_suspect}% of suspect)")
    L.append(f"- {o.distinctive_shared_blobs} distinctive shared files "
             f"(of {o.shared_blobs} shared blobs; boilerplate down-weighted)")
    if o.common_commit:
        L.append(f"- Most recent common commit: `{o.common_commit[:10]}` "
                 f"({(o.common_commit_date or '')[:10]})")
        L.append(f"    - on the upstream's mainline: "
                 f"{'yes' if c.anchor_commit == o.common_commit else 'no'}")
    L.append("- Root commit(s) of upstream on HEAD's line (an ownership signal, "
             "not proof of title):")
    for r in report.upstream_roots:
        L.append(f"    - `{r['commit'][:10]}` ({r['date'][:10]}) author: {r['author']}"
                 + (f" · committer: {r['committer']}" if r['committer'] != r['author'] else ""))
    L.append("")
    # The document has room to say how the reading was taken; the terminal does
    # not, and it is the one derived decision in an otherwise hash-only section.
    if c.anchor_commit:
        L.append("### Where the upstream was read")
        L.append(f"Every upstream-side reading below is taken at "
                 f"`{c.anchor_commit}` — {c.anchor_basis}.\n")
        L.append("```\ngit -C <upstream> merge-base --is-ancestor "
                 f"{c.anchor_commit[:10]} HEAD ; echo $?\n```\n")

    L.append("## License state")
    L.append("_Headline expressions are ScanCode detections with reference-only "
             "matches (CLA text, exceptions, unknown references) pruned; the "
             "verbatim expression of record is below each. An `AND` means both "
             "license texts are **present in the file**, which is not necessarily "
             "the same as both applying._\n")
    div = report.upstream_license_at_divergence
    for label, st in (("upstream @ divergence", div),
                      ("upstream now", report.upstream_license_now),
                      ("suspect now", report.suspect_license_now)):
        if st is None:
            L.append(f"- **{label}:** n/a")
            continue
        # The grant, which is what the terminal and `receipts license` report.
        # This said `spdx_id` instead, so report.txt and report.md in the same
        # evidence package named different licences for the same file: redis
        # came out `AGPL-3.0-only` in one and a nine-term `AND` in the other.
        L.append(f"- **{label}:** {lic.human_label(st.governing or st.spdx_id)}"
                 + (f" _(confidence {st.confidence})_" if st.confidence else ""))
        if st.governing and st.spdx_id != st.governing:
            L.append(f"    - all detected: `{st.spdx_id}`")
        if st.source_file:
            L.append(f"    - file: `{st.source_file}`"
                     + (f" @ `{st.commit[:10]}`" if st.commit else ""))
        if st.full_expression and st.full_expression != st.spdx_id:
            L.append(f"    - full ScanCode expression: `{st.full_expression}`")
        if st.also_detected:
            L.append(f"    - also detected (reference-only): "
                     f"{', '.join(st.also_detected[:6])}")
        for ev in st.evidence[:3]:
            L.append(f"    - matched: {ev}")
    if report.derivation_likely:
        L.append(f"- license differs from upstream (at divergence): "
                 f"{'yes' if report.license_differs_from_upstream else 'no'}\n")
    else:
        L.append("- license differs from upstream: n/a (no derivation established)\n")

    if report.licence_eras:
        L.append("Shared commits by the licence in force when each was made:\n")
        L.append("| commits | licence | in force from |")
        L.append("|---:|---|---|")
        for e in report.licence_eras:
            where = (f"{e['from_date']} `{e['from_commit'][:10]}`"
                     if e["from_commit"] else "the start of history")
            L.append(f"| {e['commits']:,} | "
                     + (lic.human_label(e["licence"]) if e["licence"]
                        else "_no licence-file commit in this commit's ancestry_")
                     + f" | {where if e['licence'] else '—'} |")
        L.append("")

    a = report.attribution
    L.append("## Attribution")
    L.append(f"- upstream authors present in suspect: {a.upstream_authors_present_in_suspect}")
    L.append(f"- copyright/notice present — upstream: {a.upstream_notice_present}, "
             f"suspect: {a.suspect_notice_present}")
    L.append(f"- LICENSE-level notice stripped: {a.notice_stripped}")
    L.append(f"- per-file headers on shared source files: {a.files_compared} compared, "
             f"**{a.files_header_replaced} replaced**, **{a.files_header_dropped} dropped**")
    for ex in a.header_examples:
        L.append(f"    - {ex}")
    L.append("")

    L.append("## Observations")
    for x in report.observations:
        L.append(f"- {x.text}")
    L.append("")
    L.append("## For a reviewer to determine")
    for x in report.open_questions:
        L.append(f"- {x}")
    L.append("")

    from receipts.terminal import review_points
    checks = review_points(report)
    if checks:
        L.append("## Check these first")
        L.append("Findings of a shape this tool has been wrong about before — a "
                 "ranking of fragility, not of importance.\n")
        for label, why in checks:
            L.append(f"- **{label}** — {why}")
        L.append("")

    L.append("## Chain of custody")
    L.append(f"- generated: {c.generated_at}")
    L.append(f"- tool: receipts v{c.tool_version} · {c.git_version}")
    L.append(f"- license detector: {c.license_detector} "
             f"{c.license_detector_version}".rstrip())
    L.append(f"- upstream HEAD: `{c.upstream_head}`")
    L.append(f"- suspect HEAD: `{c.suspect_head}`")
    L.append(f"- evidence SHA-256: `{c.evidence_sha256}`\n")

    # What this document cannot answer. A version number and a roadmap ("v0.2")
    # stood here instead, which tells a reader nothing about the case in front
    # of them.
    L.append("## What is outside this evidence")
    L.append("- Two repositories holding the same objects does not say which "
             "copied which, or whether both took them from a third.")
    L.append("- Only byte-identical files are compared. A file edited after "
             "being copied is outside what git object identity can answer.")
    L.append("- Permission granted outside the repository — a written licence, "
             "an assignment, a purchase — leaves no trace here.")
    return "\n".join(L)

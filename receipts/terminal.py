"""Terminal rendering — plain text, optional ANSI colour."""

from __future__ import annotations

import os
import shutil
import sys

from receipts import license as lic
from receipts.models import Report

_RESET = "\033[0m"
_CODES = {
    "bold": "1", "dim": "2", "red": "31", "green": "32",
    "yellow": "33", "blue": "34", "cyan": "36",
}


def colour_enabled(stream=None, force: bool | None = None) -> bool:
    if force is not None:
        return force
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class Style:
    """Colour helpers that degrade to identity when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, name: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{_CODES[name]}m{text}{_RESET}"

    def __getattr__(self, name: str):
        if name not in _CODES:
            raise AttributeError(name)
        return lambda text: self._wrap(name, text)


def _rule(width: int, char: str = "─") -> str:
    return char * width


def _term_width(default: int = 80) -> int:
    try:
        return min(shutil.get_terminal_size((default, 24)).columns, 100)
    except Exception:
        return default


def _short(ref: str, limit: int = 46) -> str:
    """Readable label for a repo reference."""
    ref = ref.rstrip("/")
    if ref.startswith(("http://", "https://", "git@")):
        parts = ref.replace(":", "/").split("/")
        tail = "/".join(p for p in parts[-2:] if p)
        return tail.removesuffix(".git") or ref
    parts = [p for p in ref.split(os.sep) if p]
    label = "/".join(parts[-2:]) if len(parts) > 1 else (parts[-1] if parts else ref)
    return label if len(label) <= limit else "…" + label[-limit:]


def review_points(report: Report) -> list[tuple[str, str]]:
    """(finding, why) for the parts of this report that are least trustworthy."""
    out: list[tuple[str, str]] = []
    if report.derivation_needs_review:
        out.append(("derivation", report.derivation_needs_review))
    for label, state in (("licence at divergence", report.upstream_license_at_divergence),
                         ("upstream licence now", report.upstream_license_now),
                         ("suspect licence now", report.suspect_license_now)):
        if state is not None and state.needs_review:
            out.append((label, state.needs_review))
    if report.attribution.headers_stripped or getattr(report, "unattributed", None):
        out.append(("attribution",
                    "the attribution checks have never faced a held-out set; treat "
                    "each file as a lead and read the header yourself before "
                    "repeating it anywhere"))
    return out


def render_report(report: Report, colour: bool | None = None) -> str:
    """Terminal-native rendering of a provenance report."""
    st = Style(colour_enabled(force=colour))
    w = _term_width()
    o = report.overlap
    up, sus = _short(report.upstream), _short(report.suspect)
    L: list[str] = []

    def head(title: str) -> None:
        L.append("")
        L.append(st.bold(title.upper()))
        L.append(st.dim(_rule(min(w, 72))))

    def kv(key: str, value: str, indent: int = 2) -> None:
        # Pad to the column, or past it when the key is longer — the slice this
        # replaces could only ever shorten, so a key over 28 chars ran straight
        # into its value ("compared now vs divergencesame").
        L.append(" " * indent + key.ljust(max(28, len(key) + 2)) + value)

    L.append(st.bold("RECEIPTS") + st.dim(f"  provenance report · v{report.custody.tool_version}"))
    L.append(st.dim(_rule(min(w, 72), "═")))
    kv("origin", st.cyan(up), indent=0)
    kv("compared", st.cyan(sus), indent=0)

    head("git objects")
    kv("commits in both", f"{o.shared_commits:,}"
       + st.dim(f"  of {o.total_commits_a:,} in origin, {o.total_commits_b:,} in compared"))
    if o.shared_on_compared_line and o.shared_on_compared_line != o.shared_commits:
        kv("  of those, on compared's line", f"{o.shared_on_compared_line:,}")
        L.append(st.dim("      the rest sit in refs its HEAD does not reach"))
    kv("blobs in both", f"{o.shared_blobs:,}")
    if o.common_commit:
        kv("newest commit in both", f"{o.common_commit[:10]}  "
           + st.dim((o.common_commit_date or "")[:10]))
    if report.upstream_roots:
        L.append("  origin root commit(s) on HEAD's line:")
        for r in report.upstream_roots:
            L.append(f"      {r['commit'][:10]}  {r['date'][:10]}  {r['author']}")
            if r["committer"] != r["author"]:
                L.append(st.dim(f"                              committer: {r['committer']}"))
    L.append("")
    L.append(st.dim("  derived, not a hash comparison — see evidence/excluded.txt"))
    kv("blobs after filtration", f"{o.distinctive_shared_blobs:,}")

    head("license state")
    # The divergence reading is the tool's whole differentiator and it used to
    # ship without a citation: a licence name, no commit, no basis. Both are on
    # the Custody record already; not printing them made the one derived choice
    # in this section — WHICH commit stands for "at divergence" — invisible.
    if report.upstream_license_at_divergence is not None and report.custody.anchor_commit:
        kv("read at", report.custody.anchor_commit[:10])
        for line in _wrap(report.custody.anchor_basis, w - 32):
            L.append(st.dim(" " * 30 + line))
    for label, state in (("origin at divergence", report.upstream_license_at_divergence),
                         ("origin now", report.upstream_license_now),
                         ("compared now", report.suspect_license_now)):
        if state is None:
            kv(label, st.dim("n/a"))
            continue
        conf = st.dim(f"  [{state.confidence}]") if state.confidence else ""
        kv(label, lic.human_label(state.governing or state.spdx_id) + conf)
        if state.source_file:
            L.append(st.dim(f"      file: {state.source_file}"))
        if state.spdx_id and state.spdx_id != state.governing:
            for line in _wrap("all detected: " + state.spdx_id, w - 8):
                L.append(st.dim("      " + line))
        for ev in state.evidence[:2]:
            L.append(st.dim(f"      matched: {ev}"))
    # Three licences are listed above and this compares two of them. Which two
    # was left to the reader; "compared now" and "origin now" are the pair a
    # reader reaches for first, and they are not the pair.
    if report.upstream_license_at_divergence is not None:
        kv("compared now vs divergence",
           "differ" if report.license_differs_from_upstream else "same")

    if report.licence_eras:
        L.append("")
        L.append("  shared commits by the licence in force when each was made:")
        for e in report.licence_eras:
            n = f"{e['commits']:,}"
            if not e["licence"]:
                L.append(f"      {n:>9}  " + st.dim(
                    "no licence-file commit in this commit's ancestry "
                    "— nothing follows about the licence"))
                continue
            label = lic.human_label(e["licence"])
            since = (f"  from {e['from_date']}  {e['from_commit'][:10]}"
                     if e["from_commit"] else "  from the start of history")
            for i, line in enumerate(_wrap(label, w - 24)):
                L.append(f"      {n:>9}  {line}" if i == 0 else " " * 17 + line)
            L.append(st.dim(" " * 17 + since.strip()))

    a = report.attribution
    head("notices on files held by both")
    L.append(st.dim("  Files whose blob id is identical in both repositories,\n"
                    "  so the compared repository provably held these bytes."))
    kv("origin authors named", "yes" if a.upstream_authors_present_in_suspect else "no")
    kv("LICENSE notice retained", st.red("no") if a.notice_stripped else "yes")
    kv("per-file headers", f"{a.files_compared} compared, "
       + (st.red(f"{a.files_header_replaced} replaced") if a.files_header_replaced
          else "0 replaced") + ", "
       + (st.red(f"{a.files_header_dropped} dropped") if a.files_header_dropped
          else "0 dropped"))
    for ex in a.header_examples[:3]:
        for line in _wrap(ex, w - 8):
            L.append("      " + line)

    head("for a reviewer to determine")
    for x in report.open_questions:
        for i, line in enumerate(_wrap(x, w - 6)):
            L.append(("  ? " if i == 0 else "    ") + line)

    # Everything in this report needs human judgement; that is said once at the
    # top. This section is narrower and therefore useful: the specific findings
    # that are of a shape the tool has measurably got wrong before. A reader with
    # limited time should spend it here.
    checks = review_points(report)
    if checks:
        head("check these first")
        for label, why in checks:
            L.append(st.yellow(f"  ! {label}"))
            for line in _wrap(why, w - 8):
                L.append(f"      {line}")

    c = report.custody
    head("chain of custody")
    kv("generated", c.generated_at)
    kv("tool", f"receipts {c.tool_version} · {c.git_version}")
    ver = c.license_detector_version.replace("ScanCode version:", "").strip()
    kv("detector", f"{c.license_detector} {ver}".strip())
    kv("upstream HEAD", c.upstream_head)
    kv("suspect HEAD", c.suspect_head)
    kv("evidence sha256", c.evidence_sha256)

    L.append("")
    return "\n".join(L)


def render_licdrift(ref: str, transitions, orphaned=(),
                    colour: bool | None = None) -> str:
    st = Style(colour_enabled(force=colour))
    w = _term_width()
    L = [st.bold("RECEIPTS") + st.dim("  license timeline"),
         st.dim(_rule(min(w, 72), "═")),
         f"repository   {st.cyan(_short(ref))}", ""]
    if not transitions:
        L.append("  No license file found anywhere in this history.")
        L.append(st.dim("  (No license granted, or the project stores it unusually.)"))
    else:
        L.append(st.bold(f"  {'DATE':<12}{'COMMIT':<12}{'CHANGE'}"))
        L.append(st.dim("  " + _rule(min(w, 70))))
        for t in transitions:
            arrow = st.dim("→")
            # Headline the operative grant, the same value `license` and
            # `provenance` report. The whole detected expression follows as
            # evidence when it says more than the grant does.
            before = t.before_governing or t.before
            after = t.after_governing or t.after
            # `X → X` under a column headed CHANGE reads as a contradiction. The
            # file did change — a bundled dependency's licence came or went —
            # and the grant did not. Say which.
            change = (f"{lic.human_label(before)} {arrow} "
                      f"{st.bold(lic.human_label(after))}" if before != after
                      else st.dim(f"{lic.human_label(after)} — grant unchanged, "
                                  "licence file edited"))
            L.append(f"  {t.date[:10]:<12}{t.commit[:10]:<12}{change}")
            L.append(st.dim(f"  {'':<24}in {t.file}"))
            if t.after != after:
                # The headline is a PICK among the terms detected, and saying so
                # is the difference between a fact and a claim. ScanCode reads
                # part of HashiCorp's BUSL text as `acter-psl-1.0`, which it
                # categorises as more restrictive than BUSL, so the headline for
                # that commit is a licence HashiCorp never adopted.
                detail = f"most restrictive of {len(t.after.split(' AND '))} " \
                         f"detected: {t.after}"
                for line in _wrap(detail, w - 28):
                    L.append(st.dim(" " * 26 + line))
    L.append("")
    if orphaned:
        # The loudest thing this view can say, so it is styled as such. The
        # timeline above walks the mainline; a project that re-roots its history
        # leaves licence grants reachable only from abandoned refs, and reading
        # the mainline alone reports the wrong licence with full confidence.
        # Measured at scale by Rapaport et al., "Altered Histories in Version
        # Control System Repositories" (2025): 1.22 M of 111 M repositories
        # carry rewritten history, and altered histories recurrently change
        # licences retroactively.
        L.append(st.yellow(st.bold("  ⚠  LICENCE STATE NOT ON THE MAINLINE")))
        L.append(st.yellow("     These commits are unreachable from HEAD, and the licence"))
        L.append(st.yellow("     file at each detects as a state the timeline above never"))
        L.append(st.yellow("     records. Whether anyone received the code under it is not"))
        L.append(st.yellow("     something the history can answer."))
        L.append("")
        for commit, date, spdx in orphaned:
            L.append(f"       {date[:10]:<12}{commit[:10]:<12}{st.bold(spdx)}")
        L.append("")
        L.append(st.dim("     verify:  git log --all --oneline -- LICENSE"))
        L.append(st.dim("              git merge-base --is-ancestor <commit> HEAD  # non-zero"))
        L.append("")
    return "\n".join(L)


def _wrap(text: str, width: int) -> list[str]:
    """Wrap without mangling identifiers."""
    import textwrap
    return textwrap.wrap(text, max(width, 30), break_on_hyphens=False,
                         break_long_words=False) or [""]


def render_overlap(upstream: str, suspect: str, ov, confidence: str, derived: bool,
                   review: str, anchor: str, basis: str, gap_days: int,
                   colour: bool | None = None) -> str:
    """`receipts overlap` — git object identity. Every line is a hash comparison."""
    st = Style(colour_enabled(force=colour))
    w = _term_width()
    L = [st.bold("RECEIPTS") + st.dim("  git objects"),
         st.dim(_rule(min(w, 72), "═")),
         f"origin     {st.cyan(_short(upstream))}",
         f"compared   {st.cyan(_short(suspect))}",
         ""]

    L.append(f"  commits in both           {ov.shared_commits:,}"
             + st.dim(f"  of {ov.total_commits_a:,} in origin"))
    if ov.shared_on_compared_line and ov.shared_on_compared_line != ov.shared_commits:
        L.append(f"    on compared's own line  {ov.shared_on_compared_line:,}"
                 + st.dim("  the rest sit in refs its HEAD does not reach"))
    L.append(f"  blobs in both             {ov.shared_blobs:,}")
    if ov.common_commit:
        L.append(f"  newest commit in both     {ov.common_commit[:10]}"
                 + st.dim(f"  {(ov.common_commit_date or '')[:10]}"))
        # `anchor` being set means AN anchor was found, not that THIS commit is
        # the anchor. Reporting the first as the second told the reader that
        # mapbox-gl-js's newest shared commit was on the mainline while the next
        # line named a different commit as the newest one on the mainline.
        L.append(f"    on origin's main line   "
                 f"{'yes' if anchor == ov.common_commit else 'no'}")
    # Every upstream-side reading is taken here, so it is the one commit the
    # reader most needs, and `basis` is the only thing that says what it means.
    if anchor:
        L.append(f"  upstream read at          {anchor[:10]}")
        for line in _wrap(basis, w - 30):
            L.append(st.dim(" " * 28 + line))
    else:
        L.append(f"  upstream read at          {st.dim(basis or 'n/a')}")
    if gap_days > 365:
        L.append(f"  gap to that commit        {gap_days // 365}y {gap_days % 365}d")

    L.append("")
    L.append(f"  {st.bold('derived')}, not a hash comparison — the process is in the evidence package")
    L.append(f"    blobs after filtration  {ov.distinctive_shared_blobs:,}"
             + st.dim("  vendored, generated and third-party removed"))

    if ov.common_commit or anchor:
        L.append("")
        L.append(st.dim("  verify"))
    if ov.common_commit:
        L.append(st.dim(f"    git -C <origin> merge-base --is-ancestor "
                        f"{ov.common_commit[:10]} HEAD ; echo $?"))
        L.append(st.dim(f"    git -C <compared> cat-file -t {ov.common_commit[:10]}"))
    if anchor and anchor != ov.common_commit:
        L.append(st.dim(f"    git -C <origin> merge-base --is-ancestor "
                        f"{anchor[:10]} HEAD ; echo $?"))
    L.append("")
    return "\n".join(L)


def render_license(ref: str, at: str, state, colour: bool | None = None) -> str:
    """`receipts license` — one repository, one revision."""
    st = Style(colour_enabled(force=colour))
    w = _term_width()
    L = [st.bold("RECEIPTS") + st.dim("  licence at one revision"),
         st.dim(_rule(min(w, 72), "═")),
         f"repository   {st.cyan(_short(ref))}",
         f"revision     {at}", ""]
    L.append(f"  governing licence         {st.bold(state.governing or state.spdx_id)}")
    L.append(st.dim("      the operative grant: the first substantial licence text in the file"))
    if state.spdx_id and state.spdx_id != state.governing:
        for line in _wrap("all detected: " + state.spdx_id, w - 8):
            L.append(st.dim(f"      {line}"))
    if state.source_file:
        L.append(f"  read from                 {state.source_file}")
    if state.confidence:
        L.append(f"  detector confidence       {state.confidence}")
    for ev in state.evidence[:3]:
        L.append(st.dim(f"      matched: {ev}"))
    if state.needs_review:
        L.append("")
        L.append(st.yellow("  ! " + state.needs_review))
    L.append("")
    return "\n".join(L)


def render_trace(result, colour: bool | None = None) -> str:
    """`receipts trace` — who else holds this code."""
    st = Style(colour_enabled(force=colour))
    w = _term_width()
    L = [st.bold("RECEIPTS") + st.dim("  trace  ·  who else holds this code"),
         st.dim(_rule(min(w, 72), "═"))]
    for line in _wrap("Holding a repository's root commit means holding its "
                      "history. GitHub's `fork` flag does not report this: of 189 "
                      "repositories holding gogs/gogs's root commit, none is "
                      "declared a fork.", min(w, 72)):
        L.append(st.dim(line))
    L.append("")
    L.append(f"repository   {st.cyan(_short(result.repo))}")
    if result.root_commit:
        L.append(f"root commit  {result.root_commit[:12]}")
    if result.archived:
        L.append(f"archived     {result.archived}")
    hist = [h for h in result.holders if h.how == "history"]

    L.append("")
    L.append(st.bold(f"HOLDS THE HISTORY  ({len(hist)})"))
    L.append(st.dim(_rule(min(w, 72))))
    if not hist:
        L.append("  none found")
    for h in hist[:40]:
        flag = "" if h.declared_fork else st.yellow("   fork:false")
        L.append(f"  {h.name}{flag}")
    if len(hist) > 40:
        L.append(st.dim(f"  ... and {len(hist) - 40} more"))
    undeclared = sum(1 for h in hist if h.declared_fork is False)
    if hist:
        L.append("")
        L.append(st.dim(f"  {undeclared} of {len(hist)} do not declare themselves "
                        "forks of anything"))

    L.append("")
    for note in result.notes:
        L.append("")
        for line in _wrap("! " + note, min(w, 70)):
            L.append(st.yellow("  " + line))
    L.append("")
    return "\n".join(L)


_EVENT_MARK = {
    "created": "·", "licence": "!", "licence-file": "!!",
    "notices": "!!", "rewritten": "!!",
}


def render_history(hist, colour: bool | None = None) -> str:
    """One repository's life, for a reader who does not know git."""
    st = Style(colour_enabled(force=colour))
    w = _term_width()
    L = [st.bold("RECEIPTS") + st.dim("  history of one repository"),
         st.dim(_rule(min(w, 72), "═")),
         f"repository   {st.cyan(_short(hist.repo))}", ""]

    if not hist.events:
        L.append("  Nothing found: no licence file and no copyright notice in "
                 "this history.")
        L.append("")
        return "\n".join(L)

    for e in hist.events:
        mark = _EVENT_MARK.get(e.kind, " ")
        line = f"  {e.date}  {mark:<3}{e.summary}"
        # The events that usually mean something happened are the ones a reader
        # should not have to hunt for.
        L.append(st.yellow(line) if e.kind in ("notices", "rewritten", "licence-file")
                 else line)
        meta = "  ".join(x for x in (e.commit[:10] if e.commit else "",
                                     f"by {e.author}" if e.author else "") if x)
        if meta:
            L.append(st.dim(f"                 {meta}"))
        if e.detail:
            for ln in _wrap(e.detail, w - 20):
                L.append(st.dim(f"                 {ln}"))
        if e.verify:
            L.append(st.dim(f"                 verify: {e.verify}"))

    for note in hist.notes:
        L.append("")
        for ln in _wrap(note, min(w, 72) - 4):
            L.append(st.dim(f"  {ln}"))
    L.append("")
    return "\n".join(L)

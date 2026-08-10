"""Licence history of a single repository, including what its mainline hides."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from receipts import gitobjects as git
from receipts.models import LicenseTransition
from receipts.spdx import _terms, human_label, short_id, to_spdx

# `license` is imported inside the functions that need it, not here. It
# re-exports this module's `timeline` and `orphaned_states` for callers written
# before the split, so a module-level import in this direction closes a cycle:
# importing `receipts.timeline` first would find `receipts.license` still
# half-initialised. Deferring costs one dictionary lookup per call.

logger = logging.getLogger(__name__)

def _lic_api():
    """Deferred handle on `license`, to keep the import cycle open."""
    from receipts.license import NONE, UNRESOLVED, detect_text, license_candidates
    return NONE, UNRESOLVED, detect_text, license_candidates


def orphaned_states(repo: str, known: set[str]) -> list[tuple[str, str, str]]:
    """License states that exist ONLY in history unreachable from HEAD."""
    NONE, UNRESOLVED, detect_text, license_candidates = _lic_api()
    # `known` arrives as mainline EXPRESSIONS while the comparison below is on
    # governing licences, so match on terms rather than whole strings. Without
    # this, MongoDB's own AGPL era --- plainly on its mainline --- came back as
    # "found only in rewritten history".
    # Both sides normalised to SPDX first. Without this MongoDB reported its own
    # AGPL state as "orphaned" one day after the mainline records the identical
    # licence on the identical file, because the mainline stored
    # `LicenseRef-scancode-openssl-exception...` and the orphan path ran through
    # `to_spdx` and produced `openssl-exception-agpl-3.0`. Two spellings of one
    # licence.
    def _base(term: str) -> str:
        """`X WITH Y` -> `X`: an exception does not change which licence binds."""
        low = f" {term.lower()} "
        term = term.split(" WITH ", 1)[0].strip() if " with " in low else term
        # The mainline arrives as whole expressions, which keep ScanCode's
        # `LicenseRef-scancode-` prefix, while the orphan side is a `governing`
        # value, which has already dropped it. Two spellings of one licence:
        # mapbox-gl-js relicensed to proprietary ON ITS MAINLINE in December
        # 2020 and a rewritten commit carrying the same licence was reported as
        # a grant that exists only off the mainline.
        return short_id(term)

    seen = {t for expr in known for t in _terms(to_spdx(expr))}
    seen |= {to_spdx(k) for k in known}
    seen |= {_base(t) for t in list(seen)}
    out: list[tuple[str, str, str]] = []
    for commit, date, status, path in git.license_file_history(repo, orphaned=True):
        if status == "D":
            continue
        # Which file governs at a commit is decided by ranking the tree, not by
        # which file the commit touched. Reading the touched path directly made
        # every bundled dependency's licence in unreachable history look like a
        # grant the project had made and hidden: valkey picked up a BSD-2-Clause
        # and an MIT that belong to two of the libraries redis vendors.
        ranked = license_candidates(repo, commit)
        state = detect_text(git.file_text_at(repo, commit, ranked[0] if ranked
                                             else path))
        spdx = state.governing or state.spdx_id
        if not spdx or spdx in (NONE, UNRESOLVED):
            continue
        terms = [to_spdx(t) for t in _terms(spdx)]
        if any(t in seen or _base(t) in seen for t in [spdx, *terms]):
            continue
        out.append((commit, date, spdx))
        seen.add(spdx)
        known.add(spdx)
    if out:
        logger.warning("found %d license state(s) only in rewritten history: %s",
                       len(out), ", ".join(s for _, _, s in out))
    return out


def timeline(repo: str) -> list[LicenseTransition]:
    """Ordered license transitions across the mainline history (licdrift)."""
    NONE, _unresolved, detect_text, license_candidates = _lic_api()
    events = git.license_file_history(repo)
    logger.info("license history: %d commit(s) touched a license file", len(events))
    # One commit, one state. Reading each touched file as its own event turned a
    # rename into a relicensing and back: `LICENSE` -> `LICENSE.rst` in pallets/
    # click, and `COPYING` -> `LICENSE.txt` in redis, both single commits, both
    # reported as the project going unlicensed and then licensed again on the
    # same day.
    by_commit: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for commit, date, status, path in events:
        by_commit.setdefault(commit, (date, []))[1].append((status, path))

    transitions: list[LicenseTransition] = []
    current = current_gov = NONE
    for commit, (date, touched) in by_commit.items():
        # The state after a commit is the licence of the repository's OWN
        # licence file, which is not necessarily the file that was touched.
        # Akka vendored protobuf in 2015 and added `COPYING.protobuf` at the top
        # level; it starts with COPYING, so it read as the canonical licence and
        # `licdrift` reported Akka relicensing from Apache-2.0 to protobuf and
        # back six months later. Ranking the tree's candidates is what
        # `find_license` does for a single revision, and it is what makes
        # `licdrift` and `license` agree.
        alive = license_candidates(repo, commit)
        if not alive:
            alive = [p for s, p in touched if s != "D"]
        path = alive[0] if alive else touched[0][1]
        if not alive:
            after, full, gov = NONE, "", NONE
        else:
            st = detect_text(git.file_text_at(repo, commit, path))
            after, full = st.spdx_id, st.full_expression
            gov = st.governing or st.spdx_id
        # The trigger stays the whole expression: a bundled licence appearing is
        # a real change to the file and dropping the event would hide it. Only
        # the headline is the grant.
        if after != current:
            logger.info("license change %s -> %s at %s (%s)",
                        current, after, commit[:10], date[:10])
            transitions.append(LicenseTransition(
                date=date, commit=commit, before=current, after=after,
                file=path, after_full_expression=full,
                before_governing=current_gov, after_governing=gov))
            current, current_gov = after, gov
    return transitions




# --------------------------------------------------------------------------- #
# The narrative: one repository's history as events a non-programmer can follow
# --------------------------------------------------------------------------- #
# Commits examined for notice removals. `git log -G` walks every diff, so an
# unbounded search over a 300k-commit repository costs minutes to find an event
# that is nearly always recent. Raising this is a deliberate trade.
_MAX_NOTICE_COMMITS = 400

_COPYRIGHT = re.compile(r"copyright|\(c\)\s*\d{4}|©", re.I)
_SOURCE_GLOBS = (
    "*.c", "*.h", "*.cc", "*.cpp", "*.hpp", "*.m", "*.mm", "*.go", "*.py",
    "*.js", "*.ts", "*.java", "*.rs", "*.rb", "*.cs", "*.php", "*.swift",
    "*.kt", "*.scala", "*.sh",
)


@dataclass
class Event:
    """One thing that happened to a repository, and how to check it."""

    date: str                   # YYYY-MM-DD
    kind: str                   # created | licence | licence-file | notices | rewritten
    summary: str                # one line, for a reader who does not know git
    commit: str = ""
    author: str = ""
    detail: str = ""
    verify: str = ""            # the command that confirms this line


@dataclass
class History:
    repo: str
    events: list[Event] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"repository": self.repo,
                "events": [vars(e) for e in self.events],
                "notes": self.notes}


def _created(repo: str) -> list[Event]:
    roots = git.root_identities(repo)
    if not roots:
        return []
    first = min(roots, key=lambda r: r["date"])
    out = [Event(date=first["date"][:10], kind="created",
                 summary="repository created",
                 commit=first["commit"], author=first["author"],
                 verify="git log --max-parents=0 --format='%H %cI %an'")]
    # Several roots means history was rewritten or grafted --- or that another
    # project's history was merged in whole, which is how MongoDB's object graph
    # came to contain WiredTiger's root commits after acquiring them.
    if len(roots) > 1:
        out.append(Event(
            date=first["date"][:10], kind="rewritten",
            summary=f"{len(roots)} separate root commits exist here",
            detail="history was rewritten or grafted, or another project's "
                   "history was merged in whole",
            verify="git log --all --max-parents=0 --format='%H %cI %an'"))
    return out


def _licence_events(repo: str) -> list[Event]:
    out: list[Event] = []
    transitions = timeline(repo)
    for t in transitions:
        out.append(Event(
            date=t.date[:10], kind="licence",
            summary=f"licence {human_label(t.before)} -> {human_label(t.after)}",
            commit=t.commit, detail=f"in {t.file}",
            verify=f"git show {t.commit[:10]}:{t.file}"))
    known = {t.after for t in transitions} | {t.before for t in transitions}
    for commit, date, spdx in orphaned_states(repo, known):
        out.append(Event(
            date=date[:10], kind="rewritten",
            summary=f"licence {human_label(spdx)} exists ONLY off the main line",
            commit=commit,
            detail="the project rewrote its history, so this grant is invisible "
                   "on the current main branch. A grant is not revoked by a "
                   "later relicensing",
            verify=f"git merge-base --is-ancestor {commit[:10]} HEAD ; echo $?"))
    return out


def _licence_file_deleted(repo: str) -> list[Event]:
    out: list[Event] = []
    try:
        raw = git._run(repo, "log", "--diff-filter=D", "--name-only",
                       "--format=@%H|%cI|%an", "--",
                       "LICENSE*", "LICENCE*", "COPYING*", "NOTICE*")
    except git.GitError:                                      # pragma: no cover
        return out
    sha = date = author = ""
    for line in raw.splitlines():
        if line.startswith("@"):
            parts = (line[1:].split("|") + ["", ""])[:3]
            sha, date, author = parts
        elif line.strip():
            out.append(Event(
                date=date[:10], kind="licence-file",
                summary=f"{line.strip()} deleted",
                commit=sha, author=author,
                verify=f"git show {sha[:10]} --stat"))
    return out


def notice_removals(repo: str, limit: int = _MAX_NOTICE_COMMITS) -> list[Event]:
    """Commits that removed more copyright notices than they added."""
    out: list[Event] = []
    try:
        heads = git._run(repo, "log", f"-{limit}", "-G", r"[Cc]opyright",
                         "--format=%H|%cI|%an", "--", *_SOURCE_GLOBS)
    except git.GitError:                                      # pragma: no cover
        return out
    for line in heads.splitlines():
        if not line.strip():
            continue
        sha, _, rest = line.partition("|")
        date, _, author = rest.partition("|")
        try:
            patch = git._run(repo, "show", "--format=", "--unified=0", sha,
                             "--", *_SOURCE_GLOBS)
        except git.GitError:                                  # pragma: no cover
            continue
        removed = [l[1:].strip() for l in patch.splitlines()
                   if l.startswith("-") and not l.startswith("---")
                   and _COPYRIGHT.search(l)]
        added = sum(1 for l in patch.splitlines()
                    if l.startswith("+") and not l.startswith("+++")
                    and _COPYRIGHT.search(l))
        if len(removed) <= added:
            continue
        net = len(removed) - added
        out.append(Event(
            date=date[:10], kind="notices",
            summary=f"{net} copyright notice{'s' if net > 1 else ''} removed",
            commit=sha, author=author,
            detail=f'first: "{removed[0][:66]}"' if removed else "",
            verify=f"git show {sha[:10]}"))
    logger.info("notice removals: %d commit(s) net-removed a copyright line",
                len(out))
    return out


def history(repo: str, ref: str = "", licence_only: bool = False) -> History:
    """Every event in one repository's life, oldest first."""
    h = History(repo=ref or repo)
    h.events.extend(_licence_events(repo))
    # When the repository was created, and whether it has more than one root,
    # are questions about commits. `--licence-only` exists to skip the DIFF
    # walk, which needs blobs a blob-less clone does not carry — it was
    # dropping this too, so a command headed "history of one repository" left
    # out the day the repository began.
    h.events.extend(_created(repo))
    if not licence_only:
        h.events.extend(_licence_file_deleted(repo))
        h.events.extend(notice_removals(repo))
        h.notes.append(
            f"copyright removals searched over the newest {_MAX_NOTICE_COMMITS} "
            "commits that touch a notice line; older ones are not covered")
    h.events.sort(key=lambda e: (e.date, e.kind))
    if not h.events:
        h.notes.append("no licence file and no copyright notice found anywhere "
                       "in this history")
    return h

"""Reading the licence a repository carries, and which of them governs."""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import re

from receipts import gitobjects as git
from receipts.models import LicenseState
from receipts.scancode import (
    ScanCodeUnavailable, _scan_text, detector_name, require_scancode,
    scancode_version, scan_files,
)

NONE = "NONE"
UNRESOLVED = "NOASSERTION"

logger = logging.getLogger(__name__)

# A detection below this confidence is an incidental mention, not a grant.
#
# Measured, not guessed. Across seven licence files whose governing licence is
# known from the public record, every correct detection scored >= 80 and the two
# that had to be rejected scored 50.0 and 51.7:
#
#   directus   line   5  score  50.0  1 word   gpl-1.0-plus   <- matched the
#                                                                "MSCL-1.0-GPL"
#                                                                abbreviation of
#                                                                their own licence
#   bitwarden  line   1  score  51.7  30 words apache AND elastic AND ...
#   mysql      line  18  score  96.2  330 words gpl-2.0 WITH ...   <- correct
#   metabase   line   1  score  80.0  8 words   agpl-3.0-plus      <- correct
#
# 70 sits in the middle of that gap with margin either side. Match *length* is
# not usable: correct detections ran from 4 to 3,433 words.
#
# These seven files are tuning data. The threshold has not faced a held-out set.
# Lowered from 70 to 58 once the rule-kind check above took over the job of
# rejecting incidental mentions. A parameterised licence necessarily differs from
# its template --- Vault's Business Source Licence names IBM, the product and the
# change date --- and its real text scores **63.7**, which 70 excluded outright.
# 58 sits between that and the highest noise measured (51.7).
_GRANT_SCORE = 58.0

def _first_grant(entry: dict) -> str | None:
    """The earliest substantial licence detection in the file, or None."""
    best: tuple[int, float, int, str] | None = None
    for det in entry.get("license_detections", []) or []:
        for m in det.get("matches", []) or []:
            line, score = m.get("start_line"), m.get("score")
            ident = (m.get("spdx_license_expression")
                     or m.get("license_expression") or "")
            if line is None or not ident or _is_uninformative(str(ident)):
                continue
            if not isinstance(score, (int, float)) or score < _GRANT_SCORE:
                continue
            # A tag or a bare reference points AT a licence; it is not one.
            if not _is_grant_rule(m.get("rule_identifier")):
                continue
            # On the same line, the most CONFIDENT match wins, then the longest.
            # Length alone was wrong and Audacity showed why: its first line,
            # "Audacity is released under the GNU General Public License
            # version 3 (GPLv3)", produces two detections --
            #
            #   GPL-1.0-or-later  score  80  8 words  (generic "GNU GPL")
            #   GPL-3.0-only      score 100  7 words  (the version phrase)
            #
            # -- and the longer, vaguer one won, reporting a project that says
            # GPLv3 on its first line as GPL-1.0-or-later. The same inversion hit
            # the divergence cell, where GPL-2.0-or-later (score 100) lost to
            # GPL-2.0-only (score 95).
            key = (int(line), -float(score), -int(m.get("matched_length") or 0),
                   str(ident))
            if best is None or key < best:
                best = key
    if best is None:
        return None
    expression = to_spdx(best[-1])
    logger.debug("governing grant at line %d: %s", best[0], expression)
    # A single-term expression is the grant itself; a compound one is resolved
    # by the same tree rules used everywhere else.
    parsed = _parse(expression)
    return (_governing_of(parsed, set()) if parsed is not None else expression) \
        or expression


def summarize(entry: dict) -> LicenseState:
    """Turn a ScanCode file entry into a readable LicenseState."""
    full = (entry.get("detected_license_expression_spdx")
            or entry.get("detected_license_expression") or "")
    if not full:
        return LicenseState(spdx_id=UNRESOLVED, confidence=0.0,
                            detector="scancode", full_expression="")

    terms = _terms(full)
    primary = [t for t in terms if not _is_uninformative(t)]
    noise = [t for t in terms if _is_uninformative(t)]

    # The two SPDX operators mean opposite things for "which licence governs":
    #   AND — every listed licence applies, so obligations accumulate and the
    #         MOST restrictive term is the binding one.
    #   OR  — the recipient chooses, so the LEAST restrictive term is what they
    #         can actually rely on.
    # Sorting an OR list by restrictiveness would therefore mislead in exactly
    # the wrong direction, so only AND expressions are reordered.
    # https://spdx.github.io/spdx-spec/v2.2.2/SPDX-license-expressions/
    is_or = _dominant_operator(full).strip() == "OR"

    # The operative grant is the FIRST substantial detection in the file.
    #
    # Licence files are written top-down: the grant comes first, and any
    # inventory of third-party components follows it. MySQL's licence file makes
    # the point on its own --- the GPL-2.0 grant is at line 18, the third-party
    # enumeration begins at line 488 --- and reading such a file for its "most
    # restrictive" term returns whichever bundled dependency happens to be the
    # strictest. That is how MySQL came back as `BSL-1.0`, the *Boost* licence.
    governing = _first_grant(entry)
    if governing is None:
        # No usable line information: fall back to reading the parse tree, where
        # AND takes the most restrictive term and OR the least.
        parsed = _parse(full)
        governing = _governing_of(parsed, set(noise)) if parsed is not None else None
    # BUG FIX: the headline keeps ScanCode's own expression order. Re-sorting it
    # by restrictiveness produced a string ScanCode never emitted --- Kibana's
    # `Elastic-2.0 OR AGPL-3.0-only OR SSPL-1.0`, which is already the right
    # answer, came out rearranged. Which term BINDS is answered by `governing`;
    # the headline's job is to report what was detected, faithfully.
    if governing is None:
        governing = ((max(primary, key=restrictiveness) if is_or else primary[0])
                     if primary else UNRESOLVED)
    # One licence, one spelling. The two paths above disagree: ScanCode's
    # per-match field carries the bare key (`proprietary-license`) and its
    # expression field carries `LicenseRef-scancode-proprietary-license`, so
    # which one a repository got depended on whether its detections had line
    # numbers. Comparisons downstream are string equality, and the mismatch
    # reported mapbox-gl-js's mainline relicensing as a grant that exists only
    # in rewritten history. `spdx_id` keeps ScanCode's own spelling — it is the
    # evidence of record.
    governing = short_id(governing)

    scores: list[float] = []
    evidence: list[str] = []
    for det in entry.get("license_detections", []) or []:
        for m in det.get("matches", []) or []:
            sc = m.get("score")
            if isinstance(sc, (int, float)):
                scores.append(sc / 100.0)
            rule = m.get("rule_identifier") or m.get("matched_rule", {}).get("identifier")
            s, e = m.get("start_line"), m.get("end_line")
            ident = m.get("spdx_license_expression") or m.get("license_expression") or ""
            if rule and s is not None and not _is_uninformative(str(ident)):
                evidence.append(f"{ident or '?'} @ lines {s}-{e} (rule {rule})")

    # Verbatim when nothing was pruned; rebuilt only when uninformative terms
    # had to come out, and then in ScanCode's own order.
    headline = (full if not noise
                else _dominant_operator(full).join(primary)) if primary else UNRESOLVED
    return LicenseState(
        spdx_id=headline,
        confidence=round(max(scores), 2) if scores else 0.0,
        detector="scancode",
        full_expression=full,
        also_detected=noise,
        evidence=evidence[:10],
        governing=governing,
        needs_review=_review_reason(primary),
    )


def _review_reason(primary: list[str]) -> str:
    """Why a human must look at this licence result, or "" if nothing stands out."""
    if not primary:
        return "no licence text resolved — read the file yourself"

    top = primary[0]
    category = category_of(top)
    if not category:
        return (f"ScanCode has no category for {top}, so it was ranked as the "
                "most restrictive term by default — confirm what it actually is")
    if _is_unnamed_licence(top):
        return (f"the governing text has no SPDX id (ScanCode calls it {top}, "
                f"category {category}) — read it verbatim before relying on this")

    # A tree whose licences fall in different categories has no single answer.
    # `governing` is the most restrictive OBLIGATION, which is not always what
    # the project declares: Neovim declares Apache-2.0 and carries Vim-licensed
    # parts, so the governing term is Vim (Copyleft) while the declaration is
    # Apache-2.0 (Permissive). Both readings are defensible; only a person can
    # pick the right one for the question being asked.
    categories = {category_of(t) for t in primary if category_of(t)}
    if len(categories) > 1:
        return (f"{len(primary)} licence texts spanning {len(categories)} "
                f"categories ({', '.join(sorted(categories))}); the headline is "
                "the most restrictive OBLIGATION, which may not be what the "
                "project declares")
    if len(primary) > 2:
        return (f"{len(primary)} licence texts co-exist here; the headline is a "
                "reading, not a declaration")
    return ""


def holders_from(entry: dict) -> list[str]:
    """Copyright holders / authors per ScanCode's own copyright detection."""
    out: list[str] = []
    for key, field in (("holders", "holder"), ("copyrights", "copyright"),
                       ("authors", "author")):
        for item in entry.get(key, []) or []:
            val = item.get(field) if isinstance(item, dict) else item
            if val:
                out.append(str(val))
    return list(dict.fromkeys(out))


# --------------------------------------------------------------------------- #
# Public detection API (cached)
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=512)
def _detect_cached(_hash: str, text: str) -> LicenseState:
    entry = _scan_text(text)
    if entry is None:
        return LicenseState(spdx_id=UNRESOLVED, confidence=0.0, detector="scancode")
    return summarize(entry)


def detect_text(text: str) -> LicenseState:
    """Detected license state of a license text (ScanCode)."""
    if not text or not text.strip():
        return LicenseState(spdx_id=NONE, confidence=1.0, detector="scancode")
    h = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
    cached = _detect_cached.cache_info().hits
    st = _detect_cached(h, text)
    if _detect_cached.cache_info().hits > cached:
        logger.debug("license cache hit (%s)", st.spdx_id)
    else:
        logger.info("detected license: %s (%d bytes scanned)", st.spdx_id, len(text))
    return st


@functools.lru_cache(maxsize=256)
def _holders_cached(_hash: str, text: str) -> tuple[str, ...]:
    entry = _scan_text(text)
    return tuple(holders_from(entry)) if entry else ()


def detect_holders(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    h = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
    return list(_holders_cached(h, text))


# --------------------------------------------------------------------------- #
# Locating the license of a tree (never by guessing a filename)
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=1)
def _legal_name_parts() -> tuple[str, ...]:
    """ScanCode's own list of name fragments that mark a file as legal."""
    try:
        from summarycode.classify import LEGAL_STARTS_ENDS
        return tuple(LEGAL_STARTS_ENDS) + ("unlicense",)
    except Exception:                                       # pragma: no cover
        logger.debug("summarycode.classify unavailable; using a local fallback")
        return ("copying", "copyright", "copyleft", "notice", "license",
                "licence", "licensing", "legal", "eula", "unlicense", "patent")


def _is_legal_name(base: str) -> bool:
    """Does this file name start or end with a legal-file fragment?"""
    stem = base.lower().rsplit(".", 1)[0]
    return any(stem.startswith(p) or stem.endswith(p) for p in _legal_name_parts())

# Names that look license-ish but are never the project's own license: dependency
# manifests, templates, and license *documentation*. `.release/LICENSE_DEPENDENCIES.tpl`
# outranking the real LICENSE in openbao is what made receipts accuse a compliant
# fork of stripping HashiCorp's notice (BENCHMARK §D.4).
_NOT_LICENSE = re.compile(
    r"depend|third[-_ ]?party|vendor|node_modules|template|\.tpl$|[-_]readme|"
    r"\.(py|js|ts|go|rb|java|c|h|rs|sh|ya?ml|json|toml|cfg)$|"
    r"\.(png|jpe?g|gif|svg|ico|pdf|zip|gz|jar|so|dll|dylib|exe|bin)$", re.I)

# Canonical names, preferred over decorated variants (LICENSE-APACHE, COPYING.LESSER).
_EXACT_LICENSE = re.compile(r"^(licen[cs]e|copying|unlicense)(\.(txt|md|rst))?$", re.I)

_MAX_CANDIDATES = 8   # keeps the batched scan small; trees rarely have more


def license_candidates(repo: str, commit: str = "HEAD") -> list[str]:
    """License-bearing candidate paths at *commit*, best first."""
    out = []
    for path in git.list_tree(repo, commit):
        base = os.path.basename(path)
        if not _is_legal_name(base) or _NOT_LICENSE.search(path):
            continue
        out.append(path)
    out.sort(key=lambda p: (p.count("/"),
                            0 if _EXACT_LICENSE.match(os.path.basename(p)) else 1,
                            len(p)))
    return out[:_MAX_CANDIDATES]


def find_license(repo: str, commit: str = "HEAD") -> LicenseState:
    """The license of *repo* at *commit*, as evidenced by its tree."""
    candidates = license_candidates(repo, commit)
    if not candidates:
        logger.info("no license-bearing file in the tree at %s", commit[:10])
        return LicenseState(spdx_id=NONE, confidence=1.0, detector="scancode",
                            commit=commit)

    texts = {p: git.file_text_at(repo, commit, p) for p in candidates}
    # Drop empties and anything that decoded as binary: a replacement-character
    # soup is not license text and would only add noise to the scan.
    texts = {p: t for p, t in texts.items()
             if t.strip() and t.count("\ufffd") < len(t) * 0.05}
    if not texts:
        return LicenseState(spdx_id=NONE, confidence=1.0, detector="scancode",
                            commit=commit)

    logger.debug("license candidates at %s: %s", commit[:10], ", ".join(texts))
    entries = scan_files(texts)
    fallback: LicenseState | None = None
    for path in candidates:                      # rank order decides ties
        entry = entries.get(path)
        if entry is None:
            continue
        state = summarize(entry)
        state.source_file, state.commit = path, commit
        if state.spdx_id not in (NONE, UNRESOLVED):
            logger.info("license at %s: %s (from %s)", commit[:10], state.spdx_id, path)
            return state
        fallback = fallback or state
    logger.info("license at %s: unresolved (%d candidate(s) scanned)",
                commit[:10], len(texts))
    return fallback or LicenseState(spdx_id=UNRESOLVED, confidence=0.0,
                                    detector="scancode", commit=commit)


def license_text(repo: str, state: LicenseState) -> str:
    """The raw text backing a LicenseState (for copyright-holder extraction)."""
    if not state.source_file or not state.commit:
        return ""
    return git.file_text_at(repo, state.commit, state.source_file)



# --------------------------------------------------------------------------- #
# Re-exports.
#
# Splitting a 1,100-line module is an internal change; every caller that says
# `lic.require_scancode()` or `from receipts.license import to_spdx` keeps
# working. The names below are the module's public surface, and the only reason
# this list exists is so that surface stays a deliberate choice rather than
# whatever happens to be defined.
# --------------------------------------------------------------------------- #

from receipts.spdx import (                                        # noqa: E402
    category_of, human_label, restrictiveness, short_id, to_spdx,
    _is_grant_rule, _is_uninformative, _is_unnamed_licence,
    _dominant_operator, _governing_of, _parse, _terms,
)
from receipts.timeline import orphaned_states, timeline            # noqa: E402

__all__ = [
    "NONE", "UNRESOLVED", "ScanCodeUnavailable",
    "find_license", "license_candidates", "license_text", "summarize",
    "detect_text", "detect_holders", "holders_from",
    "category_of", "restrictiveness", "to_spdx", "human_label",
    "timeline", "orphaned_states",
    "require_scancode", "scancode_version", "detector_name", "scan_files",
]

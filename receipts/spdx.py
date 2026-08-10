"""SPDX vocabulary: identifiers, categories, expressions."""

from __future__ import annotations

import functools
import logging
import os
import re

NONE = "NONE"
UNRESOLVED = "NOASSERTION"

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Summarizing ScanCode output for humans (and for DMCA text)
# --------------------------------------------------------------------------- #

# ScanCode reports every license *referenced* in a file. These reference kinds are
# real detections but are not the license the file is under, so they are pruned from
# the headline expression and listed separately instead.
# Detections that genuinely carry no licence information: a mention of a licence
# without its text, a contributor agreement, a bare warranty disclaimer.
_UNINFORMATIVE = (
    "unknown-license-reference", "generic-cla", "generic-exception",
    "free-unknown", "warranty-disclaimer",
)

# Detections that DO carry licence information and simply have no short SPDX id.
# These were previously in the list above, under the name "noise", and pruning
# them cost this project its worst error: `mapbox/mapbox-gl-js` carries the
# Mapbox Terms of Service, ScanCode detected it correctly as
#
#     LicenseRef-scancode-proprietary-license AND BSD-3-Clause AND MIT
#
# and the headline came out `BSD-3-Clause AND MIT` — a repository that had left
# open source entirely, reported as open source. A tool for finding relicensing
# must never discard the term "proprietary". These always govern the headline.
_UNNAMED_LICENCE = (
    "proprietary-license", "commercial-license", "other-copyleft",
    "other-permissive",
)
_EXCEPTION_HINT = ("-exception", "additional-terms")

# Restrictiveness comes from ScanCode's own curated `category` field, not from a
# list maintained here. The hand-written list this replaces had exactly the
# failure mode a hand-written list has: every licence missing from it scored as
# unknown, and unknown ranks as most restrictive, so any unlisted licence seized
# the headline. Held-out #4: `Vim` is a real SPDX id, was absent from the list,
# and became Neovim's reported licence over `Apache-2.0`.
#
# ScanCode ships ~2,600 licences each tagged with one of these categories,
# maintained by people who do this full time.
_CATEGORY_ORDER = (
    "Commercial", "Proprietary Free", "Source-available", "Free Restricted",
    "Copyleft", "Copyleft Limited", "Patent License", "CLA",
    "Unstated License", "Permissive", "Public Domain",
)


@functools.lru_cache(maxsize=1)
def _category_index() -> dict[str, str]:
    """{licence id (lowercased): ScanCode category} for every licence it knows."""
    try:
        import licensedcode
        root = os.path.join(os.path.dirname(licensedcode.__file__),
                            "data", "licenses")
        names = os.listdir(root)
    except Exception as exc:                        # pragma: no cover
        logger.warning("ScanCode licence categories unavailable (%s); every "
                       "licence will rank as most restrictive", exc)
        return {}

    index: dict[str, str] = {}
    for name in names:
        if not name.endswith(".LICENSE"):
            continue
        category, ids = "", []
        try:
            with open(os.path.join(root, name), encoding="utf-8",
                      errors="replace") as fh:
                for line in fh:
                    if line.startswith("---") and index.get("__seen__") is None:
                        continue
                    if line.startswith("category:"):
                        category = line.split(":", 1)[1].strip()
                    elif line.startswith(("key:", "spdx_license_key:")):
                        ids.append(line.split(":", 1)[1].strip())
                    elif line.startswith("    - LicenseRef") or line.startswith("    - "):
                        val = line.strip()[2:].strip()
                        if val and " " not in val:
                            ids.append(val)
                    elif line and not line.startswith((" ", "-")) and ":" not in line:
                        break               # past the front matter, into the text
        except OSError:                                  # pragma: no cover
            continue
        if category:
            for i in ids:
                index.setdefault(i.lower(), category)
    logger.debug("loaded %d licence categories from ScanCode", len(index))
    return index


@functools.lru_cache(maxsize=1)
def _spdx_index() -> dict[str, str]:
    """{ScanCode key: SPDX id} for every licence ScanCode knows."""
    out: dict[str, str] = {}
    try:
        import licensedcode
        root = os.path.join(os.path.dirname(licensedcode.__file__), "data", "licenses")
        names = os.listdir(root)
    except Exception:                                        # pragma: no cover
        return out
    for name in names:
        if not name.endswith(".LICENSE"):
            continue
        key = spdx = ""
        try:
            with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("key:"):
                        key = line.split(":", 1)[1].strip()
                    elif line.startswith("spdx_license_key:"):
                        spdx = line.split(":", 1)[1].strip()
                    elif key and spdx:
                        break
        except OSError:                                      # pragma: no cover
            continue
        if key and spdx and not spdx.lower().startswith("licenseref-scancode"):
            out[key.lower()] = spdx
    return out


def to_spdx(expression: str) -> str:
    """Rewrite ScanCode keys in *expression* as SPDX ids where one exists."""
    if not expression:
        return expression
    idx = _spdx_index()
    return re.sub(r"[A-Za-z0-9.\-+]+",
                  lambda m: idx.get(m.group(0).lower(), m.group(0)), expression)


def category_of(spdx_id: str) -> str:
    """ScanCode's category for a licence id, or "" if it knows none."""
    idx = _category_index()
    low = spdx_id.strip().lower()
    if low in idx:
        return idx[low]
    # `X WITH Y` is one licence carrying an exception, and only the licence is
    # categorised. Looking the compound up whole found nothing, so MySQL --- a
    # case correct in all three cells --- came back with three review flags
    # reading "ScanCode has no category for GPL-2.0-only WITH ...". False alarms
    # are the one thing that section cannot afford: a caveat on everything is a
    # caveat on nothing.
    if " with " in f" {low} ":
        return category_of(low.split(" with ", 1)[0])
    # Detections arrive as `LicenseRef-scancode-<key>`; the key is what is indexed.
    if low.startswith("licenseref-scancode-"):
        return idx.get(low[len("licenseref-scancode-"):], "")
    return ""


def _is_uninformative(spdx_id: str) -> bool:
    low = spdx_id.lower()
    if any(p in low for p in _UNINFORMATIVE):
        return True
    # `X WITH Y-exception` is a LICENCE carrying an exception, not a bare
    # exception reference, and pruning it discards the grant itself. MySQL's
    # licence file opens with
    #     GPL-2.0-only WITH mysql-linking-exception-2018
    # and this rule threw it away, leaving a bundled Boost licence 470 lines
    # further down as the file's answer.
    if " with " in f" {low} ":
        return False
    return any(h in low for h in _EXCEPTION_HINT)


def _is_unnamed_licence(spdx_id: str) -> bool:
    """A real licence that SPDX has no short id for."""
    if category_of(spdx_id) in ("Commercial", "Proprietary Free", "Source-available"):
        return spdx_id.lower().startswith("licenseref-")
    return any(p in spdx_id.lower() for p in _UNNAMED_LICENCE)


def restrictiveness(spdx_id: str) -> int:
    """Rank by ScanCode's category; an id ScanCode does not know sorts first."""
    category = category_of(spdx_id)
    if not category:
        return -1
    try:
        return _CATEGORY_ORDER.index(category)
    except ValueError:
        return -1


@functools.lru_cache(maxsize=1)
def _licensing():
    """nexB's SPDX expression parser — the one ScanCode itself uses."""
    import license_expression
    return license_expression.get_spdx_licensing()


def _parse(expression: str):
    """Parsed SPDX expression, or None if it will not parse."""
    if not expression:
        return None
    try:
        return _licensing().parse(expression, simple=True)
    except Exception as exc:                              # pragma: no cover
        logger.debug("could not parse licence expression %r: %s", expression, exc)
        return None


def _terms(expression: str) -> list[str]:
    """Licence ids in an SPDX expression, in order, deduplicated."""
    parsed = _parse(expression)
    if parsed is None:
        parts = re.split(r"\s+(?:AND|OR)\s+|[()]", expression or "", flags=re.I)
        return list(dict.fromkeys(p.strip() for p in parts if p and p.strip()))
    symbols = _licensing().license_symbols(parsed, unique=True, decompose=False)
    return [str(s) for s in symbols]


def _dominant_operator(expression: str) -> str:
    """The TOP-LEVEL operator: ' OR ' for a choice, ' AND ' for co-presence."""
    import license_expression as _le
    parsed = _parse(expression)
    if parsed is None:
        has_or = re.search(r"\bOR\b", expression or "", flags=re.I) is not None
        has_and = re.search(r"\bAND\b", expression or "", flags=re.I) is not None
        return " OR " if has_or and not has_and else " AND "
    return " OR " if isinstance(parsed, _le.OR) else " AND "


def _governing_of(node, skip) -> str | None:
    """The binding licence of a parse tree, honouring nesting."""
    import license_expression as _le
    if isinstance(node, (_le.LicenseSymbol, _le.LicenseWithExceptionSymbol)):
        name = str(node)
        return None if name in skip else name
    args = [_governing_of(a, skip) for a in getattr(node, "args", ())]
    args = [a for a in args if a]
    if not args:
        return None
    if isinstance(node, _le.OR):
        # A choice is not a licence. Elasticsearch offers
        # `Elastic-2.0 OR AGPL-3.0-only OR SSPL-1.0`; naming one of the three
        # tells a recipient entitled to pick Elastic-2.0 that they are under
        # AGPL. The whole choice is the answer, deduplicated and in the order
        # the project wrote it.
        seen, terms = set(), []
        for a in args:
            if a not in seen:
                seen.add(a)
                terms.append(a)
        return terms[0] if len(terms) == 1 else " OR ".join(terms)
    # Obligations accumulate, so the most restrictive term binds. On a tie, a
    # licence with a real SPDX id beats one ScanCode only has a local reference
    # for: ScanCode reads part of HashiCorp's BUSL text as `acter-psl-1.0` (the
    # Acter PSL is derived from BUSL, so the texts overlap), both are
    # Source-available, and the tie went to whichever came first in the
    # expression — reporting that Terraform relicensed to Acter PSL.
    return min(args, key=lambda a: (restrictiveness(a),
                                    a.lower().startswith("licenseref-")))




@functools.lru_cache(maxsize=1)
def _rule_kinds() -> dict[str, str]:
    """{rule id: 'text' | 'notice' | 'tag' | 'reference'} from ScanCode's rules."""
    kinds: dict[str, str] = {}
    try:
        import licensedcode
        root = os.path.join(os.path.dirname(licensedcode.__file__), "data", "rules")
        names = os.listdir(root)
    except Exception:                                        # pragma: no cover
        logger.debug("ScanCode rule metadata unavailable; grant-kind check off")
        return kinds
    for name in names:
        if not name.endswith(".RULE"):
            continue
        try:
            with open(os.path.join(root, name), encoding="utf-8",
                      errors="replace") as fh:
                for line in fh:
                    if line.startswith("is_license_"):
                        key, _, val = line.partition(":")
                        if val.strip().lower() in ("yes", "true"):
                            kinds[name] = key[len("is_license_"):].strip()
                            break
                    elif line.startswith("---") and name in kinds:
                        break
        except OSError:                                      # pragma: no cover
            continue
    logger.debug("loaded %d ScanCode rule kinds", len(kinds))
    return kinds


def _is_grant_rule(rule_identifier: str | None) -> bool:
    """Does this rule match a licence GRANT rather than a parameter naming one?"""
    if not rule_identifier:
        return True                      # unknown provenance: do not exclude
    return _rule_kinds().get(rule_identifier) != "tag"



_REF = "licenseref-scancode-"


def short_id(expression: str) -> str:
    """Drop ScanCode's `LicenseRef-scancode-` prefix from every term."""
    return " ".join(
        t[len(_REF):] if t.lower().startswith(_REF) else t
        for t in (expression or "").split(" "))


def human_label(spdx_id: str) -> str:
    if spdx_id == UNRESOLVED:
        return "NOASSERTION (unrecognized by ScanCode — inspect manually)"
    if spdx_id == NONE:
        return "NONE (no license file / all rights reserved)"
    return spdx_id

"""Data models — plain dataclasses, no logic."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class LicenseState:
    """Detected license at a point in a repo's history."""
    spdx_id: str            # e.g. "MIT", "Apache-2.0", "BSL-1.1", or "NONE"
    confidence: float       # 0..1
    source_file: Optional[str] = None
    commit: Optional[str] = None
    date: Optional[str] = None
    # Engine that produced the finding (always "scancode" — the only detector).
    detector: str = ""
    # ScanCode's verbatim expression: the evidence of record. `spdx_id` above is the
    # readable headline pruned from it (see license.summarize).
    full_expression: str = ""
    # License references pruned from the headline (CLA text, exceptions, unknown
    # references). Kept so nothing detected is hidden from the reader.
    also_detected: list[str] = field(default_factory=list)
    # SPDX calls the authors' own statement the DECLARED license and a reviewer's
    # reading of it the CONCLUDED license. `spdx_id` above is a whole expression
    # and can hold several texts; `governing` is the single most restrictive of
    # them — a conclusion, not a declaration, and labelled as such in reports.
    # https://spdx.github.io/spdx-spec/v2.2.2/package-information/
    governing: str = ""
    # Non-empty when this particular result is of a shape the tool is measurably
    # worse at, and a human should look before it is relied on. See
    # license._review_reason.
    needs_review: str = ""
    # Citable matches: "<license> @ lines A-B (rule <id>)" — usable verbatim in a
    # DMCA notice or a footnote.
    evidence: list[str] = field(default_factory=list)

@dataclass
class LicenseTransition:
    """A change in license over a repo's history (licdrift)."""
    date: str
    commit: str
    before: str
    after: str
    file: str
    # ScanCode's full expression for the post-change state (evidence of record).
    after_full_expression: str = ""
    # The operative grant before and after. `before`/`after` are the whole pruned
    # expression, which changes whenever a bundled dependency's licence is added
    # or dropped; `license` and `provenance` both headline the grant instead, and
    # for mapbox-gl-js that meant one file reported as `BSD-3-Clause` in two
    # commands and `BSD-3-Clause AND BSD-2-Clause AND MIT AND Apache-2.0` in a
    # third. Same detection, two answers.
    before_governing: str = ""
    after_governing: str = ""


@dataclass
class Observation:
    """One factual finding: a stable code plus the sentence shown to a human."""
    code: str
    text: str


@dataclass
class Overlap:
    """Shared-history evidence between two repos."""
    total_commits_a: int
    total_commits_b: int
    shared_commits: int
    # Two different questions, both worth answering:
    #   of_upstream — how much of the upstream's history the suspect holds
    #   of_suspect  — how much of what the suspect holds came from the upstream
    shared_pct_of_upstream: float
    shared_pct_of_suspect: float
    # The newest commit both repositories contain. NOT necessarily a fork point:
    # git history is connected through parents, so `shared_commits` is the SIZE of
    # the common ancestry — two shared commits means a two-commit shared seed, not
    # a fork (Emby → Jellyfin, BENCHMARK §E.4).
    common_commit: Optional[str]
    common_commit_date: Optional[str]
    total_blobs_b: int = 0
    shared_blobs: int = 0
    distinctive_shared_blobs: int = 0   # excludes boilerplate/common
    shared_blob_paths: list[str] = field(default_factory=list)
    # Of the shared commits, how many the COMPARED repository's own HEAD
    # reaches. A fork's clone keeps the upstream's other branches, so the two
    # numbers differ: openbao holds 20,122 of Vault's commits and its own line
    # reaches 17,927. Which number a claim rests on changes the claim.
    shared_on_compared_line: int = 0

    @property
    def common_history_is_negligible(self) -> bool:
        """True when the shared lineage is too small to be a fork point."""
        return bool(self.shared_commits) and self.shared_pct_of_upstream < 1.0


@dataclass
class AttributionDiff:
    """Did the derivative preserve authorship / notices?"""
    upstream_authors: list[str] = field(default_factory=list)
    suspect_authors: list[str] = field(default_factory=list)
    upstream_authors_present_in_suspect: bool = False
    upstream_notice_present: bool = False
    suspect_notice_present: bool = False
    notice_stripped: bool = False       # upstream had a notice, suspect doesn't
    # Per-FILE copyright headers on paths present in both repos. Most permissive
    # licenses (Apache-2.0 §4, MIT) require retaining these, so replacing them is
    # the actual violation even when relicensing itself is allowed.
    files_compared: int = 0
    files_header_replaced: int = 0       # upstream holder gone, other holder present
    files_header_dropped: int = 0        # upstream header gone, no holder at all
    header_examples: list[str] = field(default_factory=list)
    # Paths whose header changed, kept so a snapshot can extract the disputed
    # region side by side without re-deriving it from the example strings.
    changed_header_paths: list[str] = field(default_factory=list)

    @property
    def headers_stripped(self) -> bool:
        return (self.files_header_replaced + self.files_header_dropped) > 0


@dataclass
class Custody:
    """Chain-of-custody metadata for reproducibility (SPEC §4)."""
    generated_at: str
    tool_version: str
    git_version: str
    upstream_ref: str
    suspect_ref: str
    upstream_head: str
    suspect_head: str
    evidence_sha256: str = ""           # hash of the canonical evidence bundle
    license_detector: str = "scancode"  # the only detector
    license_detector_version: str = ""  # ScanCode version string
    # The upstream commit every upstream-side reading was taken at, and how it was
    # chosen. Part of the evidence: a finding is only meaningful relative to the
    # moment it describes.
    anchor_commit: str = ""
    anchor_basis: str = ""


@dataclass
class Report:
    upstream: str
    suspect: str
    overlap: Overlap
    upstream_license_at_divergence: Optional[LicenseState]
    upstream_license_now: Optional[LicenseState]
    suspect_license_now: Optional[LicenseState]
    attribution: AttributionDiff
    confidence: str                     # "high" | "medium" | "low" | "none"
    derivation_likely: bool
    # Upstream's license (at divergence) vs the suspect's current license — a
    # CROSS-repo comparison, only meaningful when derivation is established.
    # NOT the suspect's own licdrift. False when derivation_likely is False.
    license_differs_from_upstream: bool
    # Factual statements about what was observed, each with a stable `code`.
    # No legal conclusion: the tool reports evidence, a human decides what it
    # means (see `open_questions`).
    observations: list[Observation]
    # What a reviewer must establish that this tool cannot — permission granted
    # off-repo, whether the license allowed the reuse. Deliberately plain strings:
    # these are prompts for a person and carry no machine semantics, unlike
    # observations, which a consumer may act on.
    open_questions: list[str]
    summary: str
    custody: Custody
    # Non-empty when the derivation result is of a shape this tool gets wrong:
    # a sliver of shared history, or identical files with no history at all.
    # Named specifically rather than a blanket "verify everything", which reads
    # as boilerplate and gets skipped.
    derivation_needs_review: str = ""
    # All identities of all root commits of the upstream (ownership signal, not
    # proof — never assume a single author). Each: {commit,date,author,committer}.
    upstream_roots: list[dict[str, str]] = field(default_factory=list)
    # Shared commits split by the licence the origin carried when each was made.
    # A single anchor commit is one moment; a fork taken from a release branch
    # can share commits that straddle a relicensing, and this says so.
    # Each: {licence, commits, from_commit, from_date}.
    licence_eras: list[dict] = field(default_factory=list)
    # Files holding upstream expression under a notice that names only someone
    # else. Unlike `attribution`, which needs a byte-identical ancestor, this
    # survives the suspect editing the code as it copied — the normal case.
    # Each: {path, upstream_path, shared_lines, notice, sample}.
    unattributed: list[dict[str, Any]] = field(default_factory=list)
    unattributed_examined: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # asdict() serializes fields only, so derived properties would silently
        # vanish from the machine-readable output — including the flag that
        # decides whether the prose hedges. Add them explicitly.
        d["overlap"]["common_history_is_negligible"] = (
            self.overlap.common_history_is_negligible)
        d["attribution"]["headers_stripped"] = self.attribution.headers_stripped
        # The weak spots, in the machine-readable record too: a consumer that
        # only reads JSON must not miss what a human reader is told to check.
        from receipts.terminal import review_points
        d["needs_human_review"] = [{"finding": f, "why": w}
                                   for f, w in review_points(self)]
        return d

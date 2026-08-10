"""Evidence snapshot — preserve the findings before the repositories change."""

from __future__ import annotations

import datetime
import functools
import hashlib
import json
import logging
import os
import subprocess

from receipts import gitobjects as git
from receipts import overlap as ov
from receipts.models import Report

logger = logging.getLogger(__name__)

_HEADER_LINES = 15   # enough to cover a license header, no more


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")


@functools.lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """SHA-256 over every `receipts/*.py`, identifying the build that ran."""
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for name in sorted(os.listdir(here)):
        if not name.endswith(".py"):
            continue
        try:
            with open(os.path.join(here, name), "rb") as fh:
                h.update(name.encode())
                h.update(fh.read())
        except OSError:                                      # pragma: no cover
            continue
    return h.hexdigest()


def _collector() -> dict[str, str]:
    """Who ran this — the identification pillar."""
    def cfg(key: str) -> str:
        try:
            return subprocess.run(["git", "config", "--get", key],
                                  capture_output=True, text=True).stdout.strip()
        except Exception:
            return ""
    return {"name": cfg("user.name"), "email": cfg("user.email"),
            "host": os.uname().nodename if hasattr(os, "uname") else ""}


def default_dir(base: str = ".") -> str:
    """A fresh `receipts-evidence-<timestamp>/` inside *base*."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(base, f"receipts-evidence-{stamp}")


def write_snapshot(report: Report, upstream: str, suspect: str, out_dir: str,
                   renderings: dict[str, str], include_source: bool = False) -> str:
    """Build the evidence package. Returns the directory path."""
    ev = os.path.join(out_dir, "evidence")
    os.makedirs(ev, exist_ok=True)
    logger.info("writing evidence snapshot to %s", out_dir)

    for name, content in renderings.items():
        _write(os.path.join(out_dir, name), content)

    # --- hard evidence: identifiers, not content -------------------------------
    shared, _, _ = ov.commit_overlap(upstream, suspect)
    _write(os.path.join(ev, "shared-commits.txt"),
           "# Commit SHAs present in BOTH repositories.\n"
           "# Byte-identical commit objects: content, tree, parents, author and\n"
           "# timestamps all match. Verify with: git cat-file -t <sha>\n"
           f"# count: {len(shared)}\n\n" + "\n".join(sorted(shared)))

    head_b = git.head_blobs(suspect)
    lines = ["# Files byte-identical in both repositories, by git blob id (SHA-1).",
             "# Git addresses content by hash, so an identical id means an identical",
             "# file. The id IS the evidence: no file content is reproduced here.",
             "# Verify with: git -C <repo> cat-file -p <blob-id>", ""]
    for path in report.overlap.shared_blob_paths:
        lines.append(f"{head_b.get(path, '?')}  {path}")
    _write(os.path.join(ev, "shared-files.txt"), "\n".join(lines))

    # --- the filtration step, made auditable -----------------------------------
    # Abstraction-Filtration-Comparison expects non-copyrightable and third-party
    # material to be filtered out before comparing. We do that; declaring exactly
    # what was excluded strengthens the result rather than weakening it.
    excluded = [p for p in head_b if ov.is_vendored(p)]
    _write(os.path.join(ev, "excluded.txt"),
           "# Paths excluded from the comparison, and why.\n"
           "# Vendored / third-party directories: two unrelated projects that\n"
           "# bundle the same dependency legitimately share those files, so they\n"
           "# carry no provenance signal. Declared here so the filtering can be\n"
           "# audited (Abstraction-Filtration-Comparison).\n"
           f"# vendored paths excluded: {len(excluded)}\n\n"
           + "\n".join(sorted(excluded)[:2000]))

    # --- licenses, verbatim ----------------------------------------------------
    lic_dir = os.path.join(ev, "licenses")
    for label, state, repo in (
            ("upstream-at-common-commit", report.upstream_license_at_divergence, upstream),
            ("upstream-now", report.upstream_license_now, upstream),
            ("suspect-now", report.suspect_license_now, suspect)):
        if state and state.source_file and state.commit:
            text = git.file_text_at(repo, state.commit, state.source_file)
            if text:
                _write(os.path.join(lic_dir, f"{label}.txt"),
                       f"# {state.source_file} @ {state.commit}\n"
                       f"# detected: {state.spdx_id}\n"
                       f"# full expression: {state.full_expression}\n"
                       + "-" * 70 + "\n" + text)

    # --- disputed header regions, side by side ---------------------------------
    hdr_dir = os.path.join(ev, "headers")
    up_head, su_head = git.head(upstream), git.head(suspect)
    for path in report.attribution.changed_header_paths[:50]:
        safe = path.replace("/", "__")
        for side, repo, commit in (("upstream", upstream, up_head),
                                   ("suspect", suspect, su_head)):
            lines = git.file_head_lines(repo, commit, path, _HEADER_LINES)
            if not lines:
                continue
            blobs = git.head_blobs(repo)
            body = "\n".join(f"{i:>4} | {ln}" for i, ln in enumerate(lines, 1))
            _write(os.path.join(hdr_dir, f"{safe}.{side}.txt"),
                   f"# {path}  ({side})\n"
                   f"# first {len(lines)} lines only — the disputed header region.\n"
                   f"# git blob id (SHA-1) of the WHOLE file: {blobs.get(path, '?')}\n"
                   f"# retrieve the whole file with: git -C <repo> cat-file -p <id>\n"
                   + "-" * 70 + "\n" + body)

    # --- optional: the repositories themselves ---------------------------------
    if include_source:
        src = os.path.join(out_dir, "source")
        os.makedirs(src, exist_ok=True)
        for name, repo in (("upstream", upstream), ("suspect", suspect)):
            dest = os.path.join(src, f"{name}.bundle")
            logger.info("bundling %s (this is large)", name)
            proc = subprocess.run(["git", "-C", repo, "bundle", "create", dest, "--all"],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                logger.warning("git bundle failed for %s: %s", name, proc.stderr.strip())

    _write(os.path.join(out_dir, "NOTICE.txt"), _notice(report))
    _write(os.path.join(out_dir, "README.txt"), _readme(report, include_source))
    _write(os.path.join(out_dir, "MANIFEST.json"), _manifest(report, out_dir))
    return out_dir


def write_timeline(ref: str, repo: str, out_dir: str,
                   renderings: dict[str, str]) -> str:
    """Evidence package for a single repository's licence timeline."""
    os.makedirs(out_dir, exist_ok=True)
    logger.info("writing licence timeline snapshot to %s", out_dir)
    for name, content in renderings.items():
        _write(os.path.join(out_dir, name), content)

    ev = os.path.join(out_dir, "evidence")
    os.makedirs(ev, exist_ok=True)
    _write(os.path.join(ev, "refs.txt"),
           "# Every ref in the repository at collection time, with its tip.\n"
           "# A licence state can survive only on a branch the mainline left\n"
           "# behind, so which refs existed is itself evidence.\n\n"
           + git.refs(repo))
    _write(os.path.join(ev, "head.txt"), git.head(repo))

    files = {}
    for root, _, names in os.walk(out_dir):
        for n in sorted(names):
            if n == "MANIFEST.json":
                continue
            full = os.path.join(root, n)
            files[os.path.relpath(full, out_dir)] = {
                "sha256": _sha256_file(full), "bytes": os.path.getsize(full)}
    _write(os.path.join(out_dir, "MANIFEST.json"), json.dumps({
        "case": {"repository": ref},
        "collected": {
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "by": _collector(),
            "method": "receipts licdrift --snapshot",
            "code_sha256": code_fingerprint(),
        },
        "files": files,
    }, indent=2))
    return out_dir


def _manifest(report: Report, out_dir: str) -> str:
    """SHA-256 of every preserved file, plus who collected it and when."""
    files = {}
    for root, _, names in os.walk(out_dir):
        for n in sorted(names):
            if n == "MANIFEST.json":
                continue
            full = os.path.join(root, n)
            files[os.path.relpath(full, out_dir)] = {
                "sha256": _sha256_file(full), "bytes": os.path.getsize(full)}
    c = report.custody
    return json.dumps({
        "case": {"upstream": report.upstream, "suspect": report.suspect},
        "collected": {
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "by": _collector(),
            "method": f"receipts {c.tool_version} provenance --snapshot",
            # WHICH BUILD, not just which version.
            "code_sha256": code_fingerprint(),
        },
        "analysed_state": {
            "upstream_head": c.upstream_head, "suspect_head": c.suspect_head,
            "git": c.git_version,
            "license_detector": f"{c.license_detector} {c.license_detector_version}".strip(),
        },
        "report_evidence_sha256": c.evidence_sha256,
        "files": files,
    }, indent=2)


def _notice(report: Report) -> str:
    return f"""THIRD-PARTY MATERIAL — READ BEFORE SHARING

This package preserves evidence about two repositories:

  upstream: {report.upstream}
  suspect:  {report.suspect}

It contains material authored by others, kept for evidentiary purposes and
limited to what the findings rest on:

  * licence files, verbatim (the object of the dispute);
  * the header region of files whose copyright notice changed — the disputed
    lines only, never whole files;
  * hashes and commit identifiers for everything else. No other source code is
    reproduced here: for identical files the hash IS the proof.

Keeping this locally, or handing it to a lawyer or a platform's copyright
process, is the use it was built for. PUBLISHING IT IS A DIFFERENT ACT —
posting it, committing it to a repository, or attaching it to an article
distributes someone else's code. Consider whether you need to, and take advice
if you are unsure.

receipts reports evidence. It does not determine infringement.
"""


def _readme(report: Report, include_source: bool) -> str:
    c = report.custody
    src = ("\nsource/\n"
           "  upstream.bundle, suspect.bundle — complete repositories as analysed.\n"
           "  Restore with: git clone upstream.bundle restored-upstream\n"
           if include_source else
           "\nThe repositories themselves are NOT included. Re-run with\n"
           "--snapshot-source to add git bundles, which survive the originals\n"
           "being deleted.\n")
    return f"""receipts evidence package
=========================

  upstream: {report.upstream}   HEAD {c.upstream_head}
  suspect:  {report.suspect}   HEAD {c.suspect_head}
  collected: {c.generated_at}
  tool: receipts {c.tool_version} · {c.git_version} · {c.license_detector} {c.license_detector_version}

CONTENTS
  report.txt / report.md / report.json   the findings, three renderings
  MANIFEST.json                          SHA-256 of every file here
  NOTICE.txt                             third-party material; read before sharing
  evidence/shared-commits.txt            commit SHAs present in both repositories
  evidence/shared-files.txt              byte-identical files, by content hash
  evidence/excluded.txt                  what was filtered out, and why
  evidence/licences/                     licence files, verbatim
  evidence/headers/                      disputed header regions, side by side
{src}
VERIFYING THIS PACKAGE
  Every file is hashed in MANIFEST.json. To check nothing changed since
  collection, recompute and compare:

      find . -type f ! -name MANIFEST.json -exec shasum -a 256 {{}} \\;

REPRODUCING THE ANALYSIS
  A third party with access to both repositories can re-derive every finding:

      receipts provenance <upstream> <suspect>

  The HEADs above pin the state analysed. Because these are live repositories,
  a later run may differ — that is why this snapshot exists.

WHAT THIS IS NOT
  Not examined here: whether modified source was published (GPL §3 /
  AGPL §13), network-use obligations, shipped binaries or devices, or
  permission granted outside the repository.
"""

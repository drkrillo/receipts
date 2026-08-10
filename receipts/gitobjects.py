"""Git plumbing wrappers (subprocess)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from functools import lru_cache

logger = logging.getLogger(__name__)

# Downloading a repository is the tool's basic operation and does not warrant a
# prompt --- it is read-only, into a temp directory, on public code. It does
# warrant being *seen*: a `timeline` run silently fetched 6.6 MB, and six
# held-out sets silently accumulated 108 GB. This logger is wired to stderr at
# INFO by the CLI regardless of -v, so the one thing that consumes disk and time
# always announces itself.
progress = logging.getLogger("receipts.progress")


class GitError(RuntimeError):
    pass


def _run(repo: str, *args: str) -> str:
    logger.debug("git -C %s %s", os.path.basename(repo), " ".join(args[:4]))
    # errors="replace": these commands stream file CONTENTS, which may be UTF-16,
    # latin-1 or binary. Decoding must never crash the analysis.
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, errors="replace",
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout


def git_version() -> str:
    return subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()


def prepare(src: str, workdir: str, blobless: bool = False) -> str:
    """Return a local path to a standalone clone of *src*."""
    if os.path.isdir(os.path.join(src, ".git")) or os.path.isdir(os.path.join(src, "objects")):
        logger.info("using local clone: %s", src)
        return src  # already a local standalone repo
    if os.path.isdir(src):
        # A directory that exists but holds no git history. Passing it on to
        # `git clone` produced "repository does not exist", which is false and
        # sends the reader looking for a typo in a path that is correct.
        #
        # This is not supported rather than merely unimplemented: every finding
        # this tool makes is temporal — shared commits, the licence at the
        # divergence point, states surviving only in rewritten history. A tree
        # with no history supports none of them, and silently analysing it would
        # answer a much weaker question while looking like the same report.
        raise GitError(
            f"{src} is a directory but not a git repository.\n"
            f"  receipts compares HISTORIES: shared commits, the licence at the\n"
            f"  point two repositories diverged, states that survive only in\n"
            f"  rewritten history. None of that exists in a plain directory.\n"
            f"  If this is source you obtained some other way (a tarball, a\n"
            f"  download), give it a history first — the comparison then works,\n"
            f"  with the honest limitation that it holds a single commit:\n"
            f"      git -C {src} init && git -C {src} add -A && \\\n"
            f"          git -C {src} commit -m 'imported source'")
    # Keyed by OWNER and name, never name alone. A fork normally keeps the
    # upstream's name, so `GooseMod/OpenAsar` and `liziyang168/OpenAsar` both
    # resolved to <workdir>/OpenAsar: the second call silently "reused" the
    # first clone and the tool compared a repository against itself, reporting
    # 100% shared history and identical licences. That is the single most
    # common shape of the problem this tool exists to analyse.
    parts = [p for p in src.rstrip("/").removesuffix(".git").split("/") if p]
    name = "__".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    dest = os.path.join(workdir, name)
    if os.path.isdir(dest):
        # Reuse only if it really is the same remote.
        origin = subprocess.run(["git", "-C", dest, "remote", "get-url", "origin"],
                                capture_output=True, text=True).stdout.strip()
        if origin and origin.rstrip("/").removesuffix(".git") != src.rstrip("/").removesuffix(".git"):
            raise GitError(
                f"work-dir collision: {dest} already holds {origin}, not {src}")
        # A cached clone can be blob-less because an earlier command did not
        # need file contents. Handing it to one that does is the worst kind of
        # failure: git fetches blobs one at a time over the network as the walk
        # asks for them, so the command is slow AND wrong rather than either.
        # Measured on redis: 359 s to examine 250 commits, finding nothing.
        #
        # This is a real ordering hazard, not a corner case --- `trace` clones
        # blob-less and `timeline` needs blobs, and the documented workflow runs
        # them in that order.
        if not blobless and is_partial_clone(dest):
            logger.info("cached clone of %s is blob-less and this command needs "
                        "file contents; completing it", name)
            # Drop the filter settings BEFORE refetching. Doing it the other
            # way round refetches *with* the filter still configured, so the
            # clone comes back exactly as partial as it was.
            for key in ("remote.origin.partialclonefilter",
                        "remote.origin.promisor"):
                subprocess.run(["git", "-C", dest, "config", "--unset", key],
                               capture_output=True, text=True)
            proc = subprocess.run(
                ["git", "-C", dest, "fetch", "--refetch", "--quiet", "origin"],
                capture_output=True, text=True)
            if proc.returncode != 0:
                raise GitError(
                    f"{dest} was cloned without file contents and could not be "
                    f"completed: {proc.stderr.strip()}\n"
                    f"  delete it and retry:  rm -rf {dest}")
        progress.info("using cached clone of %s", name)
        return dest
    if not os.path.isdir(workdir):
        logger.info("creating work directory: %s", workdir)
    os.makedirs(workdir, exist_ok=True)
    # Announce the cost before paying it. Running six held-out sets accumulated
    # **108 GB** of clones and filled a 460 GB disk to 100%, killing three cases
    # mid-run, and nothing ever mentioned the size. GitHub publishes it; asking
    # costs one request and turns a silent surprise into a decision.
    est = remote_size_mb(src)
    if est:
        logger.info("%s is about %.0f MB; work dir currently holds %.0f MB",
                    src, est, dir_size_mb(workdir))
        if est > 500:
            logger.warning("%s is large (~%.1f GB). Clones are kept for reuse in "
                           "%s — `receipts clean` removes them.",
                           src, est / 1024, workdir)

    # Blob-less clones carry every commit and tree but no file contents, which
    # is 4x smaller and enough for anything that only reads history or a couple
    # of licence files. It is NOT enough for derivation: `vim -> neovim` shares
    # zero commits and was found through identical blobs alone, so `overlap` and
    # `provenance` always clone in full.
    cmd = ["git", "clone", "--quiet"]
    if blobless:
        cmd.append("--filter=blob:none")
    cmd += [src, dest]
    size = f"~{est:.0f} MB" if est else "size unknown"
    progress.info("downloading %s (%s) -> %s", src, size, dest)
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"clone {src} failed: {proc.stderr.strip()}")
    progress.info("downloaded %s in %.0fs — cached for reuse; "
                  "`receipts clean` frees it", name, time.monotonic() - t0)
    return dest


def dir_size_mb(path: str) -> float:
    """Megabytes currently held under *path*, 0 if it does not exist."""
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1_048_576


@lru_cache(maxsize=256)
def remote_size_mb(src: str) -> float:
    """Megabytes GitHub reports for a repository, or 0 if it cannot be asked."""
    if "github.com" not in src:
        return 0.0
    parts = [p for p in src.rstrip("/").removesuffix(".git").split("/") if p]
    if len(parts) < 2:
        return 0.0
    slug = "/".join(parts[-2:])
    try:
        proc = subprocess.run(["gh", "api", f"repos/{slug}", "--jq", ".size"],
                              capture_output=True, text=True, timeout=30)
        return float(proc.stdout.strip()) / 1024 if proc.returncode == 0 else 0.0
    except Exception:
        return 0.0


def head(repo: str) -> str:
    return _run(repo, "rev-parse", "HEAD").strip()


def all_commits(repo: str) -> set[str]:
    out = _run(repo, "rev-list", "--all")
    return set(out.split())


def commit_date(repo: str, sha: str) -> str:
    return _run(repo, "show", "-s", "--format=%cI", sha).strip()


def earliest_commit_date(repo: str) -> str:
    """ISO date of the oldest commit in the repo (when its history begins)."""
    out = _run(repo, "log", "--all", "--reverse", "--format=%cI", "--max-parents=0")
    dates = [d for d in out.splitlines() if d.strip()]
    return min(dates) if dates else ""


def commit_at_or_before(repo: str, iso_date: str) -> str:
    """Newest commit on HEAD's history at or before *iso_date* ('' if none)."""
    if not iso_date:
        return ""
    out = _run(repo, "rev-list", "-1", f"--before={iso_date}", "HEAD")
    return out.strip()


def first_parent_log(repo: str) -> list[str]:
    """HEAD mainline, newest first, as 'SHA<space>isoDate' lines."""
    out = _run(repo, "log", "--first-parent", "--format=%H %cI", "HEAD")
    return out.splitlines()


def is_partial_clone(repo: str) -> bool:
    """True if this is a partial clone, so some objects are missing locally."""
    for key in ("remote.origin.promisor", "remote.origin.partialclonefilter"):
        proc = subprocess.run(["git", "-C", repo, "config", "--get", key],
                              capture_output=True, text=True)
        if proc.stdout.strip():
            return True
    return False


def all_blobs(repo: str) -> set[str]:
    """Every blob object id in the whole object store of this standalone repo."""
    out = _run(repo, "cat-file", "--batch-all-objects",
               "--batch-check=%(objecttype) %(objectname)")
    return {line.split()[1] for line in out.splitlines() if line.startswith("blob ")}


def head_blobs(repo: str) -> dict[str, str]:
    """path -> blob sha at HEAD."""
    return blobs_at(repo, "HEAD")


def blobs_at(repo: str, commit: str) -> dict[str, str]:
    """path -> blob sha at *commit*."""
    out = _run(repo, "ls-tree", "-r", commit, "--format=%(objectname) %(path)")
    result: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        result[path] = sha
    return result


def authors(repo: str) -> set[str]:
    out = _run(repo, "log", "--all", "--format=%an <%ae>")
    return {a.strip() for a in out.splitlines() if a.strip()}


def root_commits(repo: str) -> list[str]:
    """Root commits (parentless) on the checked-out line of history.

    `--all` here answered a different question: it counts roots on every ref the
    clone happens to hold, including contributed branches and fetched PR refs.
    It gave terraform nine roots, the newest a 2024 commit by a drive-by
    contributor, under a heading that reads as where the project came from.
    """
    out = _run(repo, "rev-list", "--max-parents=0", "HEAD")
    return out.split()


def root_identities(repo: str) -> list[dict[str, str]]:
    """Every author AND committer identity of every root commit."""
    roots = []
    for sha in root_commits(repo):
        info = _run(repo, "show", "-s", "--format=%H%x00%cI%x00%an <%ae>%x00%cn <%ce>", sha)
        commit, date, author, committer = info.strip().split("\x00")
        roots.append({"commit": commit, "date": date,
                      "author": author, "committer": committer})
    return roots


def ancestors(repo: str, commit: str) -> set[str]:
    """Every commit reachable from *commit*, itself included."""
    try:
        return set(_run(repo, "rev-list", commit).split())
    except GitError:
        return set()


def descendants(repo: str, commit: str) -> set[str]:
    """Every commit that has *commit* in its ancestry. Excludes *commit*.

    `--ancestry-path=<c>` on its own also returns c's ancestors, because the
    range is open at the bottom; `--not <c>` closes it.
    """
    try:
        return set(_run(repo, "rev-list", "--all",
                        f"--ancestry-path={commit}", "--not", commit).split())
    except GitError:
        return set()


def is_ancestor(repo: str, commit: str, of: str = "HEAD") -> bool:
    """True if *commit* is an ancestor of *of* — i.e. on that line of history."""
    proc = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor",
                           commit, of], capture_output=True, text=True)
    return proc.returncode == 0


def refs(repo: str) -> str:
    """Every ref and its tip, one per line — `<sha> <refname>`."""
    return _run(repo, "for-each-ref", "--format=%(objectname) %(refname)")


def historical_objects(repo: str) -> list[tuple[str, str]]:
    """Every (blob id, path) the repo has EVER held, in one pass."""
    out = _run(repo, "rev-list", "--objects", "--all")
    pairs = []
    for line in out.splitlines():
        oid, _, path = line.partition(" ")
        if path.strip():
            pairs.append((oid, path.strip()))
    return pairs


def batch_blob_text(repo: str, shas: list[str], limit: int = 4000) -> dict[str, str]:
    """Read many blobs in ONE `git cat-file --batch` call."""
    if not shas:
        return {}
    proc = subprocess.run(["git", "-C", repo, "cat-file", "--batch"],
                          input="\n".join(shas).encode(), capture_output=True)
    data, out, i = proc.stdout, {}, 0
    while i < len(data):
        nl = data.find(b"\n", i)
        if nl == -1:
            break
        header = data[i:nl].decode("utf-8", "replace").split()
        if len(header) < 3:
            i = nl + 1
            continue
        oid, size = header[0], int(header[2])
        body = data[nl + 1: nl + 1 + min(size, limit)]
        out[oid] = body.decode("utf-8", "replace")
        i = nl + 1 + size + 1
    return out


def blob_text(repo: str, sha: str) -> str:
    try:
        return _run(repo, "cat-file", "-p", sha)
    except GitError:
        return ""


# Root and nested both. A git pathspec matches from the root, so `LICENSE*`
# never saw `docs/text/LICENSE` — which is where nginx kept its licence for its
# whole history. The tree-based finder had no such restriction, so one report
# stated `BSD-2-Clause` at the divergence commit and, four lines below, that
# every one of the 9,208 shared commits was made with no licence file.
_LICENSE_GLOBS = [":(icase)LICENSE*", ":(icase)LICENCE*", ":(icase)COPYING*",
                  ":(icase)COPYRIGHT*", ":(icase)*/LICENSE*",
                  ":(icase)*/LICENCE*", ":(icase)*/COPYING*",
                  ":(icase)*/COPYRIGHT*"]

# Same exclusion the tree-based finder uses: a dependency manifest or a template
# is never the project's own license (see license._NOT_LICENSE).
_NOT_LICENSE_FILE = re.compile(
    r"depend|template|\.tpl$|[-_]readme|third[-_ ]?party|vendor|node_modules", re.I)

# A `licenses/` or `LICENSES/` directory is auxiliary — several licences that
# coexist, or the REUSE SPDX folder — and treating its files as the project's
# own grant produced flip-flops on elasticsearch and valkey.
_LICENSE_DIR = re.compile(r"(^|/)licen[cs]es/", re.I)


def _tracks_licence(path: str) -> bool:
    """Could this path be the project's own licence file?"""
    return bool(path) and not (_LICENSE_DIR.search(path)
                               or _NOT_LICENSE_FILE.search(path))


def license_file_history(repo: str, orphaned: bool = False
                         ) -> list[tuple[str, str, str, str]]:
    """Commits that touched a license file, oldest first."""
    # Follow the mainline (HEAD, first-parent) — NOT --all: on a big repo with
    # thousands of branches/tags, --all conflates license states across branches
    # and produces spurious flip-flops (caught on hashicorp/terraform).
    scope = ["--all", "--not", "HEAD"] if orphaned else ["--first-parent", "HEAD"]
    out = _run(repo, "log", "--reverse",
               "--format=commit %H %cI", "--name-status", *scope, "--", *_LICENSE_GLOBS)
    events: list[tuple[str, str, str, str]] = []
    cur_commit = cur_date = ""
    for line in out.splitlines():
        if line.startswith("commit "):
            _, cur_commit, cur_date = line.split(" ", 2)
        elif line and line[0] in "AMDRC" and "\t" in line:
            status, _, rest = line.partition("\t")
            paths = [p.strip() for p in rest.split("\t") if p.strip()]
            # `R096\told\tnew` is one line for what is really a delete and an
            # add, and matching only A/M/D dropped it whole. A licence file
            # renamed WITH an edit — `git mv LICENSE COPYING` plus a changed
            # copyright holder, which git reports as a single R096 — was
            # invisible to the timeline: the state simply never moved.
            if status[0] in "RC" and len(paths) == 2:
                old, new = paths
                if _tracks_licence(old):
                    events.append((cur_commit, cur_date, "D", old))
                if _tracks_licence(new):
                    events.append((cur_commit, cur_date, "M", new))
                continue
            path = paths[0] if paths else ""
            # These events say WHEN to look. WHICH file governs at that commit is
            # decided by ranking the tree's candidates, the same way a single
            # revision is read — so an extra event costs a re-read and never a
            # wrong answer.
            if not _tracks_licence(path):
                continue
            events.append((cur_commit, cur_date, status[0], path))
    return events


def file_text_at(repo: str, commit: str, path: str) -> str:
    try:
        return _run(repo, "show", f"{commit}:{path}")
    except GitError:
        return ""


def list_tree(repo: str, commit: str = "HEAD") -> list[str]:
    """Every file path in the tree at *commit*."""
    return _run(repo, "ls-tree", "-r", "--name-only", commit).splitlines()


def file_head_lines(repo: str, commit: str, path: str, n: int = 12) -> list[str]:
    """First *n* lines of a file at *commit* — enough to cover a license header."""
    text = file_text_at(repo, commit, path)
    return text.splitlines()[:n] if text else []


def count_spdx_headers(repo: str) -> int:
    # A full-tree grep on a partial clone would lazily fetch every blob at HEAD
    # (hangs on large repos, caught on terraform). Skip it there — SPDX-header
    # counting is a secondary attribution signal, not worth a full fetch.
    if is_partial_clone(repo):
        return 0
    try:
        out = _run(repo, "grep", "-rIl", "SPDX-License-Identifier", "HEAD")
        return len([l for l in out.splitlines() if l.strip()])
    except GitError:
        return 0

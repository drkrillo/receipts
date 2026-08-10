"""Find where a repository's code ended up."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from receipts import gitobjects as git

logger = logging.getLogger(__name__)

_SWH = "https://archive.softwareheritage.org/api/1"


@dataclass
class Holder:
    """A repository found to hold some of the traced repository's code."""

    name: str
    how: str                    # "history" | "archive"
    detail: str = ""
    declared_fork: bool | None = None


@dataclass
class Trace:
    repo: str
    root_commit: str = ""
    holders: list[Holder] = field(default_factory=list)
    archived: str = ""
    notes: list[str] = field(default_factory=list)

    def all_urls(self) -> list[str]:
        """Every distinct repository this trace found, in the order found."""
        seen: list[str] = []
        for holder in self.holders:
            name = holder.name.strip()
            if not name:
                continue
            url = name if name.startswith(("http://", "https://")) else (
                f"https://github.com/{name}" if "/" in name else "")
            if url and url not in seen:
                seen.append(url)
        return seen


# --------------------------------------------------------------------------- #
# credentials
# --------------------------------------------------------------------------- #

def load_env(path: str = ".env") -> None:
    """Read `KEY=value` lines into the environment, without overwriting it."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except OSError:                                          # pragma: no cover
        logger.debug("could not read %s", path)


def swh_token() -> str:
    return os.environ.get("RECEIPTS_SWH_TOKEN", "").strip()


# --------------------------------------------------------------------------- #
# GitHub — via the gh CLI, so the user's own auth and rate limit are used
# --------------------------------------------------------------------------- #

class GitHubCLIUnavailable(RuntimeError):
    """The `gh` CLI is required for tracing but is missing or not signed in."""


def gh_install_hint(reason: str) -> str:
    """Copy-pasteable instructions for THIS machine."""
    import sys
    if sys.platform == "darwin":
        install = "    brew install gh"
    elif sys.platform.startswith("linux"):
        install = ("    sudo apt install gh        # Debian/Ubuntu\n"
                   "    sudo dnf install gh        # Fedora/RHEL\n"
                   "    # or see https://github.com/cli/cli#installation")
    else:
        install = "    winget install --id GitHub.cli"
    return "\n".join([
        f"`receipts trace` needs the GitHub CLI, and {reason}.",
        "",
        "It is the only way to ask GitHub which repositories hold a given",
        "commit, which is what finds descendants that do not declare",
        "themselves forks --- and almost none of them do.",
        "",
        "Install it:", install, "",
        "Then sign in (a free account is enough):",
        "    gh auth login",
    ])


def require_gh() -> None:
    """Fail loudly if `gh` cannot answer, rather than reporting nothing found."""
    import shutil
    if not shutil.which("gh"):
        raise GitHubCLIUnavailable(gh_install_hint("it is not installed"))
    try:
        proc = subprocess.run(["gh", "auth", "status"], capture_output=True,
                              text=True, timeout=60)
    except Exception as exc:                                 # pragma: no cover
        raise GitHubCLIUnavailable(gh_install_hint(f"it could not run ({exc})"))
    if proc.returncode:
        raise GitHubCLIUnavailable(gh_install_hint("it is not signed in"))


def _gh(path: str) -> dict | list | None:
    try:
        proc = subprocess.run(["gh", "api", path], capture_output=True,
                              text=True, timeout=120)
        if proc.returncode:
            logger.debug("gh api %s failed: %s", path, proc.stderr.strip()[:120])
            return None
        return json.loads(proc.stdout)
    except Exception as exc:                                 # pragma: no cover
        logger.debug("gh api %s: %s", path, exc)
        return None


def root_commit(repo: str) -> str:
    """The repository's first commit — the probe every descendant must hold."""
    roots = git.root_commits(repo)
    return roots[0] if roots else ""


def holders_of_history(root: str) -> list[Holder]:
    """Repositories GitHub reports as containing this commit."""
    if not root:
        return []
    data = _gh(f"search/commits?q=hash:{root}&per_page=100")
    if not isinstance(data, dict):
        return []
    out = []
    for item in data.get("items", []) or []:
        repo = (item.get("repository") or {})
        name = repo.get("full_name")
        if name:
            out.append(Holder(name=name, how="history",
                              declared_fork=repo.get("fork"),
                              detail=f"holds root commit {root[:10]}"))
    logger.info("history: %d repositories hold %s", len(out), root[:10])
    return out


def _swh(path: str, token: str = "") -> tuple[int, dict | list | None]:
    req = urllib.request.Request(f"{_SWH}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, None
    except Exception as exc:                                 # pragma: no cover
        logger.debug("swh %s: %s", path, exc)
        return 0, None


def archive_status(origin_url: str, token: str = "") -> str:
    """Whether Software Heritage holds an independent copy, and since when."""
    status, _ = _swh(f"/origin/{origin_url}/get/", token)
    # A broken token must never read as an answer. Treating every non-200 as
    # "not archived" meant a stale or mistyped token reported a repository that
    # IS archived as unpreserved --- a false negative that looks like a finding,
    # which is the failure direction this project exists to avoid.
    if status in (401, 403):
        return ("UNKNOWN — the Software Heritage token was rejected "
                f"(HTTP {status}); fix or remove RECEIPTS_SWH_TOKEN and retry")
    if status == 429:
        return "UNKNOWN — Software Heritage rate limit reached; retry later"
    if status == 0:
        return "UNKNOWN — could not reach Software Heritage"
    if status == 404:
        return "not archived"
    if status != 200:
        return f"UNKNOWN — Software Heritage returned HTTP {status}"
    status, visits = _swh(f"/origin/{origin_url}/visits/", token)
    if status == 200 and isinstance(visits, list) and visits:
        full = [v for v in visits if v.get("status") == "full"]
        newest = (full or visits)[0]
        return f"archived, last full visit {newest.get('date', '')[:10]}"
    return "archived"


def trace(repo_path: str, origin_url: str = "") -> Trace:
    """Which repositories hold this one's root commit, and is it archived.

    A code search over distinctive lines was here and was removed. "This line
    of yours appears in that repository" is a similarity lead, not an object
    identity — the same category as the code matcher this project already
    dropped, and it costs minutes: GitHub allows ten code searches a minute.
    Holding the root commit is a hash comparison and answers in one call.
    """
    require_gh()
    load_env()
    token = swh_token()
    result = Trace(repo=origin_url or repo_path)

    result.root_commit = root_commit(repo_path)
    if not result.root_commit:
        result.notes.append("no root commit found; cannot search by history")
    else:
        result.holders.extend(holders_of_history(result.root_commit))

    if origin_url:
        result.archived = archive_status(origin_url, token)
        if not token:
            result.notes.append(
                "no RECEIPTS_SWH_TOKEN set: Software Heritage allows 120 "
                "requests an hour anonymously, 1200 with a token")
    return result

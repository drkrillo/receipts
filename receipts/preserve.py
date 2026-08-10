"""What Software Heritage already holds for a repository.

Read-only. Asking the archive to SAVE a repository was here and was
removed: it is public, permanent and performed on somebody else's
repository, and the archive already crawls most public forges on its
own. Whoever wants a repository archived can ask for it under their
own name at archive.softwareheritage.org.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from receipts.trace import load_env, swh_token

logger = logging.getLogger(__name__)

_SWH = "https://archive.softwareheritage.org/api/1"
_TIMEOUT = 45


@dataclass
class ArchiveStatus:
    """What Software Heritage holds for one repository."""

    url: str
    archived: bool = False
    last_visit: str = ""
    visit_status: str = ""
    snapshot: str = ""
    error: str = ""

    @property
    def swhid(self) -> str:
        """The archive's permanent identifier for this origin."""
        return f"swh:1:ori:{self.url}" if self.archived else ""


def _get(path: str) -> tuple[int, dict | list | None]:
    req = urllib.request.Request(f"{_SWH}/{path}", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:                                  # pragma: no cover
        logger.debug("software heritage unreachable: %s", exc)
        return 0, None


def _headers() -> dict[str, str]:
    load_env()
    token = swh_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def status(url: str) -> ArchiveStatus:
    """What the archive currently holds for *url*. No submission is made."""
    st = ArchiveStatus(url=url)
    quoted = urllib.parse.quote(url, safe="")
    code, data = _get(f"origin/{quoted}/get/")
    # `_get` returns 0 when the request never completed. Folding that in with
    # a 404 reports "NOT ARCHIVED" for a repository the archive was simply not
    # reachable to ask about.
    if code == 0:
        st.error = "could not reach archive.softwareheritage.org"
        return st
    if code != 200 or not isinstance(data, dict):
        return st
    st.archived = True

    code, visits = _get(f"origin/{quoted}/visits/")
    if code == 200 and isinstance(visits, list) and visits:
        # Newest first; the useful one is the newest FULL visit, because a
        # partial visit means the archive holds an incomplete copy.
        full = [v for v in visits if v.get("status") == "full"] or visits
        newest = full[0]
        st.last_visit = (newest.get("date") or "")[:19]
        st.visit_status = newest.get("status") or ""
        st.snapshot = newest.get("snapshot") or ""
    return st


@dataclass
class Preservation:
    """The archival state of every repository in one analysis."""

    results: list[ArchiveStatus] = field(default_factory=list)

    @property
    def unarchived(self) -> list[ArchiveStatus]:
        """Known NOT to be archived. A repository the archive could not be
        asked about is not one of them: "we could not ask" is not "it is
        missing"."""
        return [r for r in self.results if not r.archived and not r.error]

    def to_dict(self) -> dict:
        return {"repositories": [vars(r) | {"swhid": r.swhid} for r in self.results]}


def preserve(urls: list[str]) -> Preservation:
    """The archive's state for each repository."""
    out = Preservation()
    for url in urls:
        st = status(url)
        out.results.append(st)
    return out

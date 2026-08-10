"""One function per command. The parser lives in `cli.py`."""

from __future__ import annotations

import argparse
import json
import os
import sys

from receipts import gitobjects as git
from receipts import license as lic
from receipts import preserve as pres
from receipts import report as rep
from receipts import snapshot as snap
from receipts import terminal as term

def _snapshot_base(args: argparse.Namespace) -> str:
    """Directory the evidence package goes into. It must already exist."""
    base = args.snapshot or "."
    if not os.path.isdir(base):
        raise ValueError(f"no such directory: {base}")
    return base


def cmd_provenance(args: argparse.Namespace) -> int:
    report = rep.provenance(args.upstream, args.suspect, args.work_dir,
                            suspect_at=args.suspect_at)
    # The report always goes to the terminal. --snapshot ADDS the package; it is
    # not an alternative destination, because the text, Markdown and JSON are the
    # same analysis and the package already contains all three.
    print(term.render_report(report, colour=False if args.no_color else None))
    if args.snapshot is None:
        return 0
    path = snap.write_snapshot(
        report,
        git.prepare(args.upstream, args.work_dir),
        git.prepare(args.suspect, args.work_dir),
        snap.default_dir(_snapshot_base(args)),
        {"report.txt": term.render_report(report, colour=False),
         "report.md": rep.render_markdown(report),
         "report.json": rep.render_json(report)},
        include_source=args.snapshot_source)
    print(f"evidence package: {path}/", file=sys.stderr)
    print(f"  read {path}/README.txt first; NOTICE.txt before sharing it",
          file=sys.stderr)
    return 0


def _licdrift_markdown(ref: str, transitions) -> str:
    lines = [f"# licdrift — {ref}", ""]
    lines += ([f"- {t.date[:10]}  `{t.commit[:10]}`  "
               f"{lic.human_label(t.before)} → {lic.human_label(t.after)}  ({t.file})"
               for t in transitions]
              or ["No license file found anywhere in this history."])
    return "\n".join(lines)


def cmd_timeline(args) -> int:
    """One repository's history as events, not just licence changes."""
    from receipts import timeline as tl
    # Blob-less clones carry no file contents, and finding removed copyright
    # notices means reading diffs. On a blob-less clone git fetches each blob
    # over the network as the walk needs it: measured at 359 s for 250 commits
    # of redis, and it still found nothing. Only the licence-only path, which
    # reads a handful of licence files, can afford to skip blobs.
    repo = git.prepare(args.repo, args.work_dir, blobless=args.licence_only)
    hist = tl.history(repo, ref=args.repo, licence_only=args.licence_only)
    print(term.render_history(hist, colour=False if args.no_color else None))
    if args.snapshot is None:
        return 0
    out_dir = snap.default_dir(_snapshot_base(args))
    snap.write_timeline(args.repo, repo, out_dir, {
        "timeline.txt": term.render_history(hist, colour=False),
        "timeline.json": json.dumps(hist.to_dict(), indent=2)})
    print(f"evidence package: {out_dir}/", file=sys.stderr)
    return 0


def cmd_licdrift(args: argparse.Namespace) -> int:
    repo = git.prepare(args.repo, args.work_dir, blobless=True)
    transitions = lic.timeline(repo)
    # History the project rewrote away holds licence states the mainline no
    # longer shows. Reporting the mainline alone gave the wrong answer once.
    orphaned = lic.orphaned_states(
        repo, {t.after for t in transitions} | {t.before for t in transitions})
    print(term.render_licdrift(args.repo, transitions, orphaned,
                               colour=False if args.no_color else None))
    if args.snapshot is None:
        return 0
    out_dir = snap.default_dir(_snapshot_base(args))
    snap.write_timeline(
        args.repo, repo, out_dir,
        {"licdrift.txt": term.render_licdrift(args.repo, transitions, orphaned,
                                              colour=False),
         "licdrift.md": _licdrift_markdown(args.repo, transitions),
         "licdrift.json": json.dumps(
             {"repository": args.repo,
              "transitions": [t.__dict__ for t in transitions],
              "orphaned_states": [{"commit": c, "date": d, "spdx_id": s}
                                  for c, d, s in orphaned]}, indent=2)})
    print(f"evidence package: {out_dir}/", file=sys.stderr)
    return 0


def cmd_overlap(args: argparse.Namespace) -> int:
    """Derivation only: shared objects and the temporal anchor. No ScanCode."""
    a = git.prepare(args.upstream, args.work_dir)
    b = git.prepare(args.suspect, args.work_dir)
    print(term.render_overlap(
        args.upstream, args.suspect,
        *rep.overlap_only(a, b),
        colour=False if args.no_color else None
        )
    )
    return 0


def cmd_license(args: argparse.Namespace) -> int:
    """The licence a single repository carried at one point in its history."""
    repo = git.prepare(args.repo, args.work_dir, blobless=True)
    state = lic.find_license(repo, args.at)
    print(term.render_license(
        args.repo, args.at, state,
        colour=False if args.no_color else None
        )
    )
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """Find where this repository's code ended up."""
    from receipts import trace as tr
    # `trace` only ever reads the root commit, so file contents are dead weight.
    repo = git.prepare(args.repo, args.work_dir, blobless=True)
    origin = args.repo if args.repo.startswith(("http://", "https://")) else ""
    result = tr.trace(repo, origin_url=origin)
    if args.urls:
        # Just the URLs, one per line, so the result composes:
        # receipts trace <repo> --urls | receipts preserve -
        for url in result.all_urls():
            print(url)
        return 0
    print(term.render_trace(result, colour=False if args.no_color else None))
    return 0


def cmd_preserve(args: argparse.Namespace) -> int:
    """Report what Software Heritage already holds. Read-only."""
    repos = args.repos
    if repos == ["-"]:
        repos = [l.strip() for l in sys.stdin if l.strip()
                 and not l.startswith("#")]
        if not repos:
            print("error: no repository URLs on stdin", file=sys.stderr)
            return 2

    result = pres.preserve(repos)
    st = term.Style(term.colour_enabled(force=False if args.no_color else None))
    print(st.bold("RECEIPTS") + st.dim("  preservation — Software Heritage archive"))
    print(st.dim("=" * 72))
    print()
    for r in result.results:
        mark = st.green("archived") if r.archived else st.yellow("NOT ARCHIVED")
        print(f"  {term._short(r.url):<46} {mark}")
        if r.archived:
            print(st.dim(f"      last full visit  {r.last_visit}  ({r.visit_status})"))
            print(f"      swhid            {r.swhid}")
        if r.error:
            print(st.red(f"      error            {r.error}"))
    missing = result.unarchived
    if missing:
        print()
        print(st.yellow(f"  {len(missing)} of {len(result.results)} are not archived."))
        print(st.dim("  A deleted repository takes your evidence with it: the package\n"
                     "  keeps the hashes, but nobody can resolve them any more.\n"
                     "  archive.softwareheritage.org saves a repository on request —\n"
                     "  do it there, under your own name."))
    print()
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Delete cached clones."""
    import shutil
    size = git.dir_size_mb(args.work_dir)
    if not os.path.isdir(args.work_dir):
        print(f"nothing to clean: {args.work_dir} does not exist")
        return 0
    entries = sorted(os.listdir(args.work_dir))
    if not entries:
        print(f"already empty: {args.work_dir}")
        return 0
    if not args.yes:
        print(f"{args.work_dir}\n  {len(entries)} clones, {size:.0f} MB")
        for name in entries[:15]:
            print(f"    {name}")
        if len(entries) > 15:
            print(f"    ... and {len(entries) - 15} more")
        print("\nre-run with --yes to delete them")
        return 0
    shutil.rmtree(args.work_dir, ignore_errors=True)
    print(f"removed {len(entries)} clones, freed {size:.0f} MB from {args.work_dir}")
    return 0



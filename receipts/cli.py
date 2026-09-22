"""receipts CLI (stdlib argparse — no deps)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile

from receipts import __version__
from receipts import gitobjects as git
from receipts import license as lic
from receipts import terminal as term
from receipts.trace import GitHubCLIUnavailable
from receipts.commands import (  # noqa: F401
    _snapshot_base,
    cmd_clean, cmd_license, cmd_licdrift, cmd_overlap,
    cmd_timeline,
    cmd_preserve, cmd_provenance, cmd_trace,
)


class _LogFormatter(logging.Formatter):
    """Compact, greppable log lines; colour only on a terminal."""

    _COLOURS = {"WARNING": "33", "ERROR": "31", "CRITICAL": "31", "DEBUG": "2"}

    def __init__(self, colour: bool) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        self.colour = colour

    def format(self, record: logging.LogRecord) -> str:
        record.name = record.name.replace("receipts.", "")
        line = super().format(record)
        code = self._COLOURS.get(record.levelname)
        return f"\033[{code}m{line}\033[0m" if self.colour and code else line


def _setup_logging(verbosity: int, quiet: bool) -> None:
    """Configure logging. Only the CLI does this — library code just gets loggers."""
    level = logging.ERROR if quiet else (
        logging.WARNING if verbosity == 0 else
        logging.INFO if verbosity == 1 else logging.DEBUG)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_LogFormatter(term.colour_enabled(sys.stderr)))
    root = logging.getLogger("receipts")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False

    # Downloads are always announced. They are what costs disk and time, and a
    # run that quietly fetched gigabytes is the one complaint this tool has
    # actually earned. `-q` still silences it; `-v` adds everything else.
    prog = logging.getLogger("receipts.progress")
    prog.setLevel(logging.ERROR if quiet else min(level, logging.INFO))


def _default_workdir() -> str:
    return os.path.join(tempfile.gettempdir(), "receipts-clones")




def _global_flags() -> argparse.ArgumentParser:
    """Flags shared by every subcommand, so `-v` works before OR after it."""
    g = argparse.ArgumentParser(add_help=False)
    g.add_argument("-v", "--verbose", action="count", default=0,
                   help="log each step to stderr (-vv for git/scancode detail)")
    g.add_argument("-q", "--quiet", action="store_true",
                   help="suppress warnings; errors only")
    return g


def _add_output_flags(p: argparse.ArgumentParser, *, source: bool = False) -> None:
    """Output flags."""
    p.add_argument("--snapshot", nargs="?", const="", metavar="DIR",
                   help="ALSO write an evidence package (report in all formats + "
                        "hashes + licences + disputed headers) into DIR, as "
                        "receipts-evidence-<timestamp>/. DIR must exist; "
                        "default: the current directory")
    if source:
        p.add_argument("--snapshot-source", action="store_true",
                       help="also bundle the full repositories (large; survives deletion)")
    p.add_argument("--no-color", action="store_true",
                   help="disable colour (also honoured: NO_COLOR, non-TTY output)")


_DESCRIPTION = """\
Code license provenance & lineage forensics.

Given two repositories, show whether one derives from the other, what each was
licensed under when they diverged, and whether copyright notices survived.
Output is evidence, not a legal determination."""

_EPILOG = """\
examples:
  # is this repo derived from mine, and was the license respected?
  receipts provenance https://github.com/me/project https://github.com/them/fork

  # same, watching each step (cloning and scanning are slow)
  receipts provenance ./upstream ./suspect -v

  # how has one repo's license changed over its history?
  receipts licdrift https://github.com/hashicorp/terraform

  # same, plus a sealed evidence package you can archive or send
  receipts provenance ./a ./b --snapshot
  receipts provenance ./a ./b --snapshot ~/casos

output:
  The report always goes to the terminal. --snapshot additionally writes
  receipts-evidence-<timestamp>/ holding the report as text, Markdown and JSON,
  the shared identifiers, the licence texts, and a SHA-256 manifest.

requires:
  git, and ScanCode Toolkit for license detection. If ScanCode or its libmagic
  is missing, receipts stops and prints the exact install command for your OS."""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="receipts", description=_DESCRIPTION, epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"receipts {__version__}")
    g = _global_flags()
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser(
        "provenance", parents=[g],
        help="compare two repos (upstream vs suspect)",
        description=(
            "Compare a suspect repository against the upstream it may derive from:\n"
            "shared git objects, the license each carried at the divergence point,\n"
            "and whether copyright notices survived."),
        epilog="example:\n  receipts provenance ./upstream ./suspect -v",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pv.add_argument("upstream", help="the original repository (git URL or local clone)")
    pv.add_argument("suspect", help="the repository being checked against it")
    _add_output_flags(pv, source=True)
    pv.add_argument("--suspect-at", metavar="REV", default="HEAD",
                    help="examine the suspect at this revision instead of HEAD. "
                         "A notice removed and later restored leaves no trace at "
                         "HEAD: WinObjC stripped 7 Cocotron notices at its first "
                         "public commit and restored them two days later.")
    pv.add_argument("--work-dir", default=_default_workdir(),
                    help="where to clone repos (default: a temp dir; reused across runs)")
    pv.set_defaults(func=cmd_provenance)

    pt2 = sub.add_parser(
        "timeline", parents=[g],
        help="what happened to ONE repository, as dated events",
        description=(
            "One repository's history, told as events rather than as git.\n\n"
            "  2022-03-14  repository created\n"
            "  2023-08-11  licence MIT -> GPL-3.0-only\n"
            "  2023-08-14  LICENSE deleted                    by <author>\n"
            "  2023-08-15  12 copyright notices removed       by <author>\n"
            "  2024-01-02  licence MIT exists ONLY off the main line\n\n"
            "Every line carries a commit id and the command that checks it, so\n"
            "a reader who does not trust the tool can confirm each one.\n\n"
            "This is where an investigation STARTS: it needs no suspect, and\n"
            "what it surfaces is what tells you whom to compare against."),
        epilog=("examples:\n"
                "  receipts timeline https://github.com/hashicorp/vault\n"
                "  receipts timeline ./my-repo --licence-only"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pt2.add_argument("repo", help="repository to inspect (git URL or local clone)")
    pt2.add_argument("--licence-only", action="store_true",
                     help="licence changes only — what `licdrift` reported")
    _add_output_flags(pt2)
    pt2.add_argument("--work-dir", default=_default_workdir(),
                     help="where to clone repos (default: a temp dir; reused across runs)")
    pt2.set_defaults(func=cmd_timeline)

    # `licdrift` is what `timeline --licence-only` does. Kept because it is named
    # in every document written before the split, and removing a command someone
    # has in a script is a worse cost than one extra line of help.
    pl = sub.add_parser(
        "licdrift", parents=[g],
        help="licence changes only (alias for `timeline --licence-only`)",
        description=(
            "Every change to one repository's licence, with date and commit.\n"
            "`timeline` is the fuller account; this is the licence rows alone."),
        epilog="example:\n  receipts licdrift https://github.com/redis/redis",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pl.add_argument("repo", help="repository to inspect (git URL or local clone)")
    _add_output_flags(pl)
    pl.add_argument("--work-dir", default=_default_workdir(),
                    help="where to clone repos (default: a temp dir; reused across runs)")
    pl.set_defaults(func=cmd_licdrift)

    # ----- per-capability commands -------------------------------------------
    # The pipeline is separable, so the CLI exposes it that way: a researcher can
    # study one layer at a time, and `overlap`, the only capability that is
    # exact rather than scored, runs on git alone in seconds with no ScanCode.
    po = sub.add_parser(
        "overlap", parents=[g],
        help="derivation only: shared objects and the divergence anchor (no ScanCode)",
        description=(
            "Shared commits and byte-identical files between two repositories,\n"
            "and the commit standing for the moment they parted.\n\n"
            "Needs only git. Use it to screen many candidates cheaply before\n"
            "running the full `provenance` on the few that matter."),
        epilog="example:\n  receipts overlap ./upstream ./suspect",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    po.add_argument("upstream")
    po.add_argument("suspect")
    po.add_argument("--no-color", action="store_true")
    po.add_argument("--work-dir", default=_default_workdir())
    po.set_defaults(func=cmd_overlap, needs_scancode=False, snapshot=None)

    pc = sub.add_parser(
        "license", parents=[g],
        help="the licence one repository carried at one revision",
        description=("Detect the licence of a repository at any revision.\n"
                     "`--at` takes any commit; the default is HEAD."),
        epilog="example:\n  receipts license ./repo --at 9bd9abb77",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pc.add_argument("repo")
    pc.add_argument("--at", metavar="REV", default="HEAD",
                    help="revision to read (default HEAD)")
    pc.add_argument("--no-color", action="store_true")
    pc.add_argument("--work-dir", default=_default_workdir())
    pc.set_defaults(func=cmd_license, snapshot=None)


    pt = sub.add_parser(
        "trace", parents=[g],
        help="find repositories that hold this repository's code (no ScanCode)",
        description=(
            "Who else has this code.\n\n"
            "Searches GitHub for repositories holding this repository's ROOT\n"
            "commit, which every descendant holds by construction. One API\n"
            "call, no cloning, and every hit is a hash comparison.\n\n"
            "GitHub's own `fork` flag does not answer this: the root commit of\n"
            "gogs/gogs is held by 189 repositories and NONE of them is declared\n"
            "a fork."),
        epilog=("example:\n"
                "  receipts trace https://github.com/me/project\n\n"
                "needs the `gh` CLI, authenticated. A Software Heritage token in\n"
                ".env (RECEIPTS_SWH_TOKEN) additionally reports whether an\n"
                "independent archived copy exists."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pt.add_argument("repo", help="the repository to trace (git URL or local clone)")
    pt.add_argument("--urls", action="store_true",
                    help="print only the repository URLs, one per line, for piping")
    pt.add_argument("--no-color", action="store_true")
    pt.add_argument("--work-dir", default=_default_workdir())
    pt.set_defaults(func=cmd_trace, needs_scancode=False, snapshot=None)

    pn = sub.add_parser(
        "clean", parents=[g],
        help="delete cached clones and report how much they hold",
        description=(
            "Clones are cached so re-running a pair is instant, and the cache\n"
            "has no limit. Six held-out sets accumulated 108 GB and filled a\n"
            "460 GB disk to 100%, killing three runs. Nothing announced it.\n\n"
            "Lists what is held; deletes only with --yes."),
        epilog="example:\n  receipts clean\n  receipts clean --yes",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pn.add_argument("--yes", action="store_true", help="actually delete")
    pn.add_argument("--work-dir", default=_default_workdir())
    pn.set_defaults(func=cmd_clean, needs_scancode=False, snapshot=None)

    pp = sub.add_parser(
        "preserve", parents=[g],
        help="check archival in Software Heritage (read-only)",
        description=(
            "Checks each repository against the Software Heritage archive\n"
            "(archive.softwareheritage.org), the public long-term archive of\n"
            "source code, and reports what it already holds.\n\n"
            "Repositories vanish, and they vanish precisely when someone moves\n"
            "to act on them: four in this project's case set are gone. An\n"
            "evidence package cites commit ids, and once the repository is gone\n"
            "nobody can resolve them.\n\n"
            "Pass every repository in the analysis. The one that matters most is\n"
            "usually the SUSPECT's: your own is the one you control, and is often\n"
            "already archived.\n\n"
            "Read-only. Asking the archive to SAVE a repository is public,\n"
            "permanent, and an act on somebody else's repository: do that at\n"
            "archive.softwareheritage.org, under your own name."),
        epilog=("examples:\n"
                "  receipts preserve https://github.com/me/mine https://github.com/them/copy\n"
                "  receipts trace https://github.com/me/mine --urls | receipts preserve -"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pp.add_argument("repos", nargs="+",
                    help="repository URLs, or - to read them from stdin")
    pp.add_argument("--no-color", action="store_true")
    pp.set_defaults(func=cmd_preserve, needs_scancode=False, snapshot=None,
                    work_dir=_default_workdir())
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose, args.quiet)
    try:
        # ScanCode is required only by the commands that read licences. The
        # one capability that is exact rather than scored, derivation, needs
        # nothing but git, and gating it behind a 300 MB dependency that also
        # wants libmagic and is untested on Windows and Linux made the strongest
        # part of the tool the hardest to run.
        if getattr(args, "needs_scancode", True):
            lic.require_scancode()
        # Same reason: a mistyped --snapshot directory must be caught now, not
        # after cloning and scanning. Printing a full report and only then
        # refusing to save it wastes the run and buries the error.
        if getattr(args, "snapshot", None) is not None:
            _snapshot_base(args)
        return args.func(args)
    except GitHubCLIUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    except lic.ScanCodeUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except git.GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

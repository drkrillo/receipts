# Architecture

Roughly 4,300 lines of Python across 14 modules. No framework, no plugin system,
no database. Every analysis reads a standalone git clone on disk.

## Shape

Three layers, and nothing skips one:

* **`cli.py`** builds the argument parser and is the **only** place that
  configures logging and turns exceptions into exit codes. Nothing else prints
  errors.
* **`commands.py`** has one function per subcommand. It wires arguments to the
  analysis modules and holds no logic of its own.
* Everything else is analysis, and none of it knows the CLI exists.

## Modules

| module | responsibility |
|---|---|
| `gitobjects.py` | every `git` call in the project, via subprocess. Clones, blobs, refs, ancestry. |
| `overlap.py` | derivation: shared commits and blobs, the divergence anchor, and the filtration that decides what counts as evidence |
| `license.py` | which licence a repository carried at a given commit |
| `scancode.py` | running ScanCode, and telling the user how to install it |
| `spdx.py` | SPDX vocabulary: identifiers, categories, expression parsing |
| `timeline.py` | one repository's history as dated events |
| `trace.py` | discovery — who else holds this code (GitHub CLI) |
| `preserve.py` | Software Heritage: what it already holds (read-only) |
| `report.py` | assembling findings into a report object |
| `terminal.py` | rendering to a terminal, plain text and ANSI |
| `snapshot.py` | the evidence package: files, hashes, manifest |
| `models.py` | dataclasses, no logic |

## What each command does

| command | flow |
|---|---|
| `overlap` | `gitobjects.prepare` → `report.overlap_only` → `terminal.render_overlap` |
| `license` | `gitobjects.prepare` → `license.find_license` → `terminal.render_license` |
| `timeline` | `gitobjects.prepare` → `timeline` → `terminal.render_history` |
| `licdrift` | `license.timeline` + `license.orphaned_states` → `terminal.render_licdrift` |
| `provenance` | `report.provenance` → `terminal.render_report`, plus `snapshot.write_snapshot` with `--snapshot` |
| `trace` | GitHub CLI searches → `terminal.render_trace` |
| `preserve` | `preserve.preserve` → Software Heritage API |
| `clean` | `gitobjects.dir_size_mb` over the clone cache |

`provenance` is the only command that composes several analyses: derivation from
`overlap`, and licence state from `license` at the anchor commit.

## Decisions worth knowing

**The anchor is a commit, not a date.** The licence is read at the newest commit
both repositories share *that is also on the upstream's mainline*, verified with
`git merge-base --is-ancestor`. Reading at `HEAD` answers a question nobody
asked, and reading at a date exculpates whoever rewrote history.

**Filtration decides the result more than matching does.** Vendored trees,
machine-generated files, API declarations and third-party notices are removed
before anything is compared. Most of the defects in [BENCHMARK.md](BENCHMARK.md)
were filtration failures, not detection failures.

**ScanCode is the only detector.** There is no fallback. A second detector that
disagrees silently is worse than a hard failure, so the tool stops and prints the
install command instead.

**Code similarity is not here.** A matcher was removed after measurement: it
produced claims that always needed a human to corroborate, which is a different
category from everything else the tool reports. See BENCHMARK.md.

**Clones are read, never GitHub's API.** File contents always come from a
standalone clone on disk, so an analysis can be re-run offline and a reviewer can
reproduce it with plain `git`.

## Dependencies

Three, and each replaced something written here first:

| package | replaces |
|---|---|
| ScanCode Toolkit | a hand-written licence detector |
| license-expression | a hand-written SPDX parser |
| Pygments | hand-written comment-block detection |

Two libraries were evaluated and rejected: **PyDriller** (0.04 s versus 32 s on
licence-file history, and no concept of unreachable commits) and **flict**
(scored 4/6 against 2/6, and returns `[]` for proprietary licences — a silent
failure in the dangerous direction).

## Tests

`pytest`, 36 tests, no network and no clones. They cover the decision functions —
filtration, grant selection, holder matching — not the git plumbing, which is
exercised by running the tool.

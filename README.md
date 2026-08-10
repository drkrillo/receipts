<div align="center">

# receipts

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Code license provenance and lineage forensics. Given two repositories, it shows whether one derives from the other, what each was licensed under **at the point they diverged**, and whether copyright notices survived.

**Output is evidence, not a legal conclusion.**

</div>

---

## What to rely on

| capability | status |
|---|---|
| **Derivation** — did code move | precision 1.00, recall 1.00 across six held-out sets. This is the part to trust. |
| **Licence at divergence** | right on the shapes that used to break it, with review flags where it is unsure |
| **Notices on identical files** | reported only where the compared repository provably held the byte-identical file and the notice changed |

Numbers, cases and the defects each one exposed: [BENCHMARK.md](BENCHMARK.md).

Two things no other tool does. The licence is read **at the commit where the histories diverged**, not at `HEAD` — a repository's licence today says nothing about what it offered when somebody forked it. And `timeline` recovers licence states that survive *only* in history a project rewrote away, which the obvious method reports wrongly.

Every report ends with **CHECK THESE FIRST**, naming the findings in that run whose shape this tool has been wrong about before.

---

## 1. Requirements

- **git**
- **Python 3.10+**
- 64-bit Linux, macOS, or Windows 10+

Three packages install automatically: **ScanCode Toolkit** for licence detection (required — there is no fallback), **license-expression** for SPDX parsing and **Pygments** for locating notice blocks. ScanCode brings ~70 transitive packages, which is most of the install time.

## 2. Install

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .
```

That creates the `receipts` command. Without the install the package imports but there is no executable — use `python -m receipts.cli …` instead, which always works.

**macOS only.** ScanCode's bundled `libmagic` has no Apple Silicon build, so install one or it aborts with `NoMagicLibError`:

```bash
brew install libmagic
```

**Debian/Ubuntu only.** A few system packages first:

```bash
sudo apt install python3-dev bzip2 xz-utils zlib1g libxml2-dev libxslt1-dev libpopt0
```

Windows needs nothing extra. Then verify:

```bash
scancode --version
```

Should print `ScanCode version: 32.x`. If ScanCode or `libmagic` is missing, `receipts` stops and prints the exact command for your OS — it never runs with a half-working detector.

> Verified end to end on macOS (Apple Silicon) with ScanCode 32.5.0 and Python 3.13. The Linux and Windows steps follow ScanCode's documented requirements but have not been run here.

---

## 3. Two investigations

Knowing which one you are in is most of using the tool.

**One repository — what happened here?** Start here. It needs no suspect, and what it surfaces is what tells you whom to compare against.

| command | question |
|---|---|
| [`timeline`](#31-timeline) | its whole history: licences, deleted notices, rewritten history |
| [`license`](#32-license) | what licence it carried at one exact moment |
| [`trace`](#33-trace) | who else holds this code |
| [`preserve`](#34-preserve) | will the evidence still exist next month |

**Two repositories — did code move, and under what terms?** Run once the left column gave you a suspect.

| command | question |
|---|---|
| [`overlap`](#35-overlap) | did code move? (git only, seconds) |
| [`provenance`](#36-provenance) | …and what licence governed it when |
| [`clean`](#38-clean) | how much disk the clones hold |

### The order to run them in

```bash
# 1. preserve FIRST — repositories vanish exactly when someone acts on them
receipts trace https://github.com/me/mine --urls | receipts preserve -

# 2. what happened to my own repository?
receipts timeline https://github.com/me/mine

# 3. who else has it?  (this is where a suspect comes from)
receipts trace https://github.com/me/mine

# 4. did code actually move?  seconds, no ScanCode
receipts overlap https://github.com/me/mine https://github.com/them/copy

# 5. only if step 4 said yes: under what licence, and the package
receipts provenance https://github.com/me/mine https://github.com/them/copy --snapshot
```

Steps 1–3 are collection and examination; 4–5 are analysis and reporting — the phases NIST SP 800-86 sets out. Preservation comes first on purpose: four repositories in this project's own case set were deleted while being looked at, and a package citing commit ids nobody can resolve is not evidence.

Every command takes `-v`, `-q`, `--no-color` and `--work-dir`, and accepts a **git URL or a local clone**. Only `license`, `licdrift` and `provenance` need ScanCode.

---

### 3.1 `timeline`

**Where an investigation starts.** One repository's history as dated events, each carrying a commit id and the command that checks it.

```bash
receipts timeline https://github.com/hashicorp/vault
```

```
2015-02-24  ·  repository created
               ed3069f01d  by Armon Dadgar
2015-02-24  !  licence NONE → MPL-2.0
               verify: git show ed3069f01d:LICENSE
2023-08-10  !  licence MPL-2.0 → BUSL-1.1
2023-08-18  !! licence acter-psl-1.0 exists ONLY off the main line
               the project rewrote its history, so this grant is invisible on
               the current main branch. A grant is not revoked by a later
               relicensing
               verify: git merge-base --is-ancestor db47400901 HEAD ; echo $?
```

| event | what it means |
|---|---|
| repository created | the earliest root commit, and its author |
| licence changed | with the commit and the file |
| **licence exists only off the main line** | the project rewrote its history — the obvious method reports the wrong licence here |
| `LICENSE` / `NOTICE` deleted | with the commit and who did it |
| **copyright notices removed** | how many, in which commit, **by whom** |

The last one is why this command exists. It names the commit that removed a notice and who authored it, which anyone confirms with `git show` — no similarity matching, no judgement.

Run on `redis/redis` it surfaces, unprompted:

```
2024-03-20  0b34396924  505 copyright notices removed  by Pieter Cailliau
            first: "* Copyright (c) 2018, Salvatore Sanfilippo <antirez...>"
```

That commit is `Change license from BSD-3 to dual RSALv2+SSPLv1`, 166 files. It is a fact, not an accusation — Redis Ltd presumably holds those rights through contributor agreements. Facts are what the tool is for.

`--licence-only` prints just the licence rows, which is what `licdrift` does and it is kept as an alias.

> Needs a **full clone** unless `--licence-only`: finding removed notices means reading diffs. On a blob-less clone git fetches each blob over the network as the walk needs it — measured at 359 s for 250 commits of redis, finding nothing.

### 3.2 `license`

```bash
receipts license https://github.com/redis/redis
receipts license https://github.com/redis/redis --at 4cae99e785   # before the change
```

`--at` is the point of the command.

### 3.3 `trace`

Finds repositories holding your code **without you having to know they exist**. GitHub's `fork` flag misses almost everything:

```
gogs/gogs             root commit in 189 repositories, 0 declared forks
prusa3d/PrusaSlicer                117 repositories, 0 declared forks
```

```bash
receipts trace https://github.com/gogs/gogs
```

One search, and every hit is a hash comparison: the repository contains your **root commit**, which every descendant holds by construction. One API call, no cloning.

A second search over distinctive lines was here and was removed. "This line of yours appears in that repository" is a similarity lead, not object identity — the same category as the code matcher this project already dropped — and GitHub allows ten code searches a minute, so it turned a one-second command into a multi-minute one.

| flag | effect |
|---|---|
| `--urls` | print only URLs, one per line, so the result pipes into `preserve` |

**Requires the [GitHub CLI](https://cli.github.com/)** signed in with `gh auth login`. If `gh` is missing the command stops and says so — it never reports "nothing found" because it could not ask.

#### The Software Heritage token

Optional. Without one you get 120 requests an hour; with one, 1,200. `trace` and `preserve` both use it.

Get it from [archive.softwareheritage.org](https://archive.softwareheritage.org/) → your profile → **API tokens**, then put it in a `.env` file beside the repository:

```bash
cp .env.example .env
```

```
RECEIPTS_SWH_TOKEN=your-token-here
```

`.env` is in `.gitignore` and is read at the start of every command that needs it. Nothing else in the file is used, and the token is never written into a report or an evidence package. An environment variable of the same name wins over the file, which is how you would pass it in CI:

```bash
RECEIPTS_SWH_TOKEN=… receipts trace https://github.com/org/repo
```

To rotate or revoke it, delete the token in your Software Heritage profile and replace the line. If the token is wrong the command says so and names it — it does not fall back to anonymous access silently.

### 3.4 `preserve`

Asks **[Software Heritage](https://archive.softwareheritage.org/)** what it already holds for each repository. Read-only.

```bash
receipts preserve https://github.com/me/mine https://github.com/them/copy
receipts trace https://github.com/me/mine --urls | receipts preserve -
```

The one that matters is usually **the suspect's**: your own repository is the one you control, and is often already archived. Four repositories in this project's case set disappeared mid-investigation, which is why this runs at the start and not at the end.

An archived repository reports its **SWHID** — a permanent identifier you can cite in a report, and which still resolves after the repository is deleted.

This command cannot write to the archive. A `--submit` flag was here and was removed: it asked for a public, permanent copy of a repository that is usually somebody else's, and Software Heritage already crawls the public forges on its own. If a repository needs saving, ask for it at [archive.softwareheritage.org](https://archive.softwareheritage.org/save/) under your own name.

### 3.5 `overlap`

Derivation only: shared commits, byte-identical files, and where the histories diverged. **No ScanCode, so no 300 MB dependency.** This is the part validated at precision 1.00 / recall 1.00.

```bash
receipts overlap https://github.com/prusa3d/PrusaSlicer \
                 https://github.com/bambulab/BambuStudio
```

It finds derivation even with **zero shared commits** — `vim → neovim` and `openssl → boringssl` share none, because the mirror postdates the fork, and both are found through identical files alone.

### 3.6 `provenance`

```bash
receipts provenance https://github.com/me/mine https://github.com/them/copy
receipts provenance ./upstream ./suspect --snapshot ~/cases
```

Derivation, the licence each side carried at the point they diverged, and whether notices survived on files the compared repository provably held.

`--snapshot [DIR]` additionally writes `receipts-evidence-<timestamp>/`:

```
report.txt / report.md / report.json    the findings, three renderings
MANIFEST.json                           SHA-256 of every file, plus the build hash
evidence/shared-commits.txt             commit ids present in both
evidence/shared-files.txt               byte-identical files, by content hash
evidence/excluded.txt                   what was filtered out, and why
evidence/licenses/                      the licence files, verbatim
evidence/headers/                       disputed header regions, side by side
NOTICE.txt                              third-party material — read before sharing
```

DIR must already exist. Add `--snapshot-source` for full `git bundle`s of both repositories, which survive the originals being deleted.


### 3.8 `clean`

Clones are cached so re-running a pair is instant, and the cache has no limit. Six held-out sets accumulated **108 GB** and filled a 460 GB disk, killing three runs. Now every clone reports its size first, and repositories over 500 MB warn.

```bash
receipts clean          # list what is held
receipts clean --yes    # delete it
```

---

## Code similarity is out of scope

This tool reports what git objects and licence files say. Whether two files are
*similar* is a different kind of claim: it needs a human to open both and decide,
and a tool that mixes the two makes the certain parts look as arguable as the
uncertain ones.

A matcher used to live here and was removed — the measurements and the reasoning
are in [BENCHMARK.md](BENCHMARK.md). If you want it, run a dedicated detector
against the same two clones:

```bash
receipts overlap <origin> <compared> -v        # prints where both clones live
npx @dodona/dolos run -f web -l <language> <dir-a> <dir-b>
```

[Dolos](https://dolos.ugent.be/) is MIT-licensed; [JPlag](https://jplag.github.io/)
is the other maintained option. Their output is a starting point for reading, not
evidence.

## Notes

- First run on large repositories is slow: full history clone, plus ~5 s per distinct licence text scanned (cached within a run).
- Analysis reads standalone clones only, never GitHub's web API for file contents.
- If `scancode` is not on `PATH`: `export RECEIPTS_SCANCODE=/path/to/scancode` (Windows: `set RECEIPTS_SCANCODE=C:\path\to\scancode.exe`).
- Colour is automatic on a terminal and off everywhere else. Disable with `--no-color` or `NO_COLOR=1`.
- `-v` / `-vv` log progress to stderr; `-q` errors only.
- Development: `pip install -e ".[dev]"`, then `pytest`.
- Architecture and how each command works: [ARCHITECTURE.md](ARCHITECTURE.md).

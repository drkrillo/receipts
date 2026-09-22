<div align="center">

# receipts

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Code license provenance and lineage forensics. Given two repositories, it shows whether one derives from the other, what each was licensed under **at the point they diverged**, and whether copyright notices survived.

**Output is evidence, not a legal conclusion.**

</div>

---

## What it does that other tools do not

The licence is read **at the commit where the histories diverged**, not at `HEAD`. A repository's licence today says nothing about what it offered when somebody forked it.

`timeline` recovers licence states that survive *only* in history a project rewrote away, which the obvious method reports wrongly.

Every report ends with **CHECK THESE FIRST**, naming the findings in that run whose shape this tool has been wrong about before.

| capability | status |
|---|---|
| **Derivation**: did code move | Set intersection over git object ids. Two repositories either share commit and blob hashes or they do not: no score, no threshold. Correct on every pair across seven declared sets. |
| **Licence at divergence** | right on the shapes that used to break it, with review flags where it is unsure |
| **Notices on identical files** | reported only where the compared repository provably held the byte-identical file and the notice changed |

Cases, numbers, and the defects each one exposed: [BENCHMARK.md](BENCHMARK.md).

---

## Install

Needs **git** and **Python 3.10+**, on 64-bit Linux, macOS or Windows 10+.

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .
```

ScanCode Toolkit installs with it and brings ~70 transitive packages, which is most of the install time. There is no fallback detector.

**macOS**: ScanCode's bundled `libmagic` has no Apple Silicon build. Run `brew install libmagic` or it aborts with `NoMagicLibError`.

**Debian/Ubuntu**: `sudo apt install python3-dev bzip2 xz-utils zlib1g libxml2-dev libxslt1-dev libpopt0`

Windows needs nothing extra. Verify with `scancode --version`, which should print `32.x`. If ScanCode or `libmagic` is missing, `receipts` stops and prints the exact command for your OS. It never runs with a half-working detector.

> Verified end to end on macOS (Apple Silicon) with ScanCode 32.5.0 and Python 3.13. The Linux and Windows steps follow ScanCode's documented requirements but have not been run here.

---

## The two investigations

Knowing which one you are in is most of using the tool.

**One repository: what happened here?** Needs no suspect. What it surfaces is what tells you whom to compare against.

| command | question |
|---|---|
| `timeline` | its whole history: licences, deleted notices, rewritten history |
| `license` | what licence it carried at one exact moment (`--at <commit>`) |
| `trace` | who else holds this code |
| `preserve` | will the evidence still exist next month |

**Two repositories: did code move, and under what terms?** Run once the left column gave you a suspect.

| command | question |
|---|---|
| `overlap` | did code move? git only, seconds |
| `provenance` | and what licence governed it when |
| `clean` | how much disk the clones hold |

### The order to run them in

```bash
# 1. preserve FIRST: repositories vanish exactly when someone acts on them
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

Steps 1 to 3 are collection and examination, 4 and 5 analysis and reporting: the phases NIST SP 800-86 sets out. Preservation comes first on purpose. Four repositories in this project's own case set were deleted while being looked at, and a package citing commit ids nobody can resolve is not evidence.

Every command takes `-v`, `-q`, `--no-color` and `--work-dir`, and accepts a git URL or a local clone. Only `license`, `licdrift` and `provenance` need ScanCode. `receipts <command> --help` has the flags.

---

## What a run looks like

`timeline` on `redis/redis` surfaces this unprompted:

```
2024-03-20  0b34396924  505 copyright notices removed  by Pieter Cailliau
            first: "* Copyright (c) 2018, Salvatore Sanfilippo <antirez...>"
```

That commit is `Change license from BSD-3 to dual RSALv2+SSPLv1`, 166 files. It is a fact, not an accusation: Redis Ltd presumably holds those rights through contributor agreements. Facts are what the tool is for. It names the commit and the author, which anyone confirms with `git show`. No similarity matching, no judgement.

`trace` finds holders GitHub does not declare:

```
gogs/gogs             root commit in 189 repositories, 0 declared forks
prusa3d/PrusaSlicer                117 repositories, 0 declared forks
```

Every hit is a hash comparison: the repository contains your **root commit**, which every descendant holds by construction. One API call, no cloning. Requires the [GitHub CLI](https://cli.github.com/) signed in with `gh auth login`. If `gh` is missing it stops and says so, rather than reporting "nothing found" because it could not ask.

`overlap` finds derivation even with **zero shared commits**. `vim` to `neovim` and `openssl` to `boringssl` share none, because the mirror postdates the fork, and both are found through identical files alone.

`provenance --snapshot DIR` writes an evidence package: the report in three renderings, the shared commits and files, the licence texts verbatim, the disputed header regions, and a `MANIFEST.json` carrying a SHA-256 of every file. Add `--snapshot-source` for full `git bundle`s, which survive the originals being deleted.

---

## Code similarity is out of scope

This tool reports what git objects and licence files say. Whether two files are *similar* is a different kind of claim: it needs a human to open both and decide, and a tool that mixes the two makes the certain parts look as arguable as the uncertain ones.

A matcher lived here and was removed. The measurements and the reasoning are in [BENCHMARK.md](BENCHMARK.md). If you want it, point a dedicated detector at the same two clones:

```bash
receipts overlap <origin> <compared> -v        # prints where both clones live
npx @dodona/dolos run -f web -l <language> <dir-a> <dir-b>
```

[Dolos](https://dolos.ugent.be/) and [JPlag](https://jplag.github.io/) are the maintained options. Their output is a starting point for reading, not evidence.

## Notes

- **Software Heritage token**: optional, and it raises the rate limit from 120 to 1,200 requests an hour for `trace` and `preserve`. Put `RECEIPTS_SWH_TOKEN=…` in a `.env` beside the repository (`cp .env.example .env`) or in the environment, which wins over the file. It is never written into a report, and a wrong token is reported rather than silently falling back to anonymous access.
- `preserve` is read-only. It asks Software Heritage what it already holds and reports the **SWHID**, which still resolves after a repository is deleted. To have something archived, ask at [archive.softwareheritage.org/save](https://archive.softwareheritage.org/save/) under your own name.
- `timeline` needs a full clone unless `--licence-only`, which is also available as `licdrift`: finding removed notices means reading diffs.
- First run on large repositories is slow: full history clone, plus ~5 s per distinct licence text scanned, cached within a run.
- Clones are cached with no size limit. Six held-out sets accumulated 108 GB and filled a 460 GB disk. `receipts clean` lists what is held, `--yes` deletes it.
- Analysis reads standalone clones only, never GitHub's web API for file contents.
- If `scancode` is not on `PATH`: `export RECEIPTS_SCANCODE=/path/to/scancode` (Windows: `set RECEIPTS_SCANCODE=C:\path\to\scancode.exe`).
- Colour is automatic on a terminal and off everywhere else. Disable with `--no-color` or `NO_COLOR=1`.
- Development: `pip install -e ".[dev]"`, then `pytest`.
- Architecture and how each command works: [ARCHITECTURE.md](ARCHITECTURE.md).

# Benchmark

What was measured, on which cases, and what failed. Every case here is a public
repository pair whose relationship is documented outside this project.

## Method

A case is declared before the repositories are cloned: the expected answer comes
from the public record — a company's own announcement, a remediation commit, a
licence change everyone reported. Then the tool runs once. Fixing the tool and
re-running the same case turns it into tuning data, so a spent case is never
counted again.

Seven sets have been run this way.

## Results

| capability | result | measured on |
|---|---|---|
| Derivation (did code move) | precision 1.00, recall 1.00 | seven sets, correct in every case |
| Licence at the divergence point | 0.47 → 0.67, then 6/6, then 3/4 | improved after the selection bug; the one failure in set #7 is below |

The numbers for sets #1–#6 predate the changes set #7 caused and have not been
re-measured against them. They are a record of what was true when they were
taken, not a claim about the tool today.

## Set #7 — four pairs, declared 2026-08-09 before any clone

Chosen for three shapes the first six sets never contained, and one case
expected to fail in a specific way.

| pair | what it tests | declared cells | outcome |
|---|---|---|---|
| `akka/akka-core` → `apache/pekko` | a BSL whose text names Apache-2.0 as its own Change License | 4 | **4/4** |
| `hashicorp/vault` → `openbao/openbao` | the same relicensing shape as terraform, on a different commit graph | 5 | **failed**, below |
| `puppetlabs/puppet` → `OpenVoxProject/openvox` | a fork with **no** licence change — the negative control | 5 | **5/5** |
| `nginx/nginx` → `freenginx/nginx` | two independent Mercurial→git conversions | 4 | **4/4** |

Three of the four also broke something.

### The failure: vault → openbao

Declared: licence at divergence MPL-2.0, zero shared commits made under BUSL.
Reported: **BUSL-1.1**, and 1,606 commits under BUSL and a misdetection of it.

OpenBao's clone kept HashiCorp's other branches. 2,195 of the Vault commits it
holds are in refs its own HEAD never reaches, and the anchor was chosen from
that set — landing on a commit from 2024-03-18, seven months past the
relicensing. The report then said OpenBao's code came from Vault under BUSL-1.1.

It did not. Zero post-BUSL Vault mainline commits are ancestors of OpenBao's
HEAD:

```bash
git -C vault rev-list --first-parent HEAD --since=2023-08-10 | sort > busl
git -C openbao rev-list HEAD | sort | comm -12 busl -   # empty
```

The mainline test was applied to the origin and never to the compared side.
Fixed: the anchor must be reachable from both HEADs, and the era table counts
only commits the compared repository's own line reaches. The anchor moved to
`c4198a32d5` (2023-05-26) and the table to a single row — 17,927 commits, all
MPL-2.0.

This is the most consequential defect the project has found. It produced a
false statement about a named company, in the one cell the tool exists to fill.

### What the other three broke

| what broke | case |
|---|---|
| A vendored dependency's licence read as the project's own — `COPYING.protobuf` at the root put 544 commits under a licence Akka never adopted | akka → pekko |
| A licence file outside the root was invisible: nginx has kept its at `docs/text/LICENSE` since 2004, and one report stated `BSD-2-Clause` at the divergence commit and, four lines below, that all 9,208 shared commits were unlicensed | nginx → freenginx |
| A licence file renamed **with** an edit produced no event at all — git reports `R096 old new` on one line and the parser matched only `A`, `M`, `D` | found while testing, confirmed on the same case |

### Where the declaration was wrong and the tool was right

Worth recording, because it cuts against the author rather than the tool.

* nginx → freenginx was declared as an expected **negative**: two independent
  hg→git conversions should not agree on commit ids. They do. The pair shares
  9,208 commits and diverges at `4bef3c3367`, 2024-02-14 — the day of the fork.
* puppet → openvox was declared as having one licence transition ever. It has
  three: Puppet was GPL-2.0 from 2005 and moved to Apache-2.0 in 2011, with
  Puppet 2.7. The tool found it; `git show 3dde838ac9:LICENSE` confirms it.

All four pairs are now spent.

Both rest on the same kind of statement: two git objects have the same hash, or a
licence file at a named commit says what it says. Neither can be wrong about what
it reports.

## The code-similarity feature, and why it was removed

The tool used to include a matcher (copydetect, winnowing) that looked for files
carrying the upstream's code under someone else's copyright notice. It was
measured against the only file-level ground truth available — Microsoft's commit
`9bd9abb77 "[WinObjC] Restore original licenses"`, which names 28 `.m`/`.mm`
files whose notices it restored:

| configuration | recall | precision |
|---|---|---|
| exact line matching (written here first) | 0.24 | 0.86 |
| copydetect, as shipped | 0.50 | 0.35 |
| copydetect, after tuning the line filter | 0.68 | 0.37 |

Tuning found a real bug: a 25-character minimum line length was discarding
enough of each file that small ones had nothing left to fingerprint. Lowering it
to 10 raised recall from 0.50 to 0.68 and precision slightly with it.

It was removed anyway. At 0.37 precision, two of every three files it named were
not in the remediation commit, and no threshold fixed that. Mature detectors
exist — JPlag, Dolos, NiCad all report 86–96% precision on clone benchmarks — but
they solve a different problem: "these two files are similar" is not "this notice
was wrongly removed", and every one of them still produces a claim a person has
to corroborate by opening the file.

That is the category this tool does not carry. Anyone who wants code similarity
can point Dolos or JPlag at the same two clones; the recipe is in the README.

One caveat worth recording, because it cuts the other way: several of the
"false positives" share hundreds of tokens with the upstream. `Frameworks/limbo/
NSString.mm` shares 879. Microsoft may simply not have restored every notice, in
which case part of that 0.37 is an incomplete ground truth rather than a bad
detector. Nothing available proves which.

## Defects the validation found

The point of the sets is that each one broke something. These are the ones worth
recording.

| what broke | case that exposed it |
|---|---|
| Anchor not verified on the upstream's mainline, so the party that deleted a licence was exculpated | a fork whose history had been re-rooted |
| `proprietary-license` treated as noise | Mapbox, reported as `BSD-3-Clause AND MIT` |
| Hand-written restrictiveness ranking | `Vim` outranked `Apache-2.0`; replaced with ScanCode's categories |
| A vendored jQuery counted as evidence of derivation | gitea + synapse, 0 shared commits, still called derived |
| A `Change License:` tag won by position | Vault, reported as MPL-2.0 instead of BUSL-1.1 |
| Tie-break by match length instead of confidence | Audacity, reported as `GPL-1.0-or-later` |
| `X WITH Y` pruned as an exception | MySQL's actual grant discarded |
| Project-level credit ignored | would have accused gitea, which credits gogs on line 2 of its LICENSE |
| A third-party library treated as the upstream's code | HIDAPI reported against Bambu Lab |
| Corporate succession read as appropriation | 32 false findings in Synapse: matrix.org → OpenMarket → Matrix.org Foundation → New Vector, all one project |
| Inherited removal blamed on the wrong party | Valkey blamed for a notice Redis removed in 2019, five years before Valkey existed |

One fix was implemented, measured and reverted: promoting any directory holding
several licensed bundles catches vendored trees, but in BambuStudio that
directory is `src/`, and the files examined dropped from 711 to 44.

## A case newer than the six sets

Redis relicensed twice: BSD-3-Clause to a dual RSALv2 + SSPLv1 in March 2024,
then to a **tri-licence** in May 2025. The second one is a shape none of the six
sets contain, because the file states the choice in prose:

> your choice of: (a) the Redis Source Available License v2 (RSALv2); or (b) the
> Server Side Public License v1 (SSPLv1); or (c) the GNU Affero General Public
> License v3 (AGPLv3)

`timeline` reads the whole arc correctly, including the commit that deletes
`COPYING` and creates `LICENSE.txt` on the same day. `license` reports:

```
governing licence   AGPL-3.0-only
! 9 licence texts spanning 3 categories; the headline is the most restrictive
  OBLIGATION, which may not be what the project declares
```

The flag is right and the tool does not fail silently, which is the behaviour
this document claims. But the headline names one licence where the truth is a
three-way choice: prose `or` is not parsed as an SPDX `OR`, so a recipient
entitled to pick RSALv2 reads AGPL instead. Recorded as a limit, not fixed —
fixing it against this case would make it tuning data.

## What is not validated

* **Attribution recall** outside WinObjC. One remediation commit is the only
  file-level ground truth this project has.
* **`orphaned_states`** has one confirmed positive in the project's whole
  history. Everything else is silence, which is a precision result and no recall
  result at all.
* **Licence state** at 0.67 is not good enough to report without the review
  flags the tool raises alongside it.

## Empirical notes

Two things the sets showed that are worth keeping:

Of roughly fifteen real fork relationships across the six sets, GitHub declares
**one**. `gogs/gogs`'s root commit appears in 189 repositories and GitHub reports
zero forks. Fork metadata is not evidence of anything.

Four repositories in the case set disappeared mid-investigation. That is why
`preserve` exists and why it runs at the start rather than at the end.

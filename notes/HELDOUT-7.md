# Held-out set #7 — declared 2026-08-09, before any clone

Four pairs. The expected answer for each comes from the public record and is
written here **before the tool runs**. Fixing the tool against any of these and
re-running turns that case into tuning data, so it would be spent.

Chosen so that three shapes the first six sets never contained are covered, and
one case is expected to FAIL in a specific way.

---

## 7.1 `akka/akka-core` → `apache/pekko`

**Record.** Lightbend announced on 2022-09-07 that Akka would move from
Apache-2.0 to BSL 1.1 from version 2.7.0. Apache Pekko is a fork of the Akka
2.6.x line, taken before the change took effect. Akka's BSL carries a Change
Date after which each release reverts to Apache-2.0.

**Expected**

| cell | answer |
|---|---|
| derivation | yes, substantial shared history |
| origin at divergence | Apache-2.0 |
| origin now | BUSL-1.1 |
| compared now | Apache-2.0 |
| eras | Apache-2.0 for effectively all shared commits; **zero under BUSL** |

**Why this case.** Akka's BSL text names Apache-2.0 as its Change License. A
`Change License:` line winning by position is a defect this project has already
had once, on Vault, where it reported MPL-2.0 instead of BUSL-1.1. If `origin
now` comes back Apache-2.0 here, the fix did not generalise. The repository was
also renamed (`akka/akka` → `akka/akka-core`), which is worth seeing survive.

---

## 7.2 `hashicorp/vault` → `openbao/openbao`

**Record.** HashiCorp relicensed Vault from MPL-2.0 to BUSL-1.1 on 2023-08-10.
OpenBao forked the last MPL-licensed code and is hosted by the Linux Foundation.

**Expected**

| cell | answer |
|---|---|
| derivation | yes |
| origin at divergence | MPL-2.0 |
| origin now | BUSL-1.1 |
| compared now | MPL-2.0 |
| eras | MPL-2.0 for the shared commits; **zero under BUSL** |

**Why this case.** The same relicensing shape as terraform/opentofu on a
different project. Terraform was in set #2 and its era table was the one that
happened to come out clean because its mainline is nearly linear. This asks
whether the era logic generalises or whether it fitted one graph.

---

## 7.3 `puppetlabs/puppet` → `OpenVoxProject/openvox`

**Record.** Perforce moved Puppet development to internal repositories in late
2024 and stopped publishing community packages. Vox Pupuli forked the
Apache-2.0 code as OpenVox; first release 2025-01-21. **No licence changed.**

**Expected**

| cell | answer |
|---|---|
| derivation | yes |
| origin at divergence | Apache-2.0 |
| origin now | Apache-2.0 |
| compared now | Apache-2.0 |
| compared now vs divergence | same |
| eras | a single Apache-2.0 row |
| licdrift | one transition (NONE → Apache-2.0), nothing since |

**Why this case.** The negative control. Every other case in every set involves
a relicensing, so every one of them rewards a tool that finds licence events.
This one punishes it. A fork with no licence change must produce no licence
finding.

---

## 7.4 `nginx/nginx` → `freenginx/nginx`

**Record.** Maxim Dounin forked nginx on 2024-02-14 after a dispute with F5.
Both are BSD-2-Clause. Both GitHub repositories are **mirrors of separate
Mercurial repositories** — `nginx.org/hg` and `freenginx.org/hg` — converted to
git independently.

**Expected**

| cell | answer |
|---|---|
| shared commits | **0** — independent hg→git conversions do not produce the same commit ids |
| shared blobs | some; file contents that never changed are identical |
| derivation | must NOT be claimed from history |
| the review flag | must fire, saying identical files with no shared history |

**Why this case.** It is a fork the whole industry watched, and the method this
tool rests on cannot see it. The right answer is to say so. A tool that reports
"no derivation" plainly, and a tool that reports derivation with confidence,
are distinguishable only on a case like this one — and the failure mode being
measured is the one that matters: claiming more than the evidence carries.

If the two conversions happen to agree on commit ids, that is worth knowing
too, and this becomes a positive case instead.

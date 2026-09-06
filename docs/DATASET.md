# Corpus and holdout protocol

## What is ready now

`data/manifests/dev.json` pins **30 authentic public SAT Competition main-track
instances** from 2024/2025, acquired via the [Global Benchmark Database
(GBD)](https://benchmark-database.de/). It records SHA-256 of both each downloaded
XZ archive and the uncompressed DIMACS CNF, original filename(s), source URL,
author, competition tracks, GBD hash, isomorphism hashes, and result metadata.
The CNFs themselves are ignored by Git. Download and verify them with:

```sh
python3 -m sat_eval.corpus fetch
python3 -m sat_eval.corpus verify
```

The source metadata labels `sat`, `unsat`, and `unknown` are informative, **not
correctness oracles**. Every reported answer must pass independent certificate
verification against the original CNF, even when a public label agrees.

The starter set is for quick, bounded development. It is not a representative
competition, not a hidden test, and not evidence of state-of-the-art performance.
Some competition examples remain difficult even when their files are small.
It comprises 30 family labels (29 conservative lineage keys), with 15 published
SAT labels, 13 UNSAT, and 2 unknown. The initial download was 46,100,708 bytes
(44 MiB), expanding to 915,256,827 bytes (873 MiB). One hypertree-decomposition
instance is 550 MiB uncompressed; account for parsing and proof volume when
choosing local iteration limits.

## Expansion to 300 development instances

`data/manifests/dev-catalog.json` contains **300 public acquisition candidates**.
It is a catalog, not a hash-locked evaluation manifest. The remaining 270 CNFs
have not been downloaded or silently counted as a complete development corpus.
The operator can expand before freezing an experiment:

```sh
python3 -m sat_eval.corpus fetch \
  --manifest data/manifests/dev-catalog.json \
  --output-manifest data/manifests/dev-expanded.json \
  --limit 300 --max-download-mib 512 --max-unpacked-mib 2048
```

These are cumulative budgets per invocation. The command **stops rather than
skips** when a budget is exceeded; it saves a locked partial manifest after each
successful file. Increase budgets explicitly and rerun to resume. Existing files
are reused only after SHA-256 verification. To reuse the starter downloads for a
new expansion manifest, first copy `dev.json` to `dev-expanded.json`; otherwise
the fetcher intentionally refuses an existing file with no known SHA-256 in the
selected input/output manifest. Do this during operator setup, not mid-run.

Default acquisition uses HTTPS, a fixed host, streaming downloads, and a cap on
uncompressed output. Repeated acquisition from a **locked** manifest requires
the pinned archive and uncompressed hashes to match. First acquisition of an
unpinned catalog entry records the content obtained from that HTTPS source; it
is not independent cryptographic attestation by the competition organizers.

## How public instances were selected

The selection rule is deterministic (`20260906` seed), implemented in
`sat_eval/corpus.py`, and recorded in the catalog:

1. Read the 400-row main-track metadata pages for 2024 and 2025.
2. Exclude exact/isomorphic copies whose retrieved metadata lists `main_2026`.
   This does **not** make all 2026 instances valid holdouts: related families and
   generators must still be excluded, and later metadata can change.
3. Deduplicate GBD exact hashes, `isohash`, and `isohash2`.
4. Reserve family hash bucket 0 of 3; expose only the other family buckets.
5. Seed-order examples within families, then round-robin families to 300. The
   first 30 form the starter set. No solver runtimes or candidate results were
   used to select these instances, and no size-based filtering was applied.

The initial catalog covers 75 GBD family labels. These labels are imperfect:
`circuit-equialence-checking` is normalized to the correctly spelled name;
obvious parity and hardware-BMC aliases share a `lineage` exclusion key. Raw
family names remain in `source.gbd_family`. Most lineage keys are still only
GBD-family proxies, **not independently audited generator identities**. A
curator must review generator implementations, source applications, authors,
renamings, and related parameterizations before a generalization claim.

Regenerating the catalog consults live metadata and may change the dataset.
Do not do it during an optimization run. Freeze the exact manifest hashes,
hardware, budgets, and solver baseline before the first candidate measurement.

## Hidden evaluation is intentionally not in this repository

The proposed design remains **100 sealed validation instances and 300 final
instances**; those datasets have **not** been fabricated or acquired here.
Never store their inputs or manifests on an optimization machine the agent can
read. A `.gitignore`, an obscure pathname, or a sibling directory is not a
security boundary. Use a separate evaluator host/account with no agent access.

The evaluation curator should prepare:

- Validation: 100 application-derived instances from whole withheld
  families/generator lineages, at most three aggregate feedback rounds.
- Final: 150 entirely withheld public-family instances plus 150 new
  application-derived inputs produced after submission freezes. Include both
  SAT and UNSAT and enough feasible/nontrivial problems to avoid an all-timeout
  test. Freeze family weights and difficulty-selection rules before examining
  candidate performance.
- New application inputs: hardware-verification circuits/design revisions,
  planning domains/problems, combinatorial encodings, and cryptographic
  constructions with provenance and private generation inputs. A fresh seed for
  a familiar generator is not a new problem family or proof of no pretraining
  contamination. Do not count synthetic smoke cases as this dataset.
- Independent correctness stress tests, plus second-checker tests where
  available, kept separate from performance scoring.

Maintain a separate hidden manifest in the same schema, with `split` set to
`validation` or `final`, `curator_lineage_review` describing the manual audit,
and each instance containing `source.provenance`. On the evaluator host:

```sh
python3 -m sat_eval.corpus import \
  --input-manifest /srv/sat-private/final-input.json \
  --output-manifest /srv/sat-private/final.json \
  --root /srv/sat-private/cnfs \
  --development-manifest data/manifests/dev-catalog.json
```

Import computes SHA-256 and rejects matching development family/lineage keys,
GBD/isomorphism hashes, duplicate contents, unsafe paths, and holdout files
inside the repository. Pass the **full frozen development manifest** if all 300
were acquired, so exact-byte checks cover those files too. This structural
check cannot detect dishonestly relabeled or semantically related instances;
it supplements, not replaces, curator review. Also compare validation and final
with each other (including every lineage and hash), not only development.

Record a separate `weights`/analysis plan before evaluating. The shipped starter
uses unweighted per-instance PAR-2; a larger catalog naturally has uneven family
counts. Always publish by-family scores in addition to aggregate PAR-2.

## Smoke tests and licensing

`python3 -m sat_eval.corpus smoke` writes six tiny exact-known DIMACS examples
under `data/smoke`. They test parsing/certificates and infrastructure only. Unit
tests use temporary synthetic inputs and require no network or competition data.

This repository does **not** relicense benchmark contents. GBD metadata does not
provide a uniform license grant for the original instances. The manifests link
to original submitters/proceedings; fetch for this research workflow and consult
the original rights holders before redistribution or other uses. Third-party
CNFs and archives are not committed to GitHub.

Primary provenance:

- [SAT Competition 2025 downloads](https://satcompetition.github.io/2025/downloads.html)
- [GBD 2024 main-track metadata](https://benchmark-database.de/?context=cnf&track=main_2024)
- [GBD 2025 main-track metadata](https://benchmark-database.de/?context=cnf&track=main_2025)
- [GBD paper, SAT 2024](https://doi.org/10.4230/LIPIcs.SAT.2024.18)
- [SAT Competition 2026 compilation script](https://satcompetition.github.io/2026/downloads/benchmark-compilation-script.tar.xz)
  (the organizers also deduplicate by isomorphism hash and sample by family or
  author; this repository uses its own explicitly described development split).

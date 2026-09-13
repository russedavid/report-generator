# Context selection and reference-release recovery

September 12, 2026 (America/Chicago). Keep the existing top-two retrieval default. The experimental primary-plus-dependencies policy reduced input tokens but dropped necessary context on cooling and multi-topic notes.

## What was compared

The baseline selects the top two eligible BM25 passages. The candidate selects the first passage plus its declared supporting passages, within the same count and character limits. Dependency lookup enforces the same model, revision, owner, and revocation conditions as ordinary search. It withholds an incomplete dependency set when a required passage is unavailable or the complete set exceeds its budget.

Dependency relationships are source metadata, not case-ID exceptions in ranking code. The cooling-offset passage refers to warming thresholds supplied by the fan-threshold passage. Both methods receive the same metadata; the baseline does not use dependencies. The public application's selection policy was not changed.

The [protocol and frozen fixtures](../evals/context_selection/protocol.md) were committed before the comparison. The original 20 retrieval cases remain development data. Ten additional synthetic challenge cases cover five fictional source families; their results were not used to revise the candidate. They are a bounded challenge, not a representative field dataset or independent human assessment.

## Retrieval results

| Set / policy | Complete cases | Selected-passage precision | Relevant-passage recall | Selected characters |
|---|---:|---:|---:|---:|
| Existing development / top two | 20/20 | 0.708 | 1.000 | 3,491 |
| Existing development / primary + dependencies | 20/20 | 1.000 | 1.000 | 2,259 |
| Synthetic challenge / top two | 8/10 | 1.000 | 1.000 | 580 |
| Synthetic challenge / primary + dependencies | 9/10 | 1.000 | 0.900 | 409 |

Precision is relevant returned passages divided by returned passages, averaged over answerable cases; it does not use a fixed denominator of two. Recall uses the expected relevant set. Completeness also requires appropriate abstention and no applicability/access violations. These dimensions remain separate.

The candidate prevents an incomplete procedure from being returned when its required supporting material is inaccessible or revoked. However, it loses one independent topic from a two-topic query. The higher 9/10 total is therefore insufficient to justify replacing the baseline: it exchanges two improvements for a regression on a different task.

The full report inputs expose another limitation. A short cooling query ranks the cooling passage first, which brings in its required warming thresholds. A longer maintenance note ranks the warming passage first. A one-way dependency cannot recover the missing cooling context in that direction. Short query benchmarks therefore missed a behavior that appeared with the application's actual source-packet query.

## Live report comparison

Four synthetic Raspberry Pi scenarios ran twice with each policy, reversing arm order across rounds. These source families were already used in development. Both methods used the actual Groq report request builder, full source-record serialization, validator, model, prompt, schema, temperature and output limit. All requests were frozen before inference.

| Measure | Top two | Primary + dependencies |
|---|---:|---:|
| HTTP 200 | 8/8 | 8/8 |
| Structurally accepted | 8/8 | 8/8 |
| Four structured field checks passed | 8/8 | 8/8 |
| Required references available | 8/8 | 4/8 |
| Median HTTP round-trip | 1.223 s | 1.164 s |
| Reported input tokens | 22,460 | 19,218 |
| Reported output tokens | 2,547 | 2,635 |

The candidate used 14.4% fewer input tokens. The median paired latency difference was 0.084 seconds faster, but two repetitions over four scenarios do not establish a general speed advantage. The timings exclude retrieval, UI work and the deliberate 65-second request spacing. No monetary cost is inferred from free-tier access.

Assistant review found the generated prose faithful to the packets supplied in all 16 comparison responses. That is different from having enough relevant context. The candidate omitted the cooling passage and the USB-current passage in both rounds of those cases. One baseline report also omitted reference-derived prose even though both references were available; the existing report viewer can display saved reference passages separately. Availability is not proof that the model used a reference.

The first provisional review labels conflated context loss with unfaithful prose. The initial labels were preserved, and the final review separates report faithfulness from objective context coverage. No model output or source-coverage failure was changed by that correction. These are assistant judgments, not human-calibrated accuracy.

## A successful response can carry the wrong references

An isolated release exercise used three phases: baseline, deliberately corrupted applicability metadata, and restoration of the same baseline snapshot. The corrupted version made Pi 5 fan references eligible for a Pi 4 note. Evaluation used the unchanged original reference authority rather than trusting the candidate's modified metadata.

| Phase | HTTP | Structure and field checks | Reference applicability |
|---|---|---|---|
| Baseline | 200 | Pass | Pass; no Pi 5 references selected |
| Corrupted applicability | 200 | Pass | Fail; two Pi 5 references selected for Pi 4 |
| Recovered baseline | 200 | Pass | Pass; original reference selection restored |

The model did **not** repeat the wrong-model thresholds as Pi 4 facts in the corrupted run. Its prose remained conservative. The failure was incorrect reference selection, which could also surface through the viewer's reference fallback. This is not evidence that the model hallucinated.

The staging observer checks objective outcomes and only restores a configuration when the failed trace belongs to the currently active release. A late failure from another release cannot overwrite newer work. In this run, the observer restored the matching baseline pointer and rebuilt its index in 2.622 ms. It was started after the regression response already existed, so this is **not** a measured detection-latency or production MTTR result. Its event journal records the timing and the triggering trace.

The subsequent verification report used the exact original request and source hashes. All earlier report files remained unchanged. The producer also has a cleanup rollback in a `finally` block. No public deployment, live database, user upload, or application preference was modified.

## Run identity and reproduction

Local run: `evals/runs/20260912-context-recovery-v1`. It contains 19 real Groq responses: 16 comparisons and three release phases. Every request returned HTTP 200 and passed structural and field checks; five failed the separate context/applicability conditions. All 19 received explicitly assistant-authored prose reviews.

Generation code was frozen at `3cbe2cc`, with hashes and source snapshots recorded per file. Runtime files remained identical through the run. Python, SQLite, request settings and release identities are retained; the environment used Pydantic 2.13.5, HTTPX 0.28.1 and FastHTML 0.14.13. Review/monitoring additions are separate from the frozen generator.

Offline preparation, with a fresh output path:

```sh
python -m evals.context_recovery --prepare --output /tmp/frontline-context-prepared
```

Live execution makes at most 19 report calls and respects the observed request-spacing rule:

```sh
python -m evals.context_recovery --credentials .env.hosting.local \
  --output evals/runs/context-recovery-new
```

In another terminal, the optional observer can restore **only the isolated study staging configuration**:

```sh
python -m evals.context_watch --run evals/runs/context-recovery-new --restore-staging
```

Render a read-only summary and open the existing trace-review interface:

```sh
python -m evals.context_report --run evals/runs/context-recovery-new
python -m evals.review_app --run evals/runs/context-recovery-new \
  --dataset-snapshot study-cases.json --port 5004
```

`report.html` provides per-trace links and expandable source/output details. The review server exposes the full request, selected references and release identity, and keeps human labels in a separate journal. Raw responses, generated reports, staging snapshots and review journals stay outside Git. The committed files are code, synthetic input fixtures, this aggregate account and reproduction instructions.

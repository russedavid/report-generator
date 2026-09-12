# Context selection and release recovery

Protocol fixed September 12, 2026, before the new comparison runs.

The existing retrieval comparison found unnecessary second passages (precision at two: 0.708). The fan-cooling example also needs its separately documented warming thresholds. This study asks whether selecting one ranked passage with its declared dependencies reduces irrelevant context without losing necessary evidence.

## Comparison

- Baseline: existing BM25 top-two selection with current model, revision, access and revocation filtering.
- Candidate: first ranked passage plus its declared supporting passages, with the same two-passage and character budgets. Dependencies must independently pass eligibility checks. If the complete dependency set cannot fit or is unavailable, withhold that set; do not silently present an incomplete primary passage.
- Both arms use the same passages, dependency metadata, report instructions, model, schema, decoding settings and source records. Only selection policy changes.
- The dependency relationship is authored source metadata, separate from ranking code. The existing cooling passage refers to warming thresholds supplied by the fan-threshold passage. No case IDs or expected answers enter the selector.
- Keep the public application's top-two default throughout the study. A candidate is not a production improvement until relevant quality evidence supports it.

## Data and interpretation

Run the original 20 retrieval development cases and a separate, frozen synthetic challenge set with different fictional source families. The challenge set includes independent multi-topic queries, unavailable dependencies, and permissions/revocation cases. Its sources, expected IDs and family identifiers are recorded before results are examined. Do not tune to those results and still call them an untouched assessment. These are assistant-authored fixtures, not human labels or a representative field population.

For report generation, use four explicit synthetic Raspberry Pi scenarios (warming, cooling, thermal behavior and multiple topics), two policies, and two rounds, with arm order reversed across rounds. Their underlying manufacturer sources were already used in development. They are not held-out manufacturer data. Preserve the distinction between report-field checks, retrieved-source completeness and provisional prose review.

## Recovery exercise

Create immutable release snapshots that identify code, model/request settings, instructions, corpus, dependencies and selection policy. Verify snapshot integrity before use. In an isolated staging directory, switch from the known baseline to an intentionally corrupted applicability revision, record the retrieved sources and report response, then restore the exact baseline snapshot. Expected applicability comes from an unchanged evaluator copy of the original documents, never from the corrupted candidate metadata.

A successful HTTP response is not a quality pass. Record structural status, applicability, source coverage and report fields separately. The exercise concerns staged reference-data recovery; it does not measure production downtime or claim that an AI model necessarily follows an invalid reference. Preserve original inputs and generated reports across the pointer change.

## Execution limits and decision

Reuse the actual report request builder, source serialization and validator. Freeze all requests before inference. At most 19 report calls are planned: 16 comparison calls and three staging/recovery calls. Space Groq requests at least 65 seconds apart, retain failures and rate headers, and stop on provider/transport errors without regenerating completed responses. Do not print or persist credentials. No live user data, audio capture or production deployment is required.

Report precision, recall, coverage failures, unavailable dependencies, selected characters, request tokens when supplied, measured duration and original critiques. Compare paired differences over the same cases; the small repeated sample cannot establish a general latency advantage. Any expected-source loss is a reason to retain the baseline for general use. A narrower opt-in policy can remain experimental, with that limitation explicit.

The review method follows the existing [Hamel Husain / Shreya Shankar protocol](../../docs/evaluation-method.md): investigate observed errors and preserve disagreements. Assistant verdicts are provisional; no human calibration is claimed.

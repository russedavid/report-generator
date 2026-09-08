# Report evaluation

This directory provides five synthetic development cases and a runner for the legacy report function or the new evidence-backed report component. The cases are diagnostic examples, not a representative field dataset or an independent accuracy benchmark.

Run commands from the repository root. Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required; `uv.lock` pins the evaluation dependencies.

## Offline checks

```sh
uv run --project evals python -m unittest discover -s tests -p 'test_*.py' -v
```

The tests make no model calls. They cover state consistency, unknown versus none, citation membership, identifier preservation, reference-answer isolation, and request/response recording. The saved identifier regression in `tests/fixtures` came from an assistant-authored synthetic case and contains no real field data.

Prepare requests without starting a model:

```sh
uv run --project evals python -m evals.run_reports --prepare
uv run --project evals python -m evals.run_reports --candidate --prepare
```

## Local model runs

Use an Ollama version supporting the chosen model. Download the model once, then start the server with cloud features disabled. Qwen3.8-27B was exercised with Ollama 0.33.3; its Q4_K_M package is approximately 18 GB.

```sh
ollama pull qwen3.8:27b
OLLAMA_NO_CLOUD=1 OLLAMA_HOST=127.0.0.1:11434 OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_NUM_PARALLEL=1 ollama serve
```

In a separate terminal, compare the components:

```sh
uv run --project evals python -m evals.run_reports --provider ollama --model qwen3.8:27b --timeout-seconds 180
uv run --project evals python -m evals.run_reports --candidate --provider ollama --model qwen3.8:27b --timeout-seconds 180
```

If the Ollama desktop app is already serving, use that instance with equivalent settings or stop it before starting another server. Model downloads and local compute are explicit operations; the runner neither installs a model nor starts a server.

Each invocation creates a new directory under `evals/runs/`, which Git ignores. It records source-only inputs, prompts, actual requests, raw responses, parser/validator results, model/runtime details, timings, usage where available, and implementation snapshots. Existing run directories are never overwritten. `--case FL-005` limits a run to one case; `--output PATH` selects a new output directory.

Only source packets enter model requests. Case titles, reference interpretations, and prohibited-claim lists remain outside the prompt. The legacy arm executes the existing `generate_maintenance_report` function through a captured HTTP interface, without starting the app or running upstream entity extraction, audio, retrieval, or database code. The Ollama provider substitutes the model and endpoint; it does not measure the original OpenAI deployment.

## Review

Read [the cases](reference-cases.md) and [the report contract](../docs/report-contract.md). Record JSON/validation failures separately from semantic judgments. Check that sources actually support statements, missing information stays unknown, disagreements remain visible, and proposed work is not presented as completed.

A real source ID or schema-valid response does not prove a claim is true. Keep failures and regressions, disclose model and instruction changes, and use a larger fresh assessment set before making stronger quality claims.

## Hosted Groq compatibility checks

The application now uses the report component through `groq_service.py`. With the application dependencies installed, run `python -m evals.check_groq --credentials .env.hosting.local` to evaluate the five synthetic development cases on its request format. This makes real Groq calls, uses the deployed 768-token output cap, spaces requests to respect the observed account output limit, records raw responses, and stops on provider errors. See [hosting validation](../docs/hosting.md) for results and limitations. The original Ollama experiments and runner remain unchanged.

## Human review and the paired pilot

New evaluation work follows [Hamel Husain and Shreya Shankar's methods](../docs/evaluation-method.md): inspect traces, collect human Pass/Fail judgments and critiques, derive a failure taxonomy, then validate any semantic judges against human references.

The [20-case pilot corpus](corpus/README.md) has provisional AI-authored reference labels and no holdout claim. Run `python -m evals.pilot --prepare` for an offline request capture, or add `--credentials .env.hosting.local` without `--prepare` for a real paired Groq run. All requests are frozen before execution; the run alternates the order of the deployed and minimal instruction variants and retains errors.

Start the local annotation interface with `python -m evals.review_app --run evals/runs/RUN_DIRECTORY`. It displays sources alongside the generated report, saves append-only local review events, and hides variant names and suggested labels in the primary view. Start by reviewing 30 traces yourself before using assistant suggestions. The page can be used while the rest of a run is still generating. Review journals are excluded from Git.

Those initial judgments receive a second pass: the assistant checks them against source evidence and brings disputed cases back to the owner. Revisions preserve the original judgments; unresolved cases are excluded from references used to validate automated judges. This is an explicit discussion step, not an automatic feature of the review interface. See [label review](../docs/evaluation-method.md#check-labels-before-using-them-as-references).

`python -m evals.scoring --run evals/runs/RUN_DIRECTORY` reports structural outcomes and four objective field comparisons against the provisional references. To include your judgments, also pass `--reviews PATH --reviewer NAME --criteria-version VERSION`. Different reviewers, reviewer kinds, and criteria versions are not combined. These field checks do not grade free-text faithfulness. Human review is the default; assistant review must be selected explicitly with `--reviewer-kind assistant` and remains provisional.

`python -m evals.corpus_tools` regenerates provenance and coverage diagnostics without model calls. The pilot is heavily weighted toward missing priority/date values; the score report therefore separates results by expected field state.

The first complete paired pilot is retained in [the trace bundle](published/20260908T160338Z-pilot/README.md), including its provider schema failure and explicit continuation of unattempted requests. Its [objective results](published/20260908T160338Z-pilot/objective-report.md) are provisional and separate from human semantic review. `evals.export_run` creates a local shareable bundle without making a network write.

## Delegated assistant assessment

The owner delegated all 40 reviews to the assistant. The [completed assessment](assessments/20260908T160338Z-pilot-assistant/report.md) contains 40 source-supported critiques, provisional Pass/Fail judgments, derived failure categories, original notes, and corrections. This supersedes waiting for initial human labels before engineering can continue. Human validation is still unperformed.

```sh
python -m evals.scoring --run evals/published/20260908T160338Z-pilot \
  --reviews evals/assessments/20260908T160338Z-pilot-assistant/reviews.jsonl \
  --reviewer codex-interactive-review --criteria-version assistant-v1 --reviewer-kind assistant
python -m evals.render_assessment --run evals/published/20260908T160338Z-pilot \
  --assessment evals/assessments/20260908T160338Z-pilot-assistant
python -m evals.review_app --run evals/published/20260908T160338Z-pilot \
  --assessment evals/assessments/20260908T160338Z-pilot-assistant
```

The renderer verifies source quotes, source/output identity, trace-file fingerprints, complete coverage, and review attribution before producing Markdown, HTML, and JSON summaries. Open `http://127.0.0.1:5003/assessment`. Assistant labels never enter the human journal or count as human alignment evidence. Both structural failures remain in the denominator.

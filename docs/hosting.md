Frontline hosting
================

The public demo uses alwaysdata Free for the FastHTML application and persistent files, and Groq Free for Qwen 3.8 27B report generation, Whisper transcription, and image descriptions. The hosting account is davidrussell; open [the public Frontline demo](https://davidrussell.alwaysdata.net).

Visitors select “Explore the demo” and receive a separate workspace with a synthetic note and a saved example report. No shared login is required. Demo workspaces expire after 24 hours and are cleaned up when visitors start the demo. This is a personal demonstration, not a place to retain operational records.

Live reports retain their validated structured output and the exact text sources used to generate them. Completed work, proposed actions, missing information, and disagreements are displayed separately. Editing the source later does not rewrite an earlier report. Reviewer notes and the report title can be changed while the generated findings remain preserved.

AI transcripts and image descriptions are identified as derived text in the source snapshot. They require review against the original media. Image descriptions replace the public demo's old Moondream detection control; bounding-box detection remains an optional legacy feature rather than being represented as supported by the new adapter.

Local configuration
-------------------

Install requirements.txt and set GROQ_API_KEY. For the public-demo behavior, also set FRONTLINE_DEMO=1 and FRONTLINE_DATA_DIR to a dedicated runtime directory. For a localhost HTTP preview only, set FRONTLINE_HTTPS_ONLY=0. Then run:

    python -m uvicorn main:app --host 127.0.0.1 --port 5001

The default mode retains local account registration/login and existing flat reports. The public demo disables registration/password login in favor of separate visitor sessions. The optional legacy vision integration is installed with requirements-vision.txt.

The application reads environment variables. The local .env.hosting.local file holds deployment credentials and is excluded from Git; it is not automatically loaded by main.py. The deployment tool copies only GROQ_API_KEY into the server's private application environment. alwaysdata's administrative token and the deployment SSH password never enter the application archive or web-process environment.

Data is held outside release directories:

    /home/davidrussell/frontline/runtime/data/frontline.db
    /home/davidrussell/frontline/runtime/data/usage.sqlite3
    /home/davidrussell/frontline/runtime/data/.session-key
    /home/davidrussell/frontline/runtime/uploads/

The session key is generated once and retained across restarts. A signed visitor session is bound to its visitor identity as well as its database ID. Uploaded files require ownership checks; browser-supplied MIME types do not determine how they are served.

Limits and failure behavior
---------------------------

These are application limits, deliberately below or separate from the providers' maximum allowances:

| Resource | Demo limit |
|---|---|
| Upload | 4 MB/file, two files/request; supported extensions only |
| Image | At most 12 megapixels; resized for AI interpretation |
| Aggregate request body | 9 MB; at most two simultaneous mutating requests |
| Visitor storage | 12 MB, 20 input files |
| Workspace | Eight inputs; ten workspaces/visitor |
| All uploaded data | 300 MB |
| Text input | 12,000 characters |
| Report source packet | 9,000 characters total |
| Report generation | Three attempts/visitor/rolling day; 35 attempts/application/rolling day |
| Pending report jobs | Three; one report worker |
| Qwen token budget | 7,000 estimated total tokens/rolling minute, 120,000/rolling day |
| Qwen output budget | 900 reserved output tokens/rolling minute, 24,000/rolling day |
| Qwen output cap | 768 tokens/report; reasoning disabled |
| Transcription | Six calls/visitor/day; 12 calls/application/hour; 40/day |
| Visitor creation | Ten/hour/IP, 100/day/application; IP identifiers are hashed |

Limits and provider cooldowns persist in SQLite. Actual returned token usage replaces conservative reservations when available. A short local capacity wait occurs before making an API call; there is no automatic retry of a failed model request and no fallback to a paid provider. A provider 429 becomes a visible capacity message, while existing sources and reports remain available.

The account revealed an additional **1,000 output tokens/minute** limit during live validation. The initial 2,048-token configuration received a 429 on FL-002, so the deployed configuration uses a 768-token output cap and the separate output budget above. This actual account limit takes precedence over a generic pricing table.

Interrupted jobs become a visible failed state on restart. A failed or truncated response is not saved as a completed report. Generation happens in a background task and is polled from the browser; navigation does not require keeping a long model request open.

The app can sleep after 15 minutes without requests. Its runtime directory and session key survive. CPU and RAM remain bounded by the alwaysdata Free plan; this is not an availability guarantee under arbitrary traffic.

Deployment and rollback
-----------------------

The deployment utility requires httpx and paramiko, separate from application dependencies:

    uv venv .tools/deploy-venv
    uv pip install --python .tools/deploy-venv/bin/python httpx paramiko
    .tools/deploy-venv/bin/python deploy/alwaysdata.py inspect
    .tools/deploy-venv/bin/python deploy/alwaysdata.py prepare
    .tools/deploy-venv/bin/python deploy/alwaysdata.py check
    .tools/deploy-venv/bin/python deploy/alwaysdata.py publish

The tool verifies the account's plus-free product before deployment. It creates a dedicated deployment SSH user, records the SSH host key on first contact, uploads an explicit allowlist of application files, and installs a lightweight environment. Neither the developer's local database nor existing local uploads are included.

prepare builds a release named by its content hash and records the prepared target locally. check runs the public-demo isolation, quota, upload, and persistence tests on the real Linux host without model calls. publish changes the current symlink and configures the existing placeholder site as a Uvicorn user program. No account-plan upgrade is performed.

The original site configuration is saved privately in .tools/alwaysdata/original-site.json. Previous release directories remain on the host. To roll back application code, point the current symlink at a reviewed previous release and restart the site through alwaysdata. Runtime files stay separate. Schema changes must be checked for compatibility before using an older release; this is not an automatic database downgrade.

alwaysdata uses distinct SSH and HTTP Unix users in the account group. The startup script therefore uses mode 0750, its private application environment 0640, and the shared runtime root 02770. The web process starts with umask 077. The first public probe caught and resolved a permission-denied startup caused by initially using owner-only permissions for the startup script.

Validation record — September 8, 2026
-------------------------------------

The local suite passes 33 tests, including cross-visitor reads and writes, cookie identity binding, upload limits and MIME handling, request origin checks, expiry cleanup, immutable report snapshots, interrupted jobs, and persistent quota enforcement. The 12 hosting-specific tests also run on alwaysdata. The Linux application environment occupies approximately 106 MB.

Browser checks cover the demo entry flow, saved source citations, unknown fields, review editing, uploads, modal interactions, recording upload, upload-limit feedback, and desktop/mobile overflow. Model and live public URL checks are recorded separately from those browser-only checks.

The same browser workflow passed against the actual public HTTPS URL with no captured JavaScript errors or failed application requests. A separate live check generated and validated a report in 2.55 seconds, transcribed a synthetic spoken note in 0.41 seconds, and read P-17 from a synthetic image label. The image request took 57.58 seconds including the local wait for shared AI capacity. Cross-visitor report and image requests returned 404.

A real site restart preserved the existing session and generated report; the first request took 2.26 seconds. For a controlled sleep/wake check, the idle timeout was temporarily set to 30 seconds. After 45 seconds without HTTP requests, a process check confirmed zero app processes. The next request woke the app in 1.19 seconds, and both the report and uploaded image remained accessible. The timeout was restored to 900 seconds. This tests an actual idle cycle, not a full 24-hour unattended interval.

The final Groq component run contains one generation for each of the five synthetic development cases. All five passed the existing schema, state, citation-ID, and literal-identifier checks. HTTP round-trip times ranged from 1.00 to 2.49 seconds, with a median of 1.13 seconds. They used 287–465 output tokens. These are small development fixtures, not a held-out accuracy result or a production latency guarantee.

Manual review found that the central distinctions were retained: filter installation versus proposed seal work; “none” versus missing parts information; unresolved installation disagreement; and the applicable controller revision. One remaining caveat is FL-002's proposed outlet inspection, labeled as a clarification: it goes beyond strictly extracting recorded actions. It should be reviewed rather than treated as an established maintenance requirement. Structural checks alone do not establish source entailment.

The ignored raw run directories preserve the progression:

- evals/runs/20260908-groq-initial-compatibility: one successful initial compatibility request.
- evals/runs/20260908T151313Z-groq: initial larger-cap run, stopped on the account's output-token 429.
- evals/runs/20260908T151544Z-groq: complete five-case run with the deployed 768-token cap.

To repeat the bounded component check using the same request builder:

    python -m evals.check_groq --credentials .env.hosting.local

It makes real Groq requests, spaces the cases to respect the output allowance, records requests/responses and code snapshots, and stops on provider errors. The saved example was generated from the earlier successful synthetic compatibility request and is explicitly labeled as a saved example.

The broader provider comparison and its public sources are in [free-hosting-research.md](free-hosting-research.md).


## RAG release validation — September 8, 2026

Release `b310351c54c7` adds indexed manufacturer references and the guidance-display fallback. The prepared application matched the committed source before deployment. The Linux environment supports SQLite FTS5 and JSON functions; the Python environment remains approximately 106 MB and the application release approximately 512 KB.

The local offline suite passes 63 tests. The 13 hosting checks also passed in the alwaysdata environment. An existing visitor session and saved report survived the update. A public gateway example completed generation in 1.852 seconds; this is one observation, not a latency guarantee.

Two initial smoke checks looked for a guidance heading. The first script failed before retaining its response, so that observation is incomplete. The second retained response showed that retrieval and source links worked while the model omitted a guidance summary. The viewer was corrected to display the saved reference passages explicitly when a summary is absent. Rechecking that same saved report verified the fix without another model call or alteration of the saved model output. [Recorded validation](rag-release-validation.json).

The original model outputs, the initial retrieval failure, and both retrieval comparisons remain in the evaluation artifacts. [Retrieval design and limitations](reference-retrieval.md).

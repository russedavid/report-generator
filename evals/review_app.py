"""Local, append-only trace review. No model calls or automatic human labels."""

import argparse
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from evals.trace_data import (
    output_fingerprint,
    output_text,
    provider_error,
    structural_outcome,
)

HERE = Path(__file__).resolve().parent


def make_app(run_dir, journal, test_mode=False, assessment=None, dataset_snapshot=None):
    run_dir, journal = Path(run_dir).resolve(), Path(journal).resolve()
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    order = json.loads((run_dir / "review-order.json").read_text())
    if len(order) != len(set(order)) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in order):
        raise ValueError("Review order contains duplicate or unsafe trace IDs")
    run_id = run_dir.name
    journal.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    if dataset_snapshot is not None:
        snapshot = (run_dir / dataset_snapshot).resolve()
        if not snapshot.is_relative_to(run_dir) or not snapshot.is_file():
            raise ValueError("The dataset snapshot must belong to this run")
        dataset_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    else:
        dataset_hash = manifest["artifacts"]["evals/corpus/pilot-v1.jsonl"]
    assessment_report = None
    if assessment is not None:
        assessment = Path(assessment).resolve()
        if json.loads((assessment / "manifest.json").read_text())["run_id"] != run_id:
            raise ValueError("Assessment belongs to another run")
        assessment_report = assessment / "report.html"
        if not assessment_report.is_file():
            raise ValueError("Render the assessment before serving it")

    def events():
        if not journal.exists():
            return []
        return [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]

    def record(trace_id):
        if trace_id not in order:
            raise ValueError("Unknown trace")
        path = run_dir / (trace_id + ".json")
        if not path.exists():
            raise ValueError("Trace has not completed")
        return json.loads(path.read_text())

    async def index(request):
        return FileResponse(HERE / "review.html", media_type="text/html")

    async def script(request):
        return FileResponse(HERE / "review.js", media_type="text/javascript")

    async def show_assessment(request):
        if assessment_report is None:
            return JSONResponse({"error": "No assessment attached"}, status_code=404)
        return FileResponse(assessment_report, media_type="text/html")

    async def listing(request):
        ready = [trace_id for trace_id in order if (run_dir / (trace_id + ".json")).exists()]
        labels = {}
        criteria_version = request.query_params.get("criteria_version", "open-v1")
        reviewer = request.query_params.get("reviewer")
        all_labels = {}
        for item in events():
            if item["run_id"] == run_id and item["criteria_version"] == criteria_version:
                all_labels[item["trace_id"]] = item
                if reviewer is not None and item["reviewer"] == reviewer:
                    labels[item["trace_id"]] = item
        return JSONResponse({"run_id": run_id, "dataset_sha256": dataset_hash, "ready": ready,
                             "planned": len(order), "labels": labels,
                             "test_mode": test_mode,
                             "assistant_assessment_url": "/assessment" if assessment_report else None,
                             "human_reviewed": sum(x["reviewer_kind"] == "human" for x in (labels if reviewer else all_labels).values())})

    async def trace(request):
        try:
            value = record(request.path_params["trace_id"])
        except (ValueError, json.JSONDecodeError):
            return JSONResponse({"error": "Trace is not ready"}, status_code=404)
        # Reference answers and prior assistant critiques are deliberately not included.
        result = {k: value.get(k) for k in (
            "trace_id", "input_sources", "raw_output", "output_sha256", "request",
            "http_status", "structural_status", "validation_error", "elapsed_seconds", "usage",
            "retrieval", "release_id", "transition",
        )}
        result.update(raw_output=output_text(value), output_sha256=output_fingerprint(value),
                      structural_status=structural_outcome(value),
                      provider_error={k: v for k, v in provider_error(value).items() if k != "failed_generation"})
        return JSONResponse(result)

    async def save_label(request: Request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "Use the local review page"}, status_code=403)
        body = await request.body()
        if len(body) > 16000:
            return JSONResponse({"error": "Annotation too large"}, status_code=413)
        try:
            data = json.loads(body)
            value = record(data["trace_id"])
            if data.get("output_sha256") != output_fingerprint(value):
                raise ValueError("The output changed; reload before reviewing")
            if data["verdict"] not in ("pass", "fail"):
                raise ValueError("Choose Pass or Fail")
            for field in ("reviewer", "critique", "criteria_version"):
                if not isinstance(data.get(field), str) or not data[field].strip():
                    raise ValueError("Add your name, a critique, and a criteria version")
            if len(data["critique"]) > 6000 or len(data["reviewer"]) > 100 or len(data["criteria_version"]) > 80:
                raise ValueError("Annotation field is too long")
            if data.get("reviewer_kind", "human") not in ("human", "test"):
                raise ValueError("Unsupported reviewer kind")
            event = {
                "event_id": str(uuid4()), "run_id": run_id, "dataset_sha256": dataset_hash,
                "trace_id": data["trace_id"], "output_sha256": output_fingerprint(value),
                "source_sha256": value.get("source_sha256"), "criterion": "overall_task_success",
                "reviewer": data["reviewer"].strip(), "reviewer_kind": "test" if test_mode else data.get("reviewer_kind", "human"),
                "verdict": data["verdict"], "critique": data["critique"].strip(),
                "criteria_version": data["criteria_version"].strip(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "stage": "open_coding",
            }
            with write_lock:
                fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            return JSONResponse({"saved": True})
        except (ValueError, KeyError, TypeError) as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    async def export(request):
        return JSONResponse(
            {"run_id": run_id, "dataset_sha256": dataset_hash, "events": events()},
            headers={"Content-Disposition": 'attachment; filename="frontline-review.json"'},
        )

    return Starlette(routes=[
        Route("/", index), Route("/review.js", script), Route("/items", listing),
        Route("/assessment", show_assessment),
        Route("/trace/{trace_id}", trace), Route("/label", save_label, methods=["POST"]),
        Route("/export", export),
    ])


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--port", type=int, default=5003)
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--assessment", type=Path, help="Serve a separate, read-only assistant assessment")
    parser.add_argument("--dataset-snapshot", type=Path, help="Dataset file inside the run, for non-pilot studies")
    args = parser.parse_args()
    journal = args.journal or HERE / "reviews" / (args.run.name + ".jsonl")
    print("Review: http://127.0.0.1:" + str(args.port), flush=True)
    print("Journal: " + str(journal), flush=True)
    uvicorn.run(make_app(args.run, journal, test_mode=args.test_mode, assessment=args.assessment, dataset_snapshot=args.dataset_snapshot),
                host="127.0.0.1", port=args.port, log_level="warning")

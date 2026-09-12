"""Small, versioned reference retrieval with eligibility enforced before top-k."""

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = {
    "raspberry-pi-3bplus": r"\b(?:raspberry\s*pi|rpi)\s*3\s*(?:model\s*)?b\+",
    "raspberry-pi-4": r"\b(?:raspberry\s*pi|rpi)\s*4\b",
    "raspberry-pi-5": r"\b(?:raspberry\s*pi|rpi)\s*5\b",
}
STOP_WORDS = {"a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "by", "from", "with", "its", "it", "i", "we", "is", "was", "were", "be", "been", "this", "that", "as", "no", "not", "has", "have", "had", "during", "recorded", "report", "unit", "gateway", "raspberry", "pi", "model", "degrees", "celsius", "equipment", "use", "uses", "used", "repair", "maintenance"}
COMPONENT_TERMS = {"fan", "fans", "gpu", "arm", "cpu", "usb", "hub", "hubs", "kernel", "vcgencmd"}
ELIGIBILITY = """p.status='current' AND (p.owner_id IS NULL OR p.owner_id=?)
    AND EXISTS (SELECT 1 FROM json_each(p.models) WHERE value=?)
    AND (p.revision IS NULL OR p.revision=?)"""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_references():
    path = ROOT / "reference-data/passages-v1.jsonl"
    manifest = json.loads((path.parent / "manifest.json").read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["dataset_sha256"]:
        raise ValueError("Reference corpus does not match its manifest")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def source_context(sources):
    # Unreviewed image/audio interpretations and reference text cannot establish model identity.
    direct = [s for s in sources if s.get("origin", "user") in {"user", "user_edited", "synthetic"}
              and s.get("evidence_role") != "reference"
              and s.get("type", "text") not in {"reference", "history_note", "simulated_image_description", "public_reference_summary", "fictional_manual_excerpt"}]
    mentions = []
    for source in direct:
        for sentence in re.split(r"(?<=[.!?])\s+|\n", source["text"]):
            if re.search(r"\b(maybe|might|possibly|unconfirmed|uncertain|unknown|untrusted|ocr)\b|\bnot (a |an |confirmed)|\bcould be\b", sentence, re.IGNORECASE):
                continue
            for model, expression in MODELS.items():
                if match := re.search(expression, sentence, re.IGNORECASE):
                    mentions.append({"model": model, "source_id": source.get("id"), "matched_text": match.group()})
    models = sorted({m["model"] for m in mentions})
    text = "\n".join(s["text"] for s in direct)
    revisions = {r.rstrip(".").upper() for r in re.findall(r"\brevision\s+([A-Za-z0-9.-]+)", text, re.IGNORECASE)}
    return {"model": models[0] if len(models) == 1 else None,
            "revision": next(iter(revisions)) if len(revisions) == 1 else None,
            "model_mentions": mentions,
            "status": "ambiguous_model" if len(models) > 1 or len(revisions) > 1 else "known_model" if models else "unknown_model"}


def query_terms(text):
    # Treat input as plain terms, never as executable FTS syntax.
    return list(dict.fromkeys(word for word in re.findall(r"[a-z0-9_]+", text.lower())
                              if word not in STOP_WORDS and not word.isdecimal() and len(word) > 1))[:48]


class ReferenceIndex:
    def __init__(self, path, documents):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if len({d["id"] for d in documents}) != len(documents):
            raise ValueError("Reference IDs must be unique")
        for d in documents:
            if (not re.fullmatch(r"[A-Za-z0-9_-]+", d["id"]) or not d["text"].strip()
                    or d["status"] not in {"current", "superseded", "revoked"}
                    or not d["models"] or not d.get("version") or not d.get("license")):
                raise ValueError("Reference metadata is incomplete")
            if not d["url"].startswith("https://"):
                raise ValueError("Reference links must use HTTPS")
        self.corpus_sha256 = digest(documents)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            existing = connection.execute("SELECT value FROM metadata WHERE key='corpus'").fetchone()
            if not existing or existing[0] != self.corpus_sha256:
                with connection:
                    connection.execute("DROP TABLE IF EXISTS passages")
                    connection.execute("DROP TABLE IF EXISTS passage_fts")
                    connection.execute("CREATE TABLE passages (id TEXT PRIMARY KEY, models TEXT, revision TEXT, status TEXT, owner_id TEXT, payload TEXT)")
                    connection.execute("CREATE VIRTUAL TABLE passage_fts USING fts5(id UNINDEXED, title, body, keywords, tokenize='porter unicode61')")
                    for d in documents:
                        connection.execute("INSERT INTO passages VALUES (?,?,?,?,?,?)",
                                           (d["id"], json.dumps(d["models"]), d.get("revision"), d["status"],
                                            None if d.get("owner_id") is None else str(d["owner_id"]), json.dumps(d)))
                        connection.execute("INSERT INTO passage_fts VALUES (?,?,?,?)", (d["id"], d["title"], d["text"], d.get("keywords", "")))
                    connection.execute("INSERT OR REPLACE INTO metadata VALUES ('corpus',?)", (self.corpus_sha256,))
        os.chmod(self.path, 0o600)

    def search(self, text, *, model, revision=None, actor=None, limit=2, method="bm25"):
        if method not in {"bm25", "overlap"}:
            raise ValueError("Unsupported reference ranking method")
        if not model:
            return []
        terms = query_terms(text)
        if not terms:
            return []
        components = set(terms) & COMPONENT_TERMS
        limit = max(1, min(int(limit), 4))
        eligibility = ELIGIBILITY
        params = (None if actor is None else str(actor), model, revision)
        with closing(sqlite3.connect(self.path)) as connection:
            if method == "bm25":
                query = " OR ".join('"' + word + '"' for word in terms)
                if components:
                    query = "(" + query + ") AND (" + " OR ".join('"' + word + '"' for word in sorted(components)) + ")"
                rows = connection.execute(
                    "SELECT p.payload, bm25(passage_fts,0,2,1,1) AS score FROM passage_fts "
                    "JOIN passages p ON p.id=passage_fts.id WHERE passage_fts MATCH ? AND " + eligibility +
                    " ORDER BY score, p.id LIMIT ?", (query, *params, limit)).fetchall()
            else:
                eligible = connection.execute("SELECT payload FROM passages p WHERE " + eligibility, params).fetchall()
                scored = []
                for (payload,) in eligible:
                    d = json.loads(payload)
                    words = set(query_terms(d["title"] + " " + d["text"] + " " + d.get("keywords", "")))
                    if components and not components & words:
                        continue
                    overlap = len(set(terms) & words)
                    if overlap:
                        scored.append((payload, -overlap))
                rows = sorted(scored, key=lambda row: (row[1], json.loads(row[0])["id"]))[:limit]
        return [dict(json.loads(payload), retrieval_score=score) for payload, score in rows]

    def lookup(self, passage_id, *, model, revision=None, actor=None):
        """Resolve supporting context with exactly the same eligibility as search."""
        if not model:
            return None
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT p.payload FROM passages p WHERE p.id=? AND " + ELIGIBILITY,
                (passage_id, None if actor is None else str(actor), model, revision),
            ).fetchone()
        return dict(json.loads(row[0]), retrieval_score=None) if row else None

    def enrich(self, sources, actor, max_chars=9000, *, selection_policy="top_two", dependencies=None):
        started = time.perf_counter()
        context = source_context(sources)
        trace = {**context, "method": "sqlite_fts5_bm25", "corpus_sha256": self.corpus_sha256,
                 "selected_ids": [], "candidates": [], "context_budget_excluded": []}
        if context["status"] != "known_model":
            return sources, dict(trace, elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        text = "\n".join(s["text"] for s in sources)
        if selection_policy == "top_two":
            candidates = self.search(text, model=context["model"], revision=context["revision"], actor=actor)
        else:
            from context_selection import select_context
            candidates, selection = select_context(
                self, text, model=context["model"], revision=context["revision"], actor=actor,
                policy=selection_policy, dependencies=dependencies,
                max_chars=max(0, max_chars - sum(len(s["text"]) for s in sources)),
            )
            trace["selection"] = selection
        result = list(sources)
        remaining = max_chars - sum(len(s["text"]) for s in sources)
        used_ids = {s["id"] for s in sources}
        for d in candidates:
            trace["candidates"].append({"id": d["id"], "score": d["retrieval_score"]})
            if len(d["text"]) > remaining:
                trace["context_budget_excluded"].append(d["id"])
                continue
            source_id = "REF-" + d["id"]
            if source_id in used_ids:
                raise ValueError("Reference/source ID collision")
            result.append({"id": source_id, "type": "reference", "evidence_role": "reference", "origin": "manufacturer_reference_summary",
                           "filename": d["title"], "text": d["text"], "reference_id": d["id"],
                           "reference_url": d["url"], "reference_version": d["version"], "reference_sha256": d["sha256"],
                           "license": d["license"], "attribution": d["attribution"], "section": d["section"],
                           "applicable_models": d["models"], "applicable_revision": d.get("revision")})
            remaining -= len(d["text"])
            used_ids.add(source_id)
            trace["selected_ids"].append(d["id"])
        trace["status"] = "retrieved" if trace["selected_ids"] else "context_budget" if candidates else "no_match"
        trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result, trace

"""Immutable, local configuration snapshots for controlled release exercises.

This does not change the application's deployment or select its live configuration.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME_FILES = ("reporting.py", "groq_service.py", "retrieval.py", "context_selection.py", "report_workflow.py", "release_snapshot.py")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def fingerprint(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def runtime_identity():
    return {name: fingerprint((ROOT / name).read_bytes()) for name in RUNTIME_FILES}


def store_release(directory, *, documents, dependencies, instructions, request_settings, policy):
    if policy not in {"top_two", "primary_with_dependencies"} or not instructions.strip():
        raise ValueError("Invalid release configuration")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"format": 1, "runtime": runtime_identity(), "documents": documents,
               "dependencies": dependencies, "instructions": instructions,
               "request_settings": request_settings, "selection_policy": policy}
    release_id = fingerprint(payload)
    path = directory / (release_id + ".json")
    try:
        with path.open("xb") as handle:
            handle.write(canonical(payload))
        path.chmod(0o600)
    except FileExistsError:
        if path.is_symlink() or fingerprint(path.read_bytes()) != release_id:
            raise ValueError("Existing release snapshot was modified") from None
    return release_id


def read_release(directory, release_id):
    if not isinstance(release_id, str) or not re.fullmatch(r"[a-f0-9]{64}", release_id):
        raise ValueError("Invalid release identity")
    path = Path(directory) / (release_id + ".json")
    if path.is_symlink():
        raise ValueError("Release snapshots cannot be symlinks")
    data = path.read_bytes()
    if fingerprint(data) != release_id:
        raise ValueError("Release snapshot integrity failed")
    payload = json.loads(data)
    if payload.get("format") != 1 or payload.get("runtime") != runtime_identity():
        raise ValueError("Release requires a different runtime; restore matching code first")
    return payload


def activate_release(directory, release_id):
    """Switch only this explicitly supplied staging directory's local pointer."""
    directory = Path(directory)
    payload = read_release(directory / "releases", release_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".active-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical({"release_id": release_id}))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, directory / "active-release.json")
    finally:
        if Path(name).exists():
            Path(name).unlink()
    return payload


def active_release(directory):
    directory = Path(directory)
    pointer = json.loads((directory / "active-release.json").read_text())
    return pointer["release_id"], read_release(directory / "releases", pointer["release_id"])

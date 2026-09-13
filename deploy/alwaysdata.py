"""Deploy a reviewed Frontline release to the existing alwaysdata Free account.

Run with the optional deployment environment (httpx and paramiko).
Credentials and host keys remain in .tools; neither enters the application bundle.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import tarfile
from pathlib import Path

import httpx
import paramiko

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".tools/alwaysdata"
FILES = [
    "main.py", "models.py", "utils.py", "css.py", "ai_services.py", "reporting.py",
    "report-instructions.txt", "hosting_runtime.py", "groq_service.py", "report_workflow.py",
    "report_views.py", "requirements.txt", "demo-data/sample-report.json", "static/app.js",
    "retrieval.py", "context_selection.py", "reference-data/passages-v1.jsonl", "reference-data/manifest.json",
]


def private_json(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)


def credentials():
    data = {}
    for line in (ROOT / ".env.hosting.local").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            data[name.strip()] = value.strip().strip("'\"")
    return data


def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    cfg = credentials()
    api = httpx.Client(base_url="https://api.alwaysdata.com/v1/", auth=(cfg["ALWAYSDATA_API_TOKEN"], ""),
                       timeout=30, headers={"User-Agent": "Frontline-Deploy/0.1"})
    accounts = api.get("account/").json()
    matches = [a for a in accounts if cfg["ALWAYSDATA_ACCOUNT"] in (a["name"], str(a["id"]), a["name"] + ".alwaysdata.net")]
    if not matches and len(accounts) == 1:
        matches = accounts
    if len(matches) != 1:
        raise RuntimeError("The hosting account could not be identified uniquely.")
    account = matches[0]
    name = account["name"]
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        raise RuntimeError("Unexpected hosting account name")
    product = api.get(account["product"]["href"].removeprefix("/v1/")).json()
    if product["name"] != "plus-free":
        raise RuntimeError("This deployment requires the Free plan; no plan change will be made.")
    print("Account: " + name + " · Free plan confirmed", flush=True)
    return cfg, api, name


def ssh_connection(api, account):
    state_file = STATE / "ssh-access.json"
    if state_file.exists():
        access = json.loads(state_file.read_text())
    else:
        access = {"username": account + "_frontline", "password": secrets.token_urlsafe(32)}
        existing = api.get("ssh/").json()
        if any(u["name"] == access["username"] for u in existing):
            raise RuntimeError("A deployment user already exists without local credentials; refusing to replace it.")
        response = api.post("ssh/", json={"name": access["username"], "password": access["password"],
                                         "home_directory": "", "shell": "BASH", "can_use_password": True,
                                         "annotation": "Frontline deployment"})
        if response.status_code != 201:
            raise RuntimeError("Deployment SSH user creation failed: HTTP " + str(response.status_code))
        private_json(state_file, access)
    ssh = paramiko.SSHClient()
    hosts = STATE / "known_hosts"
    if not hosts.exists():
        hosts.touch(mode=0o600)
    ssh.load_host_keys(str(hosts))
    # First connection records this host's key; later connections require that key.
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("ssh-" + account + ".alwaysdata.net", username=access["username"],
                password=access["password"], timeout=30, look_for_keys=False, allow_agent=False)
    ssh.save_host_keys(str(hosts))
    return ssh


def run(ssh, command, timeout=300):
    _, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out, err = stdout.read().decode(), stderr.read().decode()
    code = stdout.channel.recv_exit_status()
    if code:
        # Commands never contain credentials, but avoid dumping a remote environment.
        raise RuntimeError("Remote command failed (exit " + str(code) + "): " + err[-1800:])
    return out.strip()


def package():
    digest = hashlib.sha256()
    for name in FILES:
        digest.update(name.encode())
        digest.update((ROOT / name).read_bytes())
    release = digest.hexdigest()[:12]
    archive = STATE / (release + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for name in FILES:
            tar.add(ROOT / name, arcname=name)
    return release, archive


def prepare(cfg, api, account, ssh):
    release, archive = package()
    base = "/home/" + account + "/frontline"
    target = base + "/releases/" + release
    run(ssh, "mkdir -p " + shlex.quote(target) + " " + shlex.quote(base + "/runtime"))
    # SSH and HTTP use different Unix users in the account's shared group.
    run(ssh, "chmod 2770 " + shlex.quote(base + "/runtime"))
    with ssh.open_sftp() as sftp:
        remote_archive = base + "/" + archive.name
        sftp.put(str(archive), remote_archive)
    run(ssh, "tar -xzf " + shlex.quote(remote_archive) + " -C " + shlex.quote(target))
    run(ssh, "PYTHON_VERSION=3.13 python -m venv " + shlex.quote(base + "/venv"))
    print("Installing the lightweight application environment...", flush=True)
    run(ssh, shlex.quote(base + "/venv/bin/python") + " -m pip install --no-cache-dir -r " + shlex.quote(target + "/requirements.txt"), timeout=300)
    with ssh.open_sftp() as sftp:
        env_path = base + "/runtime/app.env"
        with sftp.open(env_path, "w") as stream:
            stream.write("GROQ_API_KEY=" + shlex.quote(cfg["GROQ_API_KEY"]) + "\n")
            stream.write("FRONTLINE_DEMO=1\nFRONTLINE_DATA_DIR=" + shlex.quote(base + "/runtime") + "\n")
        sftp.chmod(env_path, 0o640)
        start = base + "/start.sh"
        with sftp.open(start, "w") as stream:
            stream.write("#!/bin/sh\nset -eu\numask 077\nset -a\n. " + shlex.quote(env_path) + "\nset +a\n")
            stream.write("cd " + shlex.quote(base + "/current") + "\n")
            stream.write("exec " + shlex.quote(base + "/venv/bin/python") +
                         " -m uvicorn main:app --host \"$IP\" --port \"$PORT\" --workers 1 --proxy-headers --forwarded-allow-ips '*' --no-access-log\n")
        sftp.chmod(start, 0o750)
    run(ssh, "cd " + shlex.quote(target) + " && " + shlex.quote(base + "/venv/bin/python") +
        " -m compileall -q " + " ".join(shlex.quote(f) for f in FILES if f.endswith(".py")))
    private_json(STATE / "prepared.json", {"account": account, "release": release, "base": base, "target": target})
    print("Prepared release " + release, flush=True)
    print(run(ssh, "du -sh " + shlex.quote(base + "/venv") + " " + shlex.quote(target)), flush=True)


def publish(api, account, ssh):
    state = json.loads((STATE / "prepared.json").read_text())
    if state["account"] != account:
        raise RuntimeError("Prepared account changed")
    sites = api.get("site/").json()
    matches = [s for s in sites if account + ".alwaysdata.net/" in s["addresses"]]
    if len(matches) != 1:
        raise RuntimeError("Expected the existing account default site")
    site = matches[0]
    backup = STATE / "original-site.json"
    if not backup.exists():
        private_json(backup, site)
    run(ssh, "ln -sfn " + shlex.quote(state["target"]) + " " + shlex.quote(state["base"] + "/current"))
    response = api.patch("site/" + str(site["id"]) + "/", json={
        "type": "user_program", "command": state["base"] + "/start.sh",
        "working_directory": "frontline/current", "ssl_force": True,
        "max_idle_time": 900, "annotation": "Frontline public demo",
    })
    if response.status_code not in (200, 204):
        raise RuntimeError("Site configuration failed: HTTP " + str(response.status_code))
    response = api.post("site/" + str(site["id"]) + "/restart/")
    if response.status_code not in (200, 202, 204):
        raise RuntimeError("Site restart failed: HTTP " + str(response.status_code))
    print("Published https://" + account + ".alwaysdata.net", flush=True)


def check(ssh):
    state = json.loads((STATE / "prepared.json").read_text())
    target, base = state["target"], state["base"]
    run(ssh, "mkdir -p " + shlex.quote(target + "/tests"))
    with ssh.open_sftp() as sftp:
        sftp.put(str(ROOT / "tests/test_hosting.py"), target + "/tests/test_hosting.py")
    command = ("cd " + shlex.quote(target) + " && " + shlex.quote(base + "/venv/bin/python") +
               " -m unittest discover -s tests -p test_hosting.py")
    print(run(ssh, command), flush=True)
    print("Remote isolation, quota, and persistence tests passed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "prepare", "check", "publish"))
    args = parser.parse_args()
    cfg, api, account = connect()
    if args.action == "inspect":
        print(json.dumps({"sites": [{k: x.get(k) for k in ("id", "type", "addresses")} for x in api.get("site/").json()]}))
        return
    with ssh_connection(api, account) as ssh:
        if args.action == "prepare":
            prepare(cfg, api, account, ssh)
        elif args.action == "check":
            check(ssh)
        else:
            publish(api, account, ssh)


if __name__ == "__main__":
    main()

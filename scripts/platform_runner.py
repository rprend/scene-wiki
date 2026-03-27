from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name}.")
    return value


def api_request(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 SceneWikiRunner/1.0"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    token = require_env("RUNNER_API_TOKEN")
    headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {body}") from exc


def build_log_path(workdir: Path, job_id: str) -> Path:
    log_dir = workdir / "logs" / "jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{job_id}.log"


def append_log(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message)


def tail_log(log_path: Path, max_chars: int = 8000) -> str:
    if not log_path.exists():
        return ""
    content = log_path.read_text(encoding="utf-8", errors="replace")
    if len(content) <= max_chars:
        return content
    return content[-max_chars:]


def run_logged_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    log_path: Path,
    base_url: str | None = None,
    job_id: str | None = None,
    heartbeat_message: str | None = None,
) -> None:
    append_log(log_path, f"\n$ {' '.join(command)}\n")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    recent_lines: deque[str] = deque(maxlen=50)
    last_heartbeat = time.monotonic()
    assert process.stdout is not None
    for line in process.stdout:
        append_log(log_path, line)
        recent_lines.append(line.rstrip())
        if base_url and job_id and heartbeat_message and time.monotonic() - last_heartbeat >= 15:
            heartbeat(base_url, job_id, heartbeat_message)
            last_heartbeat = time.monotonic()

    return_code = process.wait()
    if return_code == 0:
        return

    tail = "\n".join(recent_lines).strip()
    raise RuntimeError(
        f"Command failed with exit code {return_code}. Log: {log_path}"
        + (f"\n\nRecent output:\n{tail}" if tail else "")
    )


def command_env(job: dict, title: str, custom_domain: str) -> dict[str, str]:
    env = os.environ.copy()
    env["QUARTZ_PAGE_TITLE"] = title
    env["QUARTZ_BASE_URL"] = custom_domain
    env["SCENE_WIKI_TITLE"] = title
    env["SCENE_WIKI_ARCHIVE_LABEL"] = title
    return env


def log_event(base_url: str, job_id: str, message: str, level: str = "info", payload: dict | None = None) -> None:
    api_request(
        base_url,
        f"/api/runner/jobs/{job_id}/event",
        method="POST",
        payload={"message": message, "level": level, "payload": payload},
    )


def heartbeat(base_url: str, job_id: str, message: str) -> None:
    api_request(
        base_url,
        f"/api/runner/jobs/{job_id}/heartbeat",
        method="POST",
        payload={"message": message},
    )


def ensure_pages_project(project_name: str, workdir: Path, log_path: Path) -> None:
    command = [
        "npx",
        "wrangler@latest",
        "pages",
        "project",
        "create",
        project_name,
        "--production-branch",
        "main",
    ]
    result = subprocess.run(command, cwd=workdir, capture_output=True, text=True)
    combined = f"{result.stdout}\n{result.stderr}"
    append_log(log_path, f"\n$ {' '.join(command)}\n{combined}\n")
    if result.returncode == 0:
        return
    if "already exists" in combined.lower():
        return
    raise RuntimeError(combined.strip())


def set_pages_secret(project_name: str, secret_name: str, secret_value: str, workdir: Path, log_path: Path) -> None:
    if not secret_value:
        return
    command = [
        "npx",
        "wrangler@latest",
        "pages",
        "secret",
        "put",
        secret_name,
        "--project-name",
        project_name,
    ]
    result = subprocess.run(
        command,
        cwd=workdir,
        input=secret_value,
        capture_output=True,
        text=True,
        check=False,
    )
    append_log(log_path, f"\n$ {' '.join(command)}\n{result.stdout}\n{result.stderr}\n")
    if result.returncode != 0:
        raise RuntimeError((result.stdout + "\n" + result.stderr).strip())


def deploy_pages(project_name: str, output_dir: Path, workdir: Path, log_path: Path) -> None:
    command = [
        "npx",
        "wrangler@latest",
        "pages",
        "deployment",
        "create",
        str(output_dir),
        "--project-name",
        project_name,
        "--branch",
        "main",
        "--commit-dirty=true",
    ]
    run_logged_command(command, cwd=workdir, log_path=log_path)


def add_custom_domain(project_name: str, domain_name: str) -> None:
    account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
    api_token = require_env("CLOUDFLARE_API_TOKEN")
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/pages/projects/{project_name}/domains",
        data=json.dumps({"name": domain_name}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if "already exists" in body.lower():
            return
        raise RuntimeError(f"Attaching custom domain failed: {exc.code} {body}") from exc
    if not payload.get("success", False):
        raise RuntimeError(f"Attaching custom domain failed: {json.dumps(payload)}")


def run_scene_wiki_build(job: dict, workdir: Path, custom_domain: str, log_path: Path, base_url: str) -> Path:
    title = job.get("title") or job["siteSlug"].replace("-", " ").title()
    output_dir = workdir / "dist" / "generated" / job["siteSlug"]
    vault_dir = workdir / "vault" / job["siteSlug"]
    command = [
        "scene-wiki",
        "build-substack",
        job["sourceUrl"],
        "--subject",
        title,
        "--site-title",
        title,
        "--output-dir",
        str(output_dir),
        "--vault-dir",
        str(vault_dir),
    ]
    max_articles = os.getenv("SCENE_WIKI_MAX_ARTICLES", "").strip()
    if max_articles:
        command.extend(["--max-articles", max_articles])
    run_logged_command(
        command,
        cwd=workdir,
        env=command_env(job, title, custom_domain),
        log_path=log_path,
        base_url=base_url,
        job_id=job["id"],
        heartbeat_message="Scene wiki build still running.",
    )
    return output_dir


def handle_job(base_url: str, job: dict, workdir: Path) -> None:
    project_prefix = (job.get("pagesProjectPrefix") or "scene-wiki").strip("-")
    project_name = f"{project_prefix}-{job['siteSlug']}"[:58]
    main_domain = os.getenv("MAIN_DOMAIN") or job.get("mainDomain") or "scene-wiki.example.com"
    custom_domain = f"{job['siteSlug']}.{main_domain}"
    pages_url = f"https://{project_name}.pages.dev"
    log_path = build_log_path(workdir, job["id"])

    try:
        append_log(log_path, f"Job {job['id']} for {job['sourceUrl']}\n")
        log_event(base_url, job["id"], "Runner log initialized.", payload={"logPath": str(log_path)})
        heartbeat(base_url, job["id"], "Starting scene wiki build.")
        log_event(base_url, job["id"], "Building static wiki output.", payload={"project_name": project_name})
        output_dir = run_scene_wiki_build(job, workdir, custom_domain, log_path, base_url)

        heartbeat(base_url, job["id"], "Ensuring Cloudflare Pages project exists.")
        ensure_pages_project(project_name, workdir, log_path)

        for secret_name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
          secret_value = os.getenv(secret_name, "").strip()
          if secret_value:
              log_event(base_url, job["id"], f"Syncing Pages secret {secret_name}.")
              set_pages_secret(project_name, secret_name, secret_value, workdir, log_path)

        heartbeat(base_url, job["id"], "Deploying site bundle to Cloudflare Pages.")
        deploy_pages(project_name, output_dir, workdir, log_path)

        heartbeat(base_url, job["id"], "Attaching custom domain.")
        add_custom_domain(project_name, custom_domain)

        api_request(
            base_url,
            f"/api/runner/jobs/{job['id']}/complete",
            method="POST",
            payload={
                "pagesProjectName": project_name,
                "pagesUrl": pages_url,
                "customDomain": custom_domain,
                "runDir": str(workdir / "data"),
                "title": job.get("title"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        log_event(
            base_url,
            job["id"],
            "Runner failed.",
            level="error",
            payload={"logPath": str(log_path), "logTail": tail_log(log_path)},
        )
        api_request(
            base_url,
            f"/api/runner/jobs/{job['id']}/fail",
            method="POST",
            payload={"errorMessage": str(exc), "runDir": str(workdir / "data")},
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll the Scene Wiki platform API for jobs and execute them.")
    parser.add_argument("--env-file", default="scripts/platform.env.example", help="Path to an env file.")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    parser.add_argument("--poll-seconds", type=int, default=int(os.getenv("RUNNER_POLL_SECONDS", "20")))
    parser.add_argument("--workdir", default=os.getenv("RUNNER_WORKDIR", "."), help="Repo root containing the scene-wiki CLI.")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    base_url = require_env("PLATFORM_BASE_URL")
    workdir = Path(args.workdir).resolve()

    while True:
        payload = api_request(base_url, "/api/runner/claim-next", method="POST", payload={})
        job = payload.get("job")
        if not job:
            if args.once:
                return
            time.sleep(args.poll_seconds)
            continue

        handle_job(base_url, job, workdir)
        if args.once:
            return


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)

"""Local polling agent for the Cloudflare control plane."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app import main as bot_main


DEFAULT_AGENT_CONFIG_PATH = Path.home() / ".issue-to-pr-bot-agent" / "agent-config.json"


@dataclass(frozen=True)
class AgentConfig:
    control_plane_url: str
    agent_token: str
    workspace_root: Path
    poll_interval_seconds: int = 10
    repositories: list[str] | None = None


@dataclass(frozen=True)
class ClaimedTask:
    task_id: str
    event_name: str
    repository: str
    default_branch: str
    payload: dict
    github_token: str


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "run-once":
            config = load_agent_config(Path(args.config))
            task = claim_task(config)
            if not task:
                print("처리할 작업이 없습니다.")
                return 0
            run_claimed_task(config, task)
            return 0

        if args.command == "serve":
            config = load_agent_config(Path(args.config))
            run_agent_loop(config)
            return 0
    except Exception as error:
        print(f"Error: {error}")
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue-to-pr-bot-agent",
        description="Local polling agent that executes issue-to-pr-bot tasks without GitHub Actions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once = subparsers.add_parser("run-once", help="Claim one task and execute it.")
    run_once.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG_PATH))

    serve = subparsers.add_parser("serve", help="Keep polling and execute tasks continuously.")
    serve.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG_PATH))

    return parser


def load_agent_config(path: Path) -> AgentConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    repositories = data.get("repositories") or None
    return AgentConfig(
        control_plane_url=str(data["control_plane_url"]).rstrip("/"),
        agent_token=str(data["agent_token"]),
        workspace_root=Path(str(data["workspace_root"])),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 10)),
        repositories=[str(item) for item in repositories] if repositories else None,
    )


def run_agent_loop(config: AgentConfig) -> None:
    print(f"로컬 agent 시작: {config.control_plane_url}")
    while True:
        task = claim_task(config)
        if not task:
            time.sleep(config.poll_interval_seconds)
            continue
        run_claimed_task(config, task)


def claim_task(config: AgentConfig) -> ClaimedTask | None:
    query = {
        "agent_id": os.environ.get("COMPUTERNAME") or "local-agent",
    }
    if config.repositories:
        query["repositories"] = ",".join(config.repositories)
    query["agent_token"] = config.agent_token
    url = f"{config.control_plane_url}/api/tasks/claim?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {config.agent_token}",
            "User-Agent": "Mozilla/5.0 issue-to-pr-bot-agent",
        },
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    task = payload.get("task")
    if not task:
        return None

    return ClaimedTask(
        task_id=str(task["id"]),
        event_name=str(task["eventName"]),
        repository=str(task["repository"]),
        default_branch=str(task.get("defaultBranch") or "main"),
        payload=task["payload"],
        github_token=str(task["githubToken"]),
    )


def run_claimed_task(config: AgentConfig, task: ClaimedTask) -> None:
    summary = "completed"
    detail = ""
    try:
        workspace = prepare_repository_workspace(config.workspace_root, task)
        execute_task_in_workspace(workspace, task)
    except Exception:
        summary = "failed"
        detail = traceback.format_exc()
        print(detail)
        report_task_completion(config, task.task_id, "failed", summary, detail)
        raise
    else:
        report_task_completion(config, task.task_id, "completed", summary, detail)


def prepare_repository_workspace(workspace_root: Path, task: ClaimedTask) -> Path:
    workspace_root.mkdir(parents=True, exist_ok=True)
    target = workspace_root / task.repository.replace("/", "__")
    clone_url = f"https://x-access-token:{task.github_token}@github.com/{task.repository}.git"

    if not target.exists():
        run_command(["git", "clone", "--depth", "1", clone_url, str(target)])

    run_command(["git", "-C", str(target), "config", "core.autocrlf", "false"])
    run_command(["git", "-C", str(target), "fetch", clone_url, task.default_branch])
    run_command(["git", "-C", str(target), "checkout", "-B", task.default_branch, "FETCH_HEAD"])
    run_command(["git", "-C", str(target), "reset", "--hard", "FETCH_HEAD"])
    return target


def execute_task_in_workspace(workspace: Path, task: ClaimedTask) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        event_path = Path(temp_dir) / "github-event.json"
        event_path.write_text(json.dumps(task.payload, ensure_ascii=False), encoding="utf-8")
        env_updates = {
            "BOT_CREATE_PR": "1",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": task.repository,
            "GITHUB_REF_NAME": task.default_branch,
            "GITHUB_TOKEN": task.github_token,
            "BOT_GITHUB_TOKEN": task.github_token,
            "BOT_RESET_WORKTREE": "1",
        }
        with temporary_env(env_updates), change_directory(workspace):
            bot_main.main()


def report_task_completion(
    config: AgentConfig,
    task_id: str,
    status: str,
    summary: str,
    detail: str,
) -> None:
    url = f"{config.control_plane_url}/api/tasks/{task_id}/complete"
    payload = json.dumps(
        {
            "status": status,
            "summary": summary,
            "detail": detail[:4000],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.agent_token}",
            "User-Agent": "Mozilla/5.0 issue-to-pr-bot-agent",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30):
        return


def run_command(command: list[str]) -> None:
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if (result.stdout or "").strip():
        print((result.stdout or "").rstrip())
    if result.returncode != 0:
        raise RuntimeError(f"명령 실행 실패({result.returncode}): {' '.join(command)}")


@contextmanager
def temporary_env(updates: dict[str, str]):
    original: dict[str, str | None] = {key: os.environ.get(key) for key in updates}
    for key, value in updates.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key, old_value in original.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


@contextmanager
def change_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


if __name__ == "__main__":
    raise SystemExit(main())

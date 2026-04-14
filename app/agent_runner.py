"""Local polling agent for the Cloudflare control plane."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from app import main as bot_main


DEFAULT_AGENT_CONFIG_PATH = Path.home() / ".issue-to-pr-bot-agent" / "agent-config.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AgentConfig:
    control_plane_url: str
    agent_token: str
    workspace_root: Path
    poll_interval_seconds: int = 10
    repositories: list[str] | None = None
    log_path: Path | None = None


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
        config = load_agent_config(Path(args.config))
        if args.command == "run-once":
            task = claim_task(config)
            if not task:
                log_message(config, "처리할 작업이 없습니다.")
                return 0
            run_claimed_task(config, task)
            return 0

        if args.command == "start":
            return start_agent_process(Path(args.config))

        if args.command == "stop":
            return stop_agent_process(Path(args.config))

        if args.command == "status":
            return print_agent_status(Path(args.config))

        if args.command == "serve":
            run_agent_loop(config, Path(args.config))
            return 0
    except Exception as error:
        config_path = Path(args.config) if getattr(args, "config", None) else None
        log_path = try_resolve_log_path(config_path) if config_path else None
        log_message(None, f"Error: {error}", log_path=log_path)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue-to-pr-bot-agent",
        description="Cloudflare Worker 제어면과 통신하며 로컬에서 실제 작업을 수행하는 agent입니다.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once = subparsers.add_parser("run-once", help="작업 하나만 가져와 실행합니다.")
    run_once.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG_PATH))

    start = subparsers.add_parser("start", help="agent를 백그라운드로 시작합니다.")
    start.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG_PATH))

    stop = subparsers.add_parser("stop", help="백그라운드 agent를 중지합니다.")
    stop.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG_PATH))

    status = subparsers.add_parser("status", help="agent 실행 상태를 확인합니다.")
    status.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG_PATH))

    serve = subparsers.add_parser("serve", help="계속 polling 하면서 작업을 처리합니다.")
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
        log_path=Path(str(data["log_path"])) if data.get("log_path") else None,
    )


def run_agent_loop(config: AgentConfig, config_path: Path) -> None:
    ensure_single_instance(config_path, config)
    log_message(config, f"로컬 agent 시작: {config.control_plane_url}")
    try:
        while True:
            try:
                task = claim_task(config)
            except Exception as error:
                log_message(config, f"작업 조회 실패: {error}")
                time.sleep(config.poll_interval_seconds)
                continue
            if not task:
                time.sleep(config.poll_interval_seconds)
                continue
            try:
                run_claimed_task(config, task)
            except Exception as error:
                log_message(config, f"작업 실행 실패: {error}")
    finally:
        clear_pid_file(config_path)


def start_agent_process(config_path: Path) -> int:
    config = load_agent_config(config_path)
    running_pid = read_running_pid(config_path)
    if running_pid:
        log_message(config, f"agent가 이미 실행 중입니다. PID={running_pid}")
        return 0

    executable = resolve_background_python()
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    with config.log_path.open("a", encoding="utf-8") as handle:
        subprocess.Popen(
            [str(executable), "-m", "app.agent_runner", "serve", "--config", str(config_path)],
            cwd=str(REPOSITORY_ROOT),
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    time.sleep(2)
    running_pid = read_running_pid(config_path)
    if not running_pid:
        log_message(config, "agent 시작에 실패했습니다. 로그 파일을 확인하세요.")
        return 1
    log_message(config, f"agent 시작 완료. PID={running_pid}")
    return 0


def stop_agent_process(config_path: Path) -> int:
    config = load_agent_config(config_path)
    running_pid = read_running_pid(config_path)
    if not running_pid:
        clear_pid_file(config_path)
        log_message(config, "실행 중인 agent가 없습니다.")
        return 0

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(running_pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.kill(running_pid, signal.SIGTERM)
    except OSError:
        clear_pid_file(config_path)
        log_message(config, "agent 프로세스를 찾지 못했습니다. pid 파일을 정리했습니다.")
        return 0

    time.sleep(1)
    if is_process_running(running_pid):
        log_message(config, f"agent 종료 확인 실패. PID={running_pid}")
        return 1
    clear_pid_file(config_path)
    log_message(config, "agent를 중지했습니다.")
    return 0


def print_agent_status(config_path: Path) -> int:
    config = load_agent_config(config_path)
    running_pid = read_running_pid(config_path)
    if running_pid:
        log_message(config, f"agent 실행 중. PID={running_pid}")
        return 0
    log_message(config, "agent가 실행 중이 아닙니다.")
    return 1


def claim_task(config: AgentConfig) -> ClaimedTask | None:
    query = {
        "agent_id": os.environ.get("COMPUTERNAME") or "local-agent",
        "agent_token": config.agent_token,
    }
    if config.repositories:
        query["repositories"] = ",".join(config.repositories)
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
        log_message(config, f"작업 수신: {task.repository} / {task.event_name}")
        workspace = prepare_repository_workspace(config, task)
        execute_task_in_workspace(config, workspace, task)
    except Exception:
        summary = "failed"
        detail = traceback.format_exc()
        log_message(config, detail.rstrip())
        report_task_completion(config, task.task_id, "failed", summary, detail)
        raise
    else:
        log_message(config, f"작업 완료: {task.repository} / {task.event_name}")
        report_task_completion(config, task.task_id, "completed", summary, detail)


def prepare_repository_workspace(config: AgentConfig, task: ClaimedTask) -> Path:
    config.workspace_root.mkdir(parents=True, exist_ok=True)
    target = config.workspace_root / task.repository.replace("/", "__")
    clone_url = f"https://x-access-token:{task.github_token}@github.com/{task.repository}.git"

    if not target.exists():
        run_command(["git", "clone", "--depth", "1", clone_url, str(target)], config)

    run_command(["git", "-C", str(target), "config", "core.autocrlf", "false"], config)
    run_command(["git", "-C", str(target), "fetch", clone_url, task.default_branch], config)
    run_command(["git", "-C", str(target), "checkout", "-B", task.default_branch, "FETCH_HEAD"], config)
    run_command(["git", "-C", str(target), "reset", "--hard", "FETCH_HEAD"], config)
    return target


def execute_task_in_workspace(config: AgentConfig, workspace: Path, task: ClaimedTask) -> None:
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
        log_message(config, f"작업 실행: {workspace}")
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


def run_command(command: list[str], config: AgentConfig | None = None) -> None:
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if (result.stdout or "").strip():
        log_message(config, (result.stdout or "").rstrip())
    if result.returncode != 0:
        raise RuntimeError(f"명령 실행 실패({result.returncode}): {' '.join(command)}")


def resolve_pid_path(config_path: Path) -> Path:
    return config_path.with_suffix(".pid")


def is_process_running(process_id: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {process_id}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return str(process_id) in (result.stdout or "")
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


def read_running_pid(config_path: Path) -> int | None:
    pid_path = resolve_pid_path(config_path)
    if not pid_path.exists():
        return None
    try:
        process_id = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        pid_path.unlink(missing_ok=True)
        return None
    if not is_process_running(process_id):
        pid_path.unlink(missing_ok=True)
        return None
    return process_id


def ensure_single_instance(config_path: Path, config: AgentConfig) -> None:
    running_pid = read_running_pid(config_path)
    current_pid = os.getpid()
    pid_path = resolve_pid_path(config_path)
    if running_pid and running_pid != current_pid:
        raise RuntimeError(f"이미 다른 agent가 실행 중입니다. PID={running_pid}")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(current_pid), encoding="utf-8")
    log_message(config, f"pid 파일 기록: {pid_path}")


def clear_pid_file(config_path: Path) -> None:
    resolve_pid_path(config_path).unlink(missing_ok=True)


def resolve_background_python() -> Path:
    return Path(sys.executable)


def try_resolve_log_path(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    log_path = data.get("log_path")
    return Path(str(log_path)) if log_path else None


def log_message(config: AgentConfig | None, message: str, *, log_path: Path | None = None) -> None:
    if sys.stdout is not None:
        print(message)
    target_path = log_path or (config.log_path if config else None)
    if not target_path:
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with target_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


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

"""Cloudflare 제어면과 통신하는 로컬 agent 서비스."""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import traceback
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from app import main as bot_main
from app.automation.parsing import build_issue_request, parse_bot_command
from app.codex_provider import interrupt_active_codex_process
from app.output_artifacts import (
    OUTPUT_ARTIFACT_ROOT_ENV,
    ensure_task_output_root,
    get_workspace_output_artifact_root,
)
from app.release_channel import install_standalone_binary, is_newer_version
from app.runtime.comments import configure_output_encoding, post_interrupted_comment
from app.versioning import APP_VERSION
from app.workspace_state import cleanup_stale_workspaces, infer_scope_from_workspace, touch_workspace_metadata

DEFAULT_AGENT_CONFIG_PATH = Path.home() / ".issue-to-pr-bot-agent" / "agent-config.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_REFRESH_SECONDS = 1
CENTRAL_CONFIG_REFRESH_SECONDS = 60
DEFAULT_MAX_CONCURRENCY = 2
EXECUTOR_THREAD_CAP = 8
WORKSPACE_GC_INTERVAL_SECONDS = 1800
INTERACTIVE_CONSOLE_MODE = False

if os.name == "nt":
    import msvcrt
else:
    import termios
    import tty


@dataclass(frozen=True)
class AgentConfig:
    control_plane_url: str
    agent_token: str
    workspace_root: Path
    poll_interval_seconds: int = 10
    repositories: list[str] | None = None
    log_path: Path | None = None
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    managed_runtime_path: Path | None = None
    managed_runtime_version: str | None = None
    release_repository: str | None = None


@dataclass(frozen=True)
class CentralAgentConfig:
    repositories: list[str] | None = None
    poll_interval_seconds: int | None = None
    max_concurrency: int | None = None


@dataclass(frozen=True)
class ClaimedTask:
    task_id: str
    event_name: str
    delivery_id: str | None
    repository: str
    default_branch: str
    payload: dict[str, Any]
    github_token: str


@dataclass
class RunningTask:
    task: ClaimedTask
    future: Future[int]
    task_file: Path
    log_path: Path
    pid_file: Path
    started_at: str
    lock_key: str
    workspace_path: Path


class TaskInterrupted(Exception):
    """사용자가 현재 작업을 명시적으로 중단했음을 나타낸다."""


def main(argv: Sequence[str] | None = None) -> int:
    configure_output_encoding()
    raw_args = list(argv) if argv is not None else sys.argv[1:]

    if raw_args[:1] == ["run-task"]:
        parser = build_parser(include_internal=True)
        args = parser.parse_args(raw_args)
    elif raw_args[:1] == ["replace-runtime"]:
        parser = build_parser(include_internal=True)
        args = parser.parse_args(raw_args)
    else:
        parser = build_parser(include_internal=False)
        args = parser.parse_args(raw_args)

    try:
        if args.command == "run-task":
            config = load_agent_config(Path(args.config))
            task = read_task_file(Path(args.task_file))
            return run_claimed_task(config, task, log_path=Path(args.log_path), config_path=Path(args.config))
        if args.command == "replace-runtime":
            return replace_runtime_binary(
                source=Path(args.source),
                target=Path(args.target),
                wait_pid=int(args.wait_pid),
                config_path=Path(args.config),
            )

        config = load_agent_config(Path(args.config))
        run_agent_loop(config, Path(args.config), interactive=sys.stdin.isatty())
        return 0
    except Exception as error:
        config_path = Path(args.config) if getattr(args, "config", None) else None
        log_path = try_resolve_log_path(config_path) if config_path else None
        log_message(None, f"Error: {error}", log_path=log_path)
        return 1

def build_parser(*, include_internal: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue-to-pr-bot-agent",
        description="Cloudflare Worker 제어면과 통신하며 로컬에서 실제 작업을 수행하는 agent입니다.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_AGENT_CONFIG_PATH),
        help="agent 설정 파일 경로입니다.",
    )

    if include_internal:
        subparsers = parser.add_subparsers(dest="command")
        run_task = subparsers.add_parser("run-task", help=argparse.SUPPRESS)
        run_task.add_argument("--config", required=True)
        run_task.add_argument("--task-file", required=True)
        run_task.add_argument("--log-path", required=True)
        replace_runtime = subparsers.add_parser("replace-runtime", help=argparse.SUPPRESS)
        replace_runtime.add_argument("--source", required=True)
        replace_runtime.add_argument("--target", required=True)
        replace_runtime.add_argument("--wait-pid", required=True)
        replace_runtime.add_argument("--config", required=True)
    else:
        parser.set_defaults(command="serve")
    return parser


def load_agent_config(path: Path) -> AgentConfig:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    repositories = data.get("repositories") or None
    return AgentConfig(
        control_plane_url=str(data["control_plane_url"]).rstrip("/"),
        agent_token=str(data["agent_token"]),
        workspace_root=Path(str(data["workspace_root"])),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 10)),
        repositories=[str(item) for item in repositories] if repositories else None,
        log_path=Path(str(data["log_path"])) if data.get("log_path") else None,
        max_concurrency=max(1, int(data.get("max_concurrency", DEFAULT_MAX_CONCURRENCY))),
        managed_runtime_path=Path(str(data["managed_runtime_path"])) if data.get("managed_runtime_path") else None,
        managed_runtime_version=str(data["managed_runtime_version"]) if data.get("managed_runtime_version") else None,
        release_repository=str(data["release_repository"]) if data.get("release_repository") else None,
    )


def run_agent_loop(config: AgentConfig, config_path: Path, *, interactive: bool = False) -> None:
    if interactive:
        run_interactive_console(config, config_path)
        return
    run_supervisor_loop(config, config_path)


def run_supervisor_loop(
    config: AgentConfig,
    config_path: Path,
    stop_event: threading.Event | None = None,
    ready_event: threading.Event | None = None,
) -> None:
    ensure_single_instance(config_path, config)
    clear_runtime_state(config_path)
    log_message(config, f"로컬 agent 시작: {config.control_plane_url}")
    if ready_event is not None:
        ready_event.set()
    central_config = CentralAgentConfig()
    last_refresh_at = 0.0
    last_workspace_gc_at = 0.0
    pending: list[ClaimedTask] = []
    running: dict[str, RunningTask] = {}
    max_executor_workers = max(EXECUTOR_THREAD_CAP, config.max_concurrency)

    try:
        with ThreadPoolExecutor(max_workers=max_executor_workers) as executor:
            while stop_event is None or not stop_event.is_set():
                if time.time() - last_refresh_at >= CENTRAL_CONFIG_REFRESH_SECONDS:
                    try:
                        central_config = fetch_central_agent_config(config)
                        last_refresh_at = time.time()
                    except Exception as error:
                        log_message(config, f"중앙 설정 갱신 실패: {error}")

                effective_config = merge_agent_config(config, central_config)
                effective_concurrency = max(1, effective_config.max_concurrency)
                reap_finished_tasks(effective_config, config_path, running)
                if time.time() - last_workspace_gc_at >= WORKSPACE_GC_INTERVAL_SECONDS:
                    active_workspaces = {runtime.workspace_path for runtime in running.values()}
                    for result in cleanup_stale_workspaces(
                        effective_config.workspace_root,
                        active_workspaces=active_workspaces,
                    ):
                        log_message(
                            effective_config,
                            f"오래된 workspace 정리: {result.workspace_path} ({result.reason})",
                        )
                    last_workspace_gc_at = time.time()
                start_pending_tasks(executor, effective_config, config_path, pending, running, effective_concurrency)
                prefetch_tasks(effective_config, pending, running, effective_concurrency)
                start_pending_tasks(executor, effective_config, config_path, pending, running, effective_concurrency)
                sync_runtime_state(config_path, running)
                time.sleep(min(max(effective_config.poll_interval_seconds, 1), SUPERVISOR_REFRESH_SECONDS))
    finally:
        clear_runtime_state(config_path)
        clear_pid_file(config_path)


def run_interactive_console(config: AgentConfig, config_path: Path) -> None:
    global INTERACTIVE_CONSOLE_MODE
    INTERACTIVE_CONSOLE_MODE = True
    stop_event = threading.Event()
    ready_event = threading.Event()
    supervisor = threading.Thread(
        target=run_supervisor_loop,
        args=(config, config_path, stop_event, ready_event),
        name="issue-to-pr-bot-agent-supervisor",
        daemon=True,
    )
    supervisor.start()
    ready_event.wait(timeout=2)
    print(format_console_help_banner())
    try:
        while supervisor.is_alive():
            try:
                raw = input("agent> ").strip()
            except EOFError:
                break
            except KeyboardInterrupt:
                print()
                print("입력을 취소했습니다. 종료하려면 `quit`를 입력하세요.")
                continue
            if not raw:
                continue
            if not dispatch_console_command(config_path, raw):
                break
    finally:
        INTERACTIVE_CONSOLE_MODE = False
        stop_event.set()
        supervisor.join(timeout=5)


def dispatch_console_command(config_path: Path, raw: str) -> bool:
    parts = raw.split()
    command = parts[0].lower()
    if command in {"quit", "exit"}:
        force_exit = len(parts) >= 2 and parts[1].lower() == "now"
        running_entries = get_running_entries(config_path)
        if running_entries and not force_exit:
            print("실행 중인 task가 있습니다.")
            print("  - supervisor만 종료하려면: quit now")
            print("  - 실행 중인 task를 모두 중지하려면: stop all")
            print("  - 현재 상태를 보려면: ps")
            return True
        return False
    if command == "stop" and len(parts) >= 2 and parts[1].lower() == "all":
        stop_all_running_tasks(config_path)
        return True
    if command == "help":
        print(format_console_help_detail())
        return True
    if command == "ps":
        print_running_tasks(config_path)
        return True
    if command == "status":
        print_agent_status(config_path)
        return True
    if command == "update":
        return run_console_update(config_path)
    if command == "logs":
        return handle_console_logs_command(config_path, parts)
    if command == "cancel":
        if len(parts) != 2:
            print("사용법: cancel <task-id>")
            return True
        cancel_running_task(config_path, parts[1])
        return True
    print(f"알 수 없는 명령입니다: {raw}")
    print("`help`를 입력하면 사용 가능한 명령을 볼 수 있습니다.")
    return True


def handle_console_logs_command(config_path: Path, parts: list[str]) -> bool:
    follow = "-f" in parts or "--follow" in parts
    filtered = [item for item in parts[1:] if item not in {"-f", "--follow"}]
    if not filtered:
        print("사용법: logs latest [-f] | logs <task-id> [-f]")
        return True
    target = filtered[0]
    if target == "latest":
        stream_task_logs(config_path, task_id=None, latest=True, follow=follow)
        return True
    stream_task_logs(config_path, task_id=target, latest=False, follow=follow)
    return True


def run_console_update(config_path: Path) -> bool:
    config = load_agent_config(config_path)
    message = install_latest_agent_runtime(config, config_path)
    print(message)
    return True


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
    if config.log_path is None:
        raise RuntimeError("agent log_path가 설정되어 있지 않습니다.")
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    with config.log_path.open("a", encoding="utf-8") as handle:
        subprocess.Popen([str(executable), "-m", "app.agent_runner", "--config", str(config_path)], cwd=str(REPOSITORY_ROOT), stdout=handle, stderr=handle, stdin=subprocess.DEVNULL, creationflags=creationflags, close_fds=True)
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
        terminate_process_tree(running_pid)
    except OSError:
        clear_pid_file(config_path)
        clear_runtime_state(config_path)
        log_message(config, "agent 프로세스를 찾지 못했습니다. pid 파일을 정리했습니다.")
        return 0
    time.sleep(1)
    if is_process_running(running_pid):
        log_message(config, f"agent 종료 확인 실패. PID={running_pid}")
        return 1
    clear_pid_file(config_path)
    clear_runtime_state(config_path)
    log_message(config, "agent를 중지했습니다.")
    return 0

def print_agent_status(config_path: Path) -> int:
    config = load_agent_config(config_path)
    running_pid = read_running_pid(config_path)
    if running_pid:
        state = read_runtime_state(config_path)
        running_count = len(state.get("running", []))
        print("AGENT STATUS")
        print(f"PID            {running_pid}")
        print(f"RUNNING TASKS  {running_count}")
        print(f"CONTROL PLANE  {config.control_plane_url}")
        print(f"VERSION        {config.managed_runtime_version or APP_VERSION}")
        return 0
    print("AGENT STATUS")
    print("STATE          stopped")
    return 1


def print_running_tasks(config_path: Path) -> int:
    state = read_runtime_state(config_path)
    running = state.get("running", [])
    if not running:
        print("현재 실행 중인 task가 없습니다.")
        return 0
    rows: list[dict[str, str]] = []
    for entry in running:
        rows.append(
            {
                "TASK ID": str(entry["task_id"])[:12],
                "REPO": shorten_text(str(entry["repository"]), 24),
                "EVENT": shorten_text(str(entry["event_name"]), 20),
                "SCOPE": shorten_text(extract_scope_name(str(entry.get("lock_key") or "unknown")), 16),
                "PID": str(entry.get("pid") or "-"),
                "ELAPSED": format_elapsed(entry.get("started_at")),
            }
        )
    print(format_table(rows, ["TASK ID", "REPO", "EVENT", "SCOPE", "PID", "ELAPSED"]))
    return 0


def format_console_help_banner() -> str:
    return "\n".join(
        [
            r"  ___ ____ ____ _  _ ____    ___  ____       ___  ____ ___ ",
            r"   |  [__  [__  |  | |___ __ |__] |__/ __ __ |__] |  |  |  ",
            r"   |  ___] ___] |__| |___    |    |  \       |__] |__|  |  ",
            "",
            "이슈에서 시작해서 PR까지 곧장 갑니다.",
            "",
            "빠른 시작",
            "  ps                실행 중인 task 목록",
            "  status            agent 상태 요약",
            "  update            최신 agent 설치",
            "  logs latest -f    최신 task 로그 따라가기",
            "  help              전체 명령 보기",
            "  quit              콘솔 종료",
        ]
    )


def format_console_help_detail() -> str:
    return "\n".join(
        [
            "COMMANDS",
            "  ps                    현재 실행 중인 task 목록",
            "  status                agent 상태 요약",
            "  update                최신 agent 설치",
            "  logs latest           가장 최근 task 로그",
            "  logs latest -f        가장 최근 task 로그 스트리밍 (q 로 종료)",
            "  logs <task-id>        특정 task 로그",
            "  logs <task-id> -f     특정 task 로그 스트리밍 (q 로 종료)",
            "  cancel <task-id>      실행 중인 task 취소",
            "  stop all              실행 중인 task를 모두 취소",
            "  quit                  task가 없을 때 serve 종료",
            "  quit now              실행 중 task를 남기고 serve 종료",
        ]
    )


def format_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    widths: dict[str, int] = {}
    for column in columns:
        widths[column] = max(len(column), *(len(str(row.get(column, ""))) for row in rows))
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    separator = "  ".join("-" * widths[column] for column in columns)
    body = [
        "  ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns)
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def shorten_text(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def extract_scope_name(lock_key: str) -> str:
    if ":" not in lock_key:
        return lock_key
    return lock_key.split(":", maxsplit=1)[1]


def format_elapsed(started_at: Any) -> str:
    if not started_at:
        return "-"
    try:
        started = datetime.fromisoformat(str(started_at))
    except ValueError:
        return "-"
    delta = max(0, int((datetime.now() - started).total_seconds()))
    minutes, seconds = divmod(delta, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02}m"
    if minutes:
        return f"{minutes}m{seconds:02}s"
    return f"{seconds}s"


def install_latest_agent_runtime(config: AgentConfig, config_path: Path) -> str:
    runtime_path = config.managed_runtime_path
    release_repository = config.release_repository
    if runtime_path is None or not release_repository:
        return "관리 대상 agent 설치가 아니라 업데이트를 건너뜁니다."

    install_root = runtime_path.parent
    if not runtime_path.exists():
        target_path, _, target_version = install_standalone_binary(
            "agent",
            install_root,
            repository=release_repository,
            target_name=runtime_path.name,
        )
        update_agent_config_runtime_metadata(config_path, target_path, target_version)
        return f"agent를 새로 설치했습니다: {target_path} ({target_version})"

    target_path, _, target_version = install_standalone_binary(
        "agent",
        install_root,
        repository=release_repository,
        target_name=f".staged-{runtime_path.name}",
    )
    if not is_newer_version(target_version, config.managed_runtime_version or APP_VERSION):
        target_path.unlink(missing_ok=True)
        return f"이미 최신 버전입니다: {config.managed_runtime_version or APP_VERSION}"

    if os.name != "nt":
        shutil.copy2(target_path, runtime_path)
        current_mode = runtime_path.stat().st_mode
        runtime_path.chmod(current_mode | 0o111)
        target_path.unlink(missing_ok=True)
        update_agent_config_runtime_metadata(config_path, runtime_path, target_version)
        return f"agent를 업데이트했습니다: {target_version}"

    spawn_runtime_replacement_helper(target_path, runtime_path, config_path)
    return (
        f"업데이트를 예약했습니다: {target_version}. "
        "현재 콘솔을 종료하면 새 agent가 교체된 뒤 자동으로 다시 시작됩니다."
    )


def claim_task(config: AgentConfig) -> ClaimedTask | None:
    query = {"agent_id": os.environ.get("COMPUTERNAME") or "local-agent", "agent_token": config.agent_token}
    if config.repositories:
        query["repositories"] = ",".join(config.repositories)
    url = f"{config.control_plane_url}/api/tasks/claim?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {config.agent_token}", "User-Agent": "Mozilla/5.0 issue-to-pr-bot-agent"}, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    task = payload.get("task")
    if not task:
        return None
    return ClaimedTask(task_id=str(task["id"]), event_name=str(task["eventName"]), delivery_id=str(task.get("deliveryId")) if task.get("deliveryId") else None, repository=str(task["repository"]), default_branch=str(task.get("defaultBranch") or "main"), payload=task["payload"], github_token=str(task["githubToken"]))


def fetch_central_agent_config(config: AgentConfig) -> CentralAgentConfig:
    url = f"{config.control_plane_url}/api/agent/config"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {config.agent_token}", "User-Agent": "Mozilla/5.0 issue-to-pr-bot-agent"}, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    repositories = payload.get("repositories") or None
    return CentralAgentConfig(
        repositories=[str(item).strip() for item in repositories if str(item).strip()] if repositories else None,
        poll_interval_seconds=int(payload["pollIntervalSeconds"]) if payload.get("pollIntervalSeconds") else None,
        max_concurrency=int(payload["maxConcurrency"]) if payload.get("maxConcurrency") else None,
    )


def merge_agent_config(config: AgentConfig, central_config: CentralAgentConfig) -> AgentConfig:
    repositories = central_config.repositories if central_config.repositories is not None else config.repositories
    poll_interval_seconds = central_config.poll_interval_seconds if central_config.poll_interval_seconds is not None else config.poll_interval_seconds
    max_concurrency = central_config.max_concurrency if central_config.max_concurrency is not None else config.max_concurrency
    return AgentConfig(
        control_plane_url=config.control_plane_url,
        agent_token=config.agent_token,
        workspace_root=config.workspace_root,
        poll_interval_seconds=max(1, poll_interval_seconds),
        repositories=repositories,
        log_path=config.log_path,
        max_concurrency=max(1, max_concurrency),
    )


def run_claimed_task(config: AgentConfig, task: ClaimedTask, *, log_path: Path | None = None, config_path: Path | None = None) -> int:
    summary = "completed"
    detail = ""
    try:
        log_message(config, f"작업 수신: {task.repository} / {task.event_name} / task_id={task.task_id} / delivery_id={task.delivery_id or 'unknown'} / comment_id={extract_comment_id(task.payload) or 'unknown'} / action={extract_action(task.payload) or 'unknown'}", log_path=log_path)
        if should_execute_task(task):
            workspace = prepare_repository_workspace(config, task, log_path=log_path)
            execute_task_in_workspace(config, workspace, task, log_path=log_path)
        else:
            summary = "ignored"
            detail = f"Skipped unsupported webhook action: event={task.event_name}, action={extract_action(task.payload) or 'unknown'}"
            log_message(config, f"작업 건너뜀: {detail}", log_path=log_path)
    except TaskInterrupted as error:
        summary = "interrupted"
        detail = str(error)
        log_message(config, f"작업 중단: {detail}", log_path=log_path)
        report_task_completion(config, task.task_id, "completed", summary, detail)
        return 0
    except Exception:
        summary = "failed"
        detail = traceback.format_exc()
        log_message(config, detail.rstrip(), log_path=log_path)
        report_task_completion(config, task.task_id, "failed", summary, detail)
        return 1
    else:
        completion_suffix = f" / summary={summary}" if summary != "completed" else ""
        log_message(config, f"작업 완료: {task.repository} / {task.event_name} / task_id={task.task_id}{completion_suffix}", log_path=log_path)
        report_task_completion(config, task.task_id, "completed", summary, detail)
        return 0
    finally:
        if config_path is not None:
            sync_runtime_state(config_path, {})


def prepare_repository_workspace(config: AgentConfig, task: ClaimedTask, *, log_path: Path | None = None) -> Path:
    config.workspace_root.mkdir(parents=True, exist_ok=True)
    target = resolve_workspace_path(config, task)
    scope_type, scope_number = infer_scope_from_workspace(target)
    touch_workspace_metadata(
        target,
        repository=task.repository,
        scope_type=scope_type,
        scope_number=scope_number,
    )
    clone_url = f"https://x-access-token:{task.github_token}@github.com/{task.repository}.git"
    public_url = f"https://github.com/{task.repository}.git"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        run_command(["git", "clone", "--depth", "1", clone_url, str(target)], config, log_path=log_path)
    run_command(["git", "-C", str(target), "config", "core.autocrlf", "false"], config, log_path=log_path)
    run_command(["git", "-C", str(target), "remote", "set-url", "origin", public_url], config, log_path=log_path)
    run_command(["git", "-C", str(target), "fetch", clone_url, task.default_branch], config, log_path=log_path)
    run_command(["git", "-C", str(target), "reset", "--hard"], config, log_path=log_path)
    run_command(["git", "-C", str(target), "clean", "-fd"], config, log_path=log_path)
    run_command(["git", "-C", str(target), "checkout", "-B", task.default_branch, "FETCH_HEAD"], config, log_path=log_path)
    run_command(["git", "-C", str(target), "reset", "--hard", "FETCH_HEAD"], config, log_path=log_path)
    run_command(["git", "-C", str(target), "clean", "-fd"], config, log_path=log_path)
    cleanup_workspace_output_artifacts(target, task.repository)
    return target


def cleanup_workspace_output_artifacts(workspace: Path, repository: str) -> None:
    # Codex 산출물은 workspace 안 경로만 정리한다.
    shutil.rmtree(get_workspace_output_artifact_root(workspace), ignore_errors=True)


def execute_task_in_workspace(config: AgentConfig, workspace: Path, task: ClaimedTask, *, log_path: Path | None = None) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        event_path = Path(temp_dir) / "github-event.json"
        event_path.write_text(json.dumps(task.payload, ensure_ascii=False), encoding="utf-8")
        request = build_issue_request(task.payload)
        output_root = get_workspace_output_artifact_root(workspace)
        env_updates = {
            "BOT_CREATE_PR": "1",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": task.repository,
            "GITHUB_REF_NAME": task.default_branch,
            "GITHUB_TOKEN": task.github_token,
            "BOT_GITHUB_TOKEN": task.github_token,
            "BOT_RESET_WORKTREE": "1",
            OUTPUT_ARTIFACT_ROOT_ENV: str(output_root),
        }
        log_message(config, f"작업 실행: {workspace}", log_path=log_path)
        with temporary_env(env_updates), change_directory(workspace):
            try:
                ensure_task_output_root(request)
                bot_main.main()
                touch_workspace_metadata(workspace)
            except KeyboardInterrupt as error:
                interrupt_active_codex_process()
                command = parse_bot_command(request.comment_body)
                post_interrupted_comment(request, command)
                raise TaskInterrupted("사용자 요청으로 현재 작업을 중단했고 결과 댓글을 남겼습니다.") from error


def report_task_completion(config: AgentConfig, task_id: str, status: str, summary: str, detail: str) -> None:
    url = f"{config.control_plane_url}/api/tasks/{task_id}/complete"
    payload = json.dumps({"status": status, "summary": summary, "detail": detail[:4000]}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.agent_token}", "User-Agent": "Mozilla/5.0 issue-to-pr-bot-agent"}, method="POST")
    with urllib.request.urlopen(request, timeout=30):
        return


def run_command(command: list[str], config: AgentConfig | None = None, *, log_path: Path | None = None) -> None:
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if (result.stdout or "").strip():
        log_message(config, (result.stdout or "").rstrip(), log_path=log_path)
    if result.returncode != 0:
        raise RuntimeError(f"명령 실행 실패({result.returncode}): {' '.join(command)}")

def extract_comment_id(payload: dict[str, Any]) -> int | None:
    comment = payload.get("comment") or payload.get("review") or {}
    raw_value = comment.get("id")
    try:
        return int(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        return None


def extract_issue_number(payload: dict[str, Any]) -> int | None:
    issue = payload.get("issue") or {}
    raw_value = issue.get("number")
    try:
        return int(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        return None


def extract_pull_request_number(payload: dict[str, Any]) -> int | None:
    pull_request = payload.get("pull_request")
    if isinstance(pull_request, dict):
        number = pull_request.get("number")
        try:
            if number is not None:
                return int(number)
        except (TypeError, ValueError):
            pass
    issue = payload.get("issue") or {}
    if isinstance(issue, dict) and issue.get("pull_request") is not None:
        raw_value = issue.get("number")
        try:
            return int(raw_value) if raw_value is not None else None
        except (TypeError, ValueError):
            return None
    return None


def extract_action(payload: dict[str, Any]) -> str | None:
    raw_value = payload.get("action")
    return str(raw_value) if raw_value is not None else None


def should_execute_task(task: ClaimedTask) -> bool:
    action = (extract_action(task.payload) or "").lower()
    if task.event_name in {"issue_comment", "pull_request_review_comment"}:
        return action == "created"
    if task.event_name == "pull_request_review":
        return action == "submitted"
    return True


def resolve_pid_path(config_path: Path) -> Path:
    return config_path.with_suffix(".pid")


def resolve_state_path(config_path: Path) -> Path:
    return config_path.with_suffix(".state.json")


def resolve_tasks_root(config_path: Path) -> Path:
    return config_path.parent / "tasks"


def resolve_task_log_path(config_path: Path, task_id: str) -> Path:
    return resolve_tasks_root(config_path) / f"{task_id}.log"


def resolve_task_file_path(config_path: Path, task_id: str) -> Path:
    return resolve_tasks_root(config_path) / f"{task_id}.json"


def resolve_task_pid_path(config_path: Path, task_id: str) -> Path:
    return resolve_tasks_root(config_path) / f"{task_id}.pid"


def is_process_running(process_id: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {process_id}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", check=False)
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


def clear_runtime_state(config_path: Path) -> None:
    resolve_state_path(config_path).unlink(missing_ok=True)


def resolve_background_python() -> Path:
    return Path(sys.executable)


def resolve_task_python() -> Path:
    executable = Path(sys.executable)
    if os.name != "nt":
        return executable
    pythonw = executable.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else executable


def try_resolve_log_path(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    log_path = data.get("log_path")
    return Path(str(log_path)) if log_path else None


def log_message(config: AgentConfig | None, message: str, *, log_path: Path | None = None) -> None:
    if sys.stdout is not None and not INTERACTIVE_CONSOLE_MODE:
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


def prefetch_tasks(config: AgentConfig, pending: list[ClaimedTask], running: dict[str, RunningTask], effective_concurrency: int) -> None:
    prefetch_limit = max(1, effective_concurrency) * 2
    while len(pending) + len(running) < prefetch_limit:
        try:
            task = claim_task(config)
        except Exception as error:
            log_message(config, f"작업 조회 실패: {error}")
            return
        if not task:
            return
        pending.append(task)
        log_message(config, f"큐 적재: {task.repository} / {task.event_name} / task_id={task.task_id}")


def start_pending_tasks(executor: ThreadPoolExecutor, config: AgentConfig, config_path: Path, pending: list[ClaimedTask], running: dict[str, RunningTask], effective_concurrency: int) -> None:
    available_slots = max(0, effective_concurrency - len(running))
    if available_slots <= 0:
        return
    active_lock_keys = {runtime.lock_key for runtime in running.values()}
    remaining: list[ClaimedTask] = []
    for task in pending:
        if available_slots <= 0:
            remaining.append(task)
            continue
        lock_key = resolve_task_lock_key(task)
        if lock_key in active_lock_keys:
            remaining.append(task)
            continue
        runtime = run_task_process(config, config_path, task, executor=executor)
        running[task.task_id] = runtime
        active_lock_keys.add(runtime.lock_key)
        available_slots -= 1
        log_message(
            config,
            f"task 시작: {task.repository} / {task.event_name} / task_id={task.task_id} / "
            f"lock={runtime.lock_key} / log={runtime.log_path.name}",
        )
    pending[:] = remaining


def run_task_process(config: AgentConfig, config_path: Path, task: ClaimedTask, *, executor: ThreadPoolExecutor | None = None) -> RunningTask:
    task_file = resolve_task_file_path(config_path, task.task_id)
    log_path = resolve_task_log_path(config_path, task.task_id)
    pid_file = resolve_task_pid_path(config_path, task.task_id)
    workspace_path = resolve_workspace_path(config, task)
    lock_key = resolve_task_lock_key(task)
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(serialize_task(task), encoding="utf-8")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(run_task_subprocess, config_path, task_file, log_path, pid_file)
    return RunningTask(
        task=task,
        future=future,
        task_file=task_file,
        log_path=log_path,
        pid_file=pid_file,
        started_at=datetime.now().isoformat(timespec="seconds"),
        lock_key=lock_key,
        workspace_path=workspace_path,
    )


def reap_finished_tasks(config: AgentConfig, config_path: Path, running: dict[str, RunningTask]) -> None:
    finished: list[str] = []
    for task_id, runtime in running.items():
        if not runtime.future.done():
            continue
        try:
            returncode = runtime.future.result()
        except Exception as error:
            log_message(config, f"task 종료 오류: {runtime.task.repository} / {runtime.task.event_name} / task_id={task_id} / error={error}")
        else:
            summary = "성공" if returncode == 0 else f"실패(returncode={returncode})"
            log_message(config, f"task 종료: {runtime.task.repository} / {runtime.task.event_name} / task_id={task_id} / {summary}")
        runtime.pid_file.unlink(missing_ok=True)
        finished.append(task_id)
    for task_id in finished:
        running.pop(task_id, None)
    if finished:
        sync_runtime_state(config_path, running)


def run_task_subprocess(config_path: Path, task_file: Path, log_path: Path, pid_file: Path) -> int:
    with log_path.open("a", encoding="utf-8") as handle:
        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        process = subprocess.Popen(
            [
                str(resolve_task_python()),
                "-m",
                "app.agent_runner",
                "run-task",
                "--config",
                str(config_path),
                "--task-file",
                str(task_file),
                "--log-path",
                str(log_path),
            ],
            cwd=str(REPOSITORY_ROOT),
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
            close_fds=True,
        )
        pid_file.write_text(str(process.pid), encoding="utf-8")
        return process.wait()


def wait_for_task_runtime(config: AgentConfig, runtime: RunningTask) -> int:
    returncode = runtime.future.result()
    summary = "성공" if returncode == 0 else f"실패(returncode={returncode})"
    log_message(config, f"task 종료: {runtime.task.repository} / {runtime.task.event_name} / task_id={runtime.task.task_id} / {summary}")
    return returncode


def serialize_task(task: ClaimedTask) -> str:
    return json.dumps({"id": task.task_id, "eventName": task.event_name, "deliveryId": task.delivery_id, "repository": task.repository, "defaultBranch": task.default_branch, "payload": task.payload, "githubToken": task.github_token}, ensure_ascii=False, indent=2)


def read_task_file(task_file: Path) -> ClaimedTask:
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    return ClaimedTask(task_id=str(payload["id"]), event_name=str(payload["eventName"]), delivery_id=str(payload.get("deliveryId")) if payload.get("deliveryId") else None, repository=str(payload["repository"]), default_branch=str(payload.get("defaultBranch") or "main"), payload=payload["payload"], github_token=str(payload["githubToken"]))

def read_runtime_state(config_path: Path) -> dict[str, Any]:
    state_path = resolve_state_path(config_path)
    if not state_path.exists():
        return {"running": []}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"running": []}


def get_running_entries(config_path: Path) -> list[dict[str, Any]]:
    state = read_runtime_state(config_path)
    return list(state.get("running", []))


def sync_runtime_state(config_path: Path, running: dict[str, RunningTask]) -> None:
    entries: list[dict[str, Any]] = []
    for runtime in running.values():
        pid = read_task_pid(runtime.pid_file)
        entries.append({
            "task_id": runtime.task.task_id,
            "repository": runtime.task.repository,
            "event_name": runtime.task.event_name,
            "delivery_id": runtime.task.delivery_id,
            "comment_id": extract_comment_id(runtime.task.payload),
            "started_at": runtime.started_at,
            "pid": pid,
            "lock_key": runtime.lock_key,
            "log_path": str(runtime.log_path),
            "task_file": str(runtime.task_file),
            "workspace_path": str(runtime.workspace_path),
        })
    write_json_atomically(resolve_state_path(config_path), {"updated_at": datetime.now().isoformat(timespec="seconds"), "running": entries})


def read_task_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        return None
    return pid if is_process_running(pid) else None


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def stream_task_logs(config_path: Path, *, task_id: str | None, latest: bool, follow: bool) -> int:
    log_path = resolve_requested_log_path(config_path, task_id=task_id, latest=latest)
    if log_path is None or not log_path.exists():
        print("확인할 로그 파일이 없습니다.")
        return 1
    if not follow:
        print(log_path.read_text(encoding="utf-8", errors="replace"))
        return 0
    print("로그 스트리밍 중입니다. 종료하려면 `q`를 누르세요.")
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            while True:
                if should_stop_log_stream():
                    print()
                    print("로그 스트리밍을 종료하고 agent 프롬프트로 돌아갑니다.")
                    return 0
                line = handle.readline()
                if line:
                    print(line, end="")
                    continue
                time.sleep(0.2)
    except KeyboardInterrupt:
        print()
        print("로그 스트리밍을 종료하고 agent 프롬프트로 돌아갑니다.")
        return 0


def should_stop_log_stream() -> bool:
    if os.name == "nt":
        return should_stop_log_stream_windows()
    return should_stop_log_stream_posix()


def should_stop_log_stream_windows() -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return False
    if not msvcrt.kbhit():
        return False
    key = msvcrt.getwch()
    return key.lower() == "q"


def should_stop_log_stream_posix() -> bool:
    if sys.stdin is None or not sys.stdin.isatty():
        return False

    file_descriptor = sys.stdin.fileno()
    original_settings = termios.tcgetattr(file_descriptor)
    try:
        tty.setcbreak(file_descriptor)
        readable, _, _ = select.select([file_descriptor], [], [], 0)
        if not readable:
            return False
        key = os.read(file_descriptor, 1).decode("utf-8", errors="ignore")
        return key.lower() == "q"
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, original_settings)


def resolve_requested_log_path(config_path: Path, *, task_id: str | None, latest: bool) -> Path | None:
    if task_id:
        return resolve_requested_task_log_path(config_path, task_id)
    if latest:
        return resolve_latest_log_path(config_path)
    return None


def resolve_requested_task_log_path(config_path: Path, task_id: str) -> Path | None:
    state_entries = get_running_entries(config_path)
    exact_entry = next((item for item in state_entries if str(item.get("task_id") or "") == task_id), None)
    if exact_entry is not None:
        return Path(str(exact_entry.get("log_path")))

    prefix_matches = [
        item
        for item in state_entries
        if str(item.get("task_id") or "").startswith(task_id)
    ]
    if len(prefix_matches) == 1:
        return Path(str(prefix_matches[0].get("log_path")))
    if len(prefix_matches) > 1:
        return None

    exact_path = resolve_task_log_path(config_path, task_id)
    if exact_path.exists():
        return exact_path

    candidates = sorted(resolve_tasks_root(config_path).glob(f"{task_id}*.log"))
    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_latest_log_path(config_path: Path) -> Path | None:
    state_entries = get_running_entries(config_path)
    if state_entries:
        latest_entry = max(
            state_entries,
            key=lambda item: str(item.get("started_at") or ""),
        )
        log_path = latest_entry.get("log_path")
        if log_path:
            return Path(str(log_path))

    candidates = sorted(
        resolve_tasks_root(config_path).glob("*.log"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def resolve_workspace_path(config: AgentConfig, task: ClaimedTask) -> Path:
    repository_segment = task.repository.replace("/", "__")
    work_item_segment = resolve_workspace_segment(task)
    return config.workspace_root / repository_segment / work_item_segment


def resolve_workspace_segment(task: ClaimedTask) -> str:
    pull_request_number = extract_pull_request_number(task.payload)
    if pull_request_number is not None:
        return f"pr-{pull_request_number}"
    issue_number = extract_issue_number(task.payload)
    if issue_number is not None:
        return f"issue-{issue_number}"
    return f"task-{task.task_id}"


def resolve_task_lock_key(task: ClaimedTask) -> str:
    return f"{task.repository}:{resolve_workspace_segment(task)}"


def cancel_running_task(config_path: Path, task_id: str) -> int:
    config = load_agent_config(config_path)
    state = read_runtime_state(config_path)
    running_entries = list(state.get("running", []))
    exact_entry = next((item for item in running_entries if item.get("task_id") == task_id), None)
    if exact_entry is not None:
        entry = exact_entry
    else:
        prefix_matches = [item for item in running_entries if str(item.get("task_id") or "").startswith(task_id)]
        if len(prefix_matches) > 1:
            print("여러 task가 같은 prefix와 일치합니다. 전체 task id를 사용하세요.")
            for item in prefix_matches:
                print(f"  - {item.get('task_id')}")
            return 1
        entry = prefix_matches[0] if prefix_matches else None
    if not entry:
        log_message(config, f"취소할 실행 중 task를 찾지 못했습니다: {task_id}")
        return 1
    resolved_task_id = str(entry.get("task_id"))
    pid = entry.get("pid")
    if not pid:
        log_message(config, f"task PID를 찾지 못했습니다: {resolved_task_id}")
        return 1
    terminate_process_tree(int(pid))
    detail = "사용자 요청으로 task를 취소했습니다."
    try:
        report_task_completion(config, resolved_task_id, "completed", "cancelled", detail)
    except Exception as error:
        log_message(config, f"취소 결과 보고 실패: {error}")
    log_message(config, f"task를 취소했습니다. task_id={resolved_task_id}, pid={pid}")
    return 0


def stop_all_running_tasks(config_path: Path) -> int:
    running_entries = get_running_entries(config_path)
    if not running_entries:
        print("중지할 실행 중 task가 없습니다.")
        return 0

    cancelled = 0
    for entry in running_entries:
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            continue
        if cancel_running_task(config_path, task_id) == 0:
            cancelled += 1
    print(f"실행 중 task {cancelled}개에 중지 요청을 보냈습니다.")
    return 0


def update_agent_config_runtime_metadata(config_path: Path, runtime_path: Path, version: str) -> None:
    payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    payload["managed_runtime_path"] = str(runtime_path)
    payload["managed_runtime_version"] = version
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def spawn_runtime_replacement_helper(source: Path, target: Path, config_path: Path) -> None:
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    subprocess.Popen(
        [
            str(source),
            "replace-runtime",
            "--source",
            str(source),
            "--target",
            str(target),
            "--wait-pid",
            str(os.getpid()),
            "--config",
            str(config_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
        close_fds=True,
    )


def replace_runtime_binary(source: Path, target: Path, *, wait_pid: int, config_path: Path) -> int:
    deadline = time.time() + 120
    while time.time() < deadline and is_process_running(wait_pid):
        time.sleep(0.5)
    if is_process_running(wait_pid):
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if os.name != "nt":
        current_mode = target.stat().st_mode
        target.chmod(current_mode | 0o111)
    update_agent_config_runtime_metadata(config_path, target, APP_VERSION)

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0

    subprocess.Popen(
        [str(target), "--config", str(config_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
        close_fds=True,
    )
    return 0


def terminate_process_tree(process_id: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process_id), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    os.kill(process_id, signal.SIGTERM)


if __name__ == "__main__":
    raise SystemExit(main())

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import shutil as shutil_module
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from time import perf_counter

from app.bot import BotCommand, BotRuntimeOptions
from app.workspace_state import (
    invalidate_codex_session,
    mark_codex_session_ready,
    resolve_workspace_codex_home_dir,
    should_resume_codex_session,
)


@dataclass(frozen=True)
class ProviderRunResult:
    output: str
    duration_seconds: float = 0.0
    prompt_chars: int = 0


@dataclass(frozen=True)
class StreamRunResult:
    output: str
    forced_completion: bool = False


ALLOWED_EFFORTS = {"low", "medium", "high", "xhigh"}
HEARTBEAT_SECONDS = 30.0
FINAL_MESSAGE_QUIET_SECONDS = 20.0
LATEST_CODEX_MODEL = "gpt-5.4"
ACTIVE_CODEX_PROCESS: subprocess.Popen[str] | None = None
ACTIVE_CODEX_LOCK = threading.Lock()


def run_codex_prompt(
    workspace: Path,
    prompt: str,
    bot_command: BotCommand | None = None,
    runtime_options: BotRuntimeOptions | None = None,
    output_last_message: Path | None = None,
) -> ProviderRunResult:
    managed_output_last_message = output_last_message
    if managed_output_last_message is None:
        managed_output_last_message = Path(tempfile.gettempdir()) / "issue-to-pr-bot-provider-last-message.txt"

    started_at = perf_counter()
    effort = get_effort(bot_command, runtime_options)
    with tempfile.TemporaryDirectory(prefix="codex-gh-config-") as gh_config_dir:
        codex_home_dir = prepare_persistent_codex_home(workspace)
        reuse_session = should_resume_codex_session(workspace)
        managed_output_last_message.unlink(missing_ok=True)
        stream_result, return_code = execute_codex_command(
            workspace=workspace,
            prompt=prompt,
            effort=effort,
            gh_config_dir=gh_config_dir,
            codex_home_dir=codex_home_dir,
            output_last_message=managed_output_last_message,
            started_at=started_at,
            reuse_session=reuse_session,
        )
        if return_code != 0 and reuse_session and should_retry_with_fresh_session(stream_result.output):
            invalidate_codex_session(workspace)
            managed_output_last_message.unlink(missing_ok=True)
            stream_result, return_code = execute_codex_command(
                workspace=workspace,
                prompt=prompt,
                effort=effort,
                gh_config_dir=gh_config_dir,
                codex_home_dir=codex_home_dir,
                output_last_message=managed_output_last_message,
                started_at=started_at,
                reuse_session=False,
            )
    duration_seconds = perf_counter() - started_at
    output = stream_result.output

    success = return_code == 0 or (
        stream_result.forced_completion and has_usable_last_message(managed_output_last_message)
    )

    if not success:
        detail = format_provider_output(output) if output.strip() else "(no codex output)"
        raise RuntimeError(f"Codex execution failed ({return_code})\n{detail}")

    mark_codex_session_ready(workspace, resumed=reuse_session)

    if managed_output_last_message.exists():
        return ProviderRunResult(
            output=managed_output_last_message.read_text(encoding="utf-8"),
            duration_seconds=duration_seconds,
            prompt_chars=len(prompt),
        )
    return ProviderRunResult(output=output, duration_seconds=duration_seconds, prompt_chars=len(prompt))


def write_prompt(process: subprocess.Popen[str], prompt: str) -> None:
    if process.stdin is None:
        return
    process.stdin.write(prompt)
    process.stdin.close()


def set_active_codex_process(process: subprocess.Popen[str] | None) -> None:
    global ACTIVE_CODEX_PROCESS
    with ACTIVE_CODEX_LOCK:
        ACTIVE_CODEX_PROCESS = process


def interrupt_active_codex_process() -> bool:
    with ACTIVE_CODEX_LOCK:
        process = ACTIVE_CODEX_PROCESS
    if process is None or process.poll() is not None:
        return False

    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            return False
    return True


def build_codex_environment(gh_config_dir: str, codex_home_dir: str | Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "BOT_GITHUB_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_PAT",
        "GITHUB_API_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GIT_ASKPASS",
    ):
        env.pop(key, None)
    codex_home_path = Path(codex_home_dir)
    home_dir = resolve_home_dir(codex_home_dir)
    env["GH_CONFIG_DIR"] = gh_config_dir
    env["CODEX_HOME"] = str(codex_home_path)
    env["HOME"] = home_dir
    env["USERPROFILE"] = home_dir
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["NO_COLOR"] = "1"
    return env


def resolve_home_dir(codex_home_dir: str | Path) -> str:
    raw = str(codex_home_dir)
    if "\\" in raw and "/" not in raw:
        return str(PureWindowsPath(raw).parent)
    return str(Path(raw).parent)


def prepare_persistent_codex_home(workspace: Path) -> Path:
    temp_root = resolve_workspace_codex_home_dir(workspace).parent
    source_dir = resolve_codex_home()
    target_dir = temp_root / ".codex"
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("auth.json", "config.toml"):
        source_path = source_dir / filename
        if source_path.exists():
            shutil.copy2(source_path, target_dir / filename)
    return target_dir


def resolve_codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))


def stream_codex_output(
    process: subprocess.Popen[str],
    started_at: float,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    last_message_path: Path | None = None,
    final_message_quiet_seconds: float = FINAL_MESSAGE_QUIET_SECONDS,
) -> StreamRunResult:
    if process.stdout is None:
        return StreamRunResult(output="")

    lines: list[str] = []
    output_queue: queue.Queue[str | None] = queue.Queue()

    def enqueue_output() -> None:
        try:
            for line in iter(process.stdout.readline, ""):
                output_queue.put(line)
        finally:
            process.stdout.close()
            output_queue.put(None)

    reader = threading.Thread(target=enqueue_output, daemon=True)
    reader.start()

    last_summary: str | None = None
    last_activity_at = started_at
    reader_done = False
    forced_completion = False
    while not reader_done or not output_queue.empty():
        try:
            line = output_queue.get(timeout=1.0)
        except queue.Empty:
            now = perf_counter()
            quiet_seconds = now - last_activity_at
            if (
                not forced_completion
                and process.poll() is None
                and can_force_codex_completion(
                    last_message_path,
                    quiet_seconds=quiet_seconds,
                    grace_seconds=final_message_quiet_seconds,
                )
            ):
                print("[codex-status] 留덉?留??묐떟???뺣낫??Codex 醫낅즺瑜??뺣━?⑸땲??")
                terminate_process(process)
                forced_completion = True
            if not reader_done and now - last_activity_at >= heartbeat_seconds:
                print(f"[codex-status] ?묐떟 ?湲?以?.. elapsed={int(now - started_at)}s")
                last_activity_at = now
            continue

        if line is None:
            reader_done = True
            continue

        lines.append(line)
        summary = classify_codex_output(line)
        if summary and summary != last_summary:
            print(f"[codex-status] {summary}")
            last_summary = summary
        print(line.rstrip("\n"))
        last_activity_at = perf_counter()

    return StreamRunResult(output="".join(lines), forced_completion=forced_completion)


def can_force_codex_completion(
    last_message_path: Path | None,
    quiet_seconds: float,
    grace_seconds: float = FINAL_MESSAGE_QUIET_SECONDS,
) -> bool:
    return quiet_seconds >= grace_seconds and has_usable_last_message(last_message_path)


def has_usable_last_message(last_message_path: Path | None) -> bool:
    if last_message_path is None or not last_message_path.exists() or not last_message_path.is_file():
        return False
    try:
        return bool(last_message_path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        process.kill()


def get_effort(
    bot_command: BotCommand | None,
    runtime_options: BotRuntimeOptions | None = None,
) -> str | None:
    if runtime_options and runtime_options.effort:
        effort = runtime_options.effort
    elif bot_command:
        effort = bot_command.options.get("effort")
    else:
        return None

    if not effort:
        return None

    effort = effort.lower()
    if effort not in ALLOWED_EFFORTS:
        allowed = ", ".join(sorted(ALLOWED_EFFORTS))
        raise ValueError(f"Unsupported effort value: {effort}. Allowed values: {allowed}")
    return effort


def build_codex_command(
    workspace: Path,
    effort: str | None = None,
    output_last_message: Path | None = None,
) -> list[str]:
    command = [resolve_codex_executable(), "-C", str(workspace), "exec", "--model", LATEST_CODEX_MODEL]
    if effort:
        command.extend(["-c", f'reasoning_effort="{effort}"'])
    command.append("--full-auto")
    if output_last_message:
        command.extend(["--output-last-message", str(output_last_message)])
    command.append("-")
    return command


def build_codex_resume_command(
    workspace: Path,
    effort: str | None = None,
    output_last_message: Path | None = None,
) -> list[str]:
    command = [resolve_codex_executable(), "-C", str(workspace), "exec", "resume", "--last", "--model", LATEST_CODEX_MODEL]
    if effort:
        command.extend(["-c", f'reasoning_effort="{effort}"'])
    command.append("--full-auto")
    if output_last_message:
        command.extend(["--output-last-message", str(output_last_message)])
    command.append("-")
    return command


def resolve_codex_executable() -> str:
    if os.name != "nt":
        return "codex"
    for candidate in ("codex.exe", "codex-command-runner.exe", "codex.cmd", "codex"):
        resolved = shutil_module.which(candidate)
        if resolved:
            return resolved
    return "codex"


def execute_codex_command(
    *,
    workspace: Path,
    prompt: str,
    effort: str | None,
    gh_config_dir: str,
    codex_home_dir: Path,
    output_last_message: Path,
    started_at: float,
    reuse_session: bool,
) -> tuple[StreamRunResult, int]:
    command = (
        build_codex_resume_command(workspace, effort=effort, output_last_message=output_last_message)
        if reuse_session
        else build_codex_command(workspace, effort=effort, output_last_message=output_last_message)
    )
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    process = subprocess.Popen(
        command,
        cwd=workspace,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        env=build_codex_environment(gh_config_dir, codex_home_dir),
        creationflags=creationflags,
        startupinfo=startupinfo,
    )
    set_active_codex_process(process)
    try:
        write_prompt(process, prompt)
        stream_result = stream_codex_output(
            process,
            started_at,
            last_message_path=output_last_message,
        )
        return stream_result, process.wait()
    except KeyboardInterrupt:
        interrupt_active_codex_process()
        raise
    finally:
        set_active_codex_process(None)


def should_retry_with_fresh_session(output: str) -> bool:
    lowered = output.lower()
    session_markers = (
        "resume",
        "session",
        "no recorded session",
        "most recent recorded session",
        "failed to load session",
        "unknown session",
    )
    return any(marker in lowered for marker in session_markers)


def classify_codex_output(line: str) -> str | None:
    lowered = line.strip().lower()
    if not lowered:
        return None

    if any(token in lowered for token in ("apply_patch", "*** update file:", "*** add file:", "*** delete file:")):
        return "파일 수정 중"
    if any(token in lowered for token in ("get-content", "select-string", "rg ", "read", "open ", "inspect", "search")):
        return "코드/파일 확인 중"
    if any(
        token in lowered
        for token in ("npm ", "pnpm ", "yarn ", "pytest", "vitest", "eslint", "ruff", "python -m", "cargo test", "go test")
    ):
        return "명령 실행 중"
    if any(token in lowered for token in ("git ", "clone", "fetch", "checkout", "reset", "push", "merge", "rebase")):
        return "git 작업 중"
    if any(token in lowered for token in ("thinking", "analy", "plan", "reasoning", "investigat")):
        return "분석 중"
    if "warn" in lowered:
        return "경고 출력"
    if any(token in lowered for token in ("done", "completed", "finished", "success")):
        return "마무리 중"
    return None


def format_provider_output(output: str) -> str:
    text = output.rstrip()
    if len(text) <= 4_000:
        return text
    return text[:3_980].rstrip() + "\n... (provider output truncated)"

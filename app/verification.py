import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.bot import IssueRequest
from app.config import BotConfig, get_check_commands, load_config
from app.output_artifacts import is_non_publishable_workspace_path
from app.verification_policy import VerificationPlan, build_verification_plan


@dataclass(frozen=True)
class VerificationResult:
    command: str
    output: str


class VerificationError(RuntimeError):
    def __init__(self, command: str, output: str, returncode: int) -> None:
        super().__init__(f"검증 명령 실패({returncode}): {command}")
        self.command = command
        self.output = output
        self.returncode = returncode


def build_hidden_windows_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def run_verification(
    config: BotConfig,
    workspace: Path,
    commands: list[str] | None = None,
) -> list[VerificationResult]:
    configured_commands = commands if commands is not None else get_check_commands(config)
    if not configured_commands:
        print("설정된 테스트 명령이 없어 검증을 건너뜁니다.")
        return []

    results: list[VerificationResult] = []
    for configured_command in configured_commands:
        command = shlex.split(configured_command)
        if not command:
            continue
        resolved_command = resolve_verification_command(command)

        print(f"테스트 명령 실행: {configured_command}")
        try:
            result = subprocess.run(
                resolved_command,
                cwd=workspace,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                **build_hidden_windows_subprocess_kwargs(),
            )
        except FileNotFoundError as error:
            raise VerificationError(configured_command, str(error), 127) from error

        output = result.stdout or ""
        if output.strip():
            print(output.rstrip())

        if result.returncode != 0:
            raise VerificationError(configured_command, output, result.returncode)

        results.append(VerificationResult(command=configured_command, output=output))

    return results


def resolve_verification_command(command: list[str]) -> list[str]:
    if not command:
        return command

    executable = command[0]
    if executable in {"python", "python3"}:
        return [sys.executable, *command[1:]]

    resolved_path = shutil.which(executable)
    if resolved_path:
        return [resolved_path, *command[1:]]

    if os.name != "nt" or Path(executable).suffix:
        return command

    for extension in (".cmd", ".bat", ".exe", ".com"):
        resolved_path = shutil.which(executable + extension)
        if resolved_path:
            return [resolved_path, *command[1:]]
    return command


def resolve_verification_plan(
    config: BotConfig,
    workspace: Path,
    request: IssueRequest | None = None,
) -> VerificationPlan:
    return build_verification_plan(
        candidate_commands=get_check_commands(config),
        changed_files=collect_workspace_changes(workspace),
        request=request,
    )


def collect_workspace_changes(workspace: Path) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "status",
            "--porcelain",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )

    if result.returncode != 0:
        return []

    changed_files: list[str] = []
    output_dir = normalize_output_dir(load_config(workspace).output_dir)
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if not path:
            continue
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        normalized = path.replace("\\", "/")
        if is_output_artifact_path(normalized, output_dir):
            continue
        if normalized not in changed_files:
            changed_files.append(normalized)
    return changed_files


def is_output_artifact_path(path: str, output_dir: str) -> bool:
    return is_non_publishable_workspace_path(path, output_dir)


def normalize_output_dir(output_dir: str) -> str:
    return output_dir.replace("\\", "/").strip("/")

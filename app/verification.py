import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.bot import IssueRequest
from app.config import BotConfig, get_check_commands
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

        print(f"테스트 명령 실행: {configured_command}")
        result = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        output = result.stdout or ""
        if output.strip():
            print(output.rstrip())

        if result.returncode != 0:
            raise VerificationError(configured_command, output, result.returncode)

        results.append(VerificationResult(command=configured_command, output=output))

    return results


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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    if result.returncode != 0:
        return []

    changed_files: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if not path:
            continue
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        normalized = path.replace("\\", "/")
        if normalized not in changed_files:
            changed_files.append(normalized)
    return changed_files

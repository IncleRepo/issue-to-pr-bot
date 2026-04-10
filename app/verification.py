import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import BotConfig, get_check_commands


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


def run_verification(config: BotConfig, workspace: Path) -> list[VerificationResult]:
    configured_commands = get_check_commands(config)
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

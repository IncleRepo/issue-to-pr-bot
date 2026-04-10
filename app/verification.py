import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import BotConfig


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


def run_verification(config: BotConfig, workspace: Path) -> VerificationResult | None:
    command = shlex.split(config.test_command)
    if not command:
        print("설정된 테스트 명령이 없어 검증을 건너뜁니다.")
        return None

    print(f"테스트 명령 실행: {config.test_command}")
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
        raise VerificationError(config.test_command, output, result.returncode)

    return VerificationResult(command=config.test_command, output=output)

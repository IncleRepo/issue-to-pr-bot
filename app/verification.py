import shlex
import subprocess
from pathlib import Path

from app.config import BotConfig


def run_verification(config: BotConfig, workspace: Path) -> None:
    command = shlex.split(config.test_command)
    if not command:
        print("설정된 테스트 명령이 없어 검증을 건너뜁니다.")
        return

    print(f"테스트 명령 실행: {config.test_command}")
    subprocess.run(command, cwd=workspace, check=True)

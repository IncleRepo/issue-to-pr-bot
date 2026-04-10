import shlex
import subprocess
from pathlib import Path

from app.config import load_config


def main() -> None:
    config = load_config(Path.cwd())
    command = shlex.split(config.test_command)
    if not command:
        print("설정된 테스트 명령이 없어 건너뜁니다.")
        return

    print(f"테스트 명령 실행: {config.test_command}")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()

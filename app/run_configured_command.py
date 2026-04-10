from pathlib import Path

from app.config import load_config
from app.verification import run_verification


def main() -> None:
    config = load_config(Path.cwd())
    run_verification(config, Path.cwd())


if __name__ == "__main__":
    main()

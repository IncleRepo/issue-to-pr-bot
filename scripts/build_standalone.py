"""OS별 standalone installer/agent 빌드 스크립트."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from app.release_channel import detect_platform_tag, standalone_archive_name


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = REPOSITORY_ROOT / "build" / "standalone"
RELEASE_ROOT = DIST_ROOT / "release"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PyInstaller로 standalone installer/agent를 빌드합니다.")
    parser.add_argument("--role", choices=("agent", "installer", "all"), default="all")
    parser.add_argument("--clean", action="store_true", help="기존 build/standalone 산출물을 비웁니다.")
    args = parser.parse_args(argv)

    if shutil.which("pyinstaller") is None:
        raise SystemExit("PyInstaller가 필요합니다. `pip install pyinstaller` 후 다시 실행하세요.")

    if args.clean and DIST_ROOT.exists():
        shutil.rmtree(DIST_ROOT)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)

    roles = ("installer", "agent") if args.role == "all" else (args.role,)
    for role in roles:
        build_role(role)
    return 0


def build_role(role: str) -> None:
    entry = REPOSITORY_ROOT / "app" / ("install_manager.py" if role == "installer" else "agent_runner.py")
    name = f"issue-to-pr-bot-{role}"
    platform_tag = detect_platform_tag()
    command = [
        "pyinstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(REPOSITORY_ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(REPOSITORY_ROOT / "build" / "spec"),
        "--add-data",
        build_pyinstaller_data_arg(REPOSITORY_ROOT / "app" / "manager_templates", "app/manager_templates"),
        "--add-data",
        build_pyinstaller_data_arg(REPOSITORY_ROOT / "app" / "worker_templates", "app/worker_templates"),
        str(entry),
    ]
    subprocess.run(command, cwd=str(REPOSITORY_ROOT), check=True)
    built_binary = DIST_ROOT / (name + (".exe" if platform_tag.startswith("windows-") else ""))
    archive_path = RELEASE_ROOT / standalone_archive_name(role, platform_tag)
    if platform_tag.startswith("windows-"):
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(built_binary, built_binary.name)
    else:
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(built_binary, arcname=built_binary.name)


def build_pyinstaller_data_arg(source: Path, target: str) -> str:
    separator = ";" if os.name == "nt" else ":"
    return f"{source}{separator}{target}"


if __name__ == "__main__":
    raise SystemExit(main())

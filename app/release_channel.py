"""GitHub Releases 기반 standalone 배포 채널 유틸리티."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.versioning import APP_VERSION, RELEASE_REPOSITORY


GITHUB_RELEASES_API = "https://api.github.com/repos/{repository}/releases/latest"


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: str
    assets: tuple[ReleaseAsset, ...]


def normalize_version(value: str) -> str:
    return value.removeprefix("v").strip()


def parse_version_tuple(value: str) -> tuple[int, ...]:
    normalized = normalize_version(value)
    parts = normalized.split(".")
    numbers: list[int] = []
    for part in parts:
        digits = "".join(character for character in part if character.isdigit())
        if digits:
            numbers.append(int(digits))
        else:
            numbers.append(0)
    return tuple(numbers)


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    return parse_version_tuple(candidate) > parse_version_tuple(current)


def is_windows_platform() -> bool:
    return os.name == "nt"


def detect_platform_tag() -> str:
    return "windows-x64" if is_windows_platform() else "linux-x64"


def standalone_binary_name(role: str, platform_tag: str | None = None) -> str:
    actual_platform = platform_tag or detect_platform_tag()
    suffix = ".exe" if actual_platform.startswith("windows-") else ""
    return f"issue-to-pr-bot-{role}{suffix}"


def standalone_archive_name(role: str, platform_tag: str | None = None) -> str:
    actual_platform = platform_tag or detect_platform_tag()
    extension = ".zip" if actual_platform.startswith("windows-") else ".tar.gz"
    return f"issue-to-pr-bot-{role}-{actual_platform}{extension}"


def fetch_latest_release_info(repository: str = RELEASE_REPOSITORY, *, timeout: int = 20) -> ReleaseInfo:
    url = GITHUB_RELEASES_API.format(repository=repository)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "issue-to-pr-bot-release-client",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assets = tuple(
        ReleaseAsset(
            name=str(item["name"]),
            download_url=str(item["browser_download_url"]),
            size=int(item["size"]) if item.get("size") is not None else None,
        )
        for item in payload.get("assets", [])
    )
    tag_name = str(payload.get("tag_name") or payload.get("name") or "")
    return ReleaseInfo(
        tag_name=tag_name,
        version=normalize_version(tag_name),
        assets=assets,
    )


def select_release_asset(
    release: ReleaseInfo,
    role: str,
    *,
    platform_tag: str | None = None,
) -> ReleaseAsset:
    expected_name = standalone_archive_name(role, platform_tag)
    for asset in release.assets:
        if asset.name == expected_name:
            return asset
    raise RuntimeError(f"릴리즈에서 standalone asset을 찾지 못했습니다: {expected_name}")


def download_release_asset(asset: ReleaseAsset, destination: Path, *, timeout: int = 60) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        asset.download_url,
        headers={"User-Agent": "issue-to-pr-bot-release-client"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        destination.write_bytes(response.read())
    return destination


def extract_release_asset(archive_path: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(destination_dir)
    elif archive_path.name.endswith(".tar.gz"):
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(destination_dir)
    else:
        raise RuntimeError(f"지원하지 않는 릴리즈 아카이브 형식입니다: {archive_path.name}")
    return destination_dir


def locate_extracted_binary(destination_dir: Path, role: str, *, platform_tag: str | None = None) -> Path:
    expected = standalone_binary_name(role, platform_tag)
    matches = list(destination_dir.rglob(expected))
    if not matches:
        raise RuntimeError(f"압축 해제 결과에서 실행 파일을 찾지 못했습니다: {expected}")
    return matches[0]


def install_standalone_binary(
    role: str,
    install_root: Path,
    *,
    repository: str = RELEASE_REPOSITORY,
    version: str | None = None,
    platform_tag: str | None = None,
    target_name: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[Path, str, str]:
    def emit_progress(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    actual_platform = platform_tag or detect_platform_tag()
    emit_progress("[1/5] 최신 릴리즈 정보를 확인하는 중...")
    release = fetch_latest_release_info(repository)
    target_version = normalize_version(version) if version else release.version
    if version and normalize_version(release.version) != target_version:
        raise RuntimeError(
            f"현재 구현은 latest 릴리즈 설치만 지원합니다. latest={release.version}, requested={target_version}"
        )
    asset = select_release_asset(release, role, platform_tag=actual_platform)
    emit_progress(f"[2/5] 설치 자산을 선택했습니다: {asset.name}")
    install_root.mkdir(parents=True, exist_ok=True)
    archive_path = install_root / asset.name
    temp_extract_dir = install_root / f".extract-{role}-{release.version}"
    if temp_extract_dir.exists():
        shutil.rmtree(temp_extract_dir)
    emit_progress(f"[3/5] 다운로드 중: {asset.name}")
    download_release_asset(asset, archive_path)
    emit_progress("[4/5] 압축을 해제하는 중...")
    extract_release_asset(archive_path, temp_extract_dir)
    extracted_binary = locate_extracted_binary(temp_extract_dir, role, platform_tag=actual_platform)
    binary_name = target_name or standalone_binary_name(role, actual_platform)
    target_binary = install_root / binary_name
    action = "updated" if target_binary.exists() else "created"
    emit_progress(f"[5/5] 실행 파일을 배치하는 중: {binary_name}")
    shutil.copy2(extracted_binary, target_binary)
    if not is_windows_platform():
        current_mode = target_binary.stat().st_mode
        target_binary.chmod(current_mode | 0o111)
    archive_path.unlink(missing_ok=True)
    shutil.rmtree(temp_extract_dir, ignore_errors=True)
    emit_progress(f"설치 완료: {binary_name} ({release.version})")
    return target_binary, action, release.version

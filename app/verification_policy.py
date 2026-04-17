from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import IssueRequest
from app.slot_inference import VERIFICATION_SCOPE_SLOT_LEXICON, score_slot_values


DOC_EXTENSIONS = {".md", ".markdown", ".rst", ".txt", ".adoc"}
FRONTEND_EXTENSIONS = {
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".svg",
    ".ico",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
}
PYTHON_EXTENSIONS = {".py", ".pyi"}
FRONTEND_TOOLING_FILES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "vite.config.js",
    "vite.config.ts",
    "webpack.config.js",
    "webpack.config.ts",
    "tsconfig.json",
}
PYTHON_CORE_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
    "noxfile.py",
}
CONFIG_ONLY_FILES = {
    ".editorconfig",
    ".prettierrc",
    ".prettierrc.json",
    ".eslintrc",
    ".eslintrc.json",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
}
FRONTEND_HINTS = (
    "html",
    "css",
    "javascript",
    "typescript",
    "frontend",
    "front-end",
    "landing",
    "page",
    "ui",
    "화면",
    "페이지",
    "프론트",
    "스타일",
)
DOC_HINTS = (
    "readme",
    "docs",
    "documentation",
    "typo",
    "comment",
    "문서",
    "오타",
    "주석",
)
PYTHON_HINTS = (
    "python",
    "pytest",
    "unittest",
    ".py",
    "venv",
    "pip",
    "app/",
    "tests/",
    "백엔드",
    "backend",
)
CONFIG_HINTS = (
    "config",
    "configuration",
    "yaml",
    "yml",
    "json",
    "toml",
    "ini",
    "docker",
    "compose",
    "설정",
    "인프라",
)
SETUP_COMMAND_PATTERNS = (
    "python -m venv",
    "python3 -m venv",
    "py -m venv",
    "pip install",
    "pip3 install",
    "python -m pip install",
    "python3 -m pip install",
    "npm install",
    "npm ci",
    "pnpm install",
    "yarn install",
    "yarn add",
    "uv sync",
    "uv pip install",
    "poetry install",
    "bundle install",
    "cargo fetch",
    "go mod download",
    "dotnet restore",
)
PYTHON_SANITY_PATTERNS = (
    "python -m compileall",
    "python3 -m compileall",
    "py -m compileall",
    "python -m py_compile",
    "python3 -m py_compile",
    "ruff check",
)
FRONTEND_VERIFICATION_PATTERNS = (
    "htmlhint",
    "stylelint",
    "eslint",
    "prettier --check",
    "npm run lint",
    "npm run test",
    "npm run build",
    "pnpm lint",
    "pnpm test",
    "pnpm build",
    "yarn lint",
    "yarn test",
    "yarn build",
)
CONFIG_VERIFICATION_PATTERNS = (
    "yamllint",
    "actionlint",
    "docker compose config",
    "docker-compose config",
    "npm run validate",
    "pnpm validate",
    "yarn validate",
)


@dataclass(frozen=True)
class VerificationPlan:
    commands: list[str]
    changed_files: list[str]
    profile: str


def build_verification_plan(
    candidate_commands: list[str],
    changed_files: list[str],
    request: IssueRequest | None = None,
) -> VerificationPlan:
    normalized_commands = unique_commands(candidate_commands)
    filtered_commands = [command for command in normalized_commands if not is_setup_command(command)]
    normalized_files = [normalize_path(path) for path in changed_files if path.strip()]
    profile = classify_verification_scope(request, normalized_files)

    if profile == "docs_only":
        selected = []
    elif profile in {"frontend_static", "frontend_app"}:
        selected = frontend_specific_commands(filtered_commands)
    elif profile == "config_only":
        selected = config_specific_commands(filtered_commands)
    else:
        selected = filtered_commands

    return VerificationPlan(commands=selected, changed_files=normalized_files, profile=profile)


def unique_commands(commands: list[str]) -> list[str]:
    unique: list[str] = []
    for command in commands:
        normalized = command.strip()
        if not normalized or normalized in unique:
            continue
        unique.append(normalized)
    return unique


def classify_verification_scope(request: IssueRequest | None, changed_files: list[str]) -> str:
    if not changed_files:
        return infer_scope_from_request(request)

    has_python = any(is_python_core_path(path) for path in changed_files)
    has_frontend_static = any(is_frontend_static_path(path) for path in changed_files)
    has_frontend_tooling = any(is_frontend_tooling_path(path) for path in changed_files)
    has_docs = any(is_docs_path(path) for path in changed_files)
    has_config = any(is_config_only_path(path) for path in changed_files)
    has_other = any(not is_known_verification_path(path) for path in changed_files)

    if has_python:
        return "mixed" if has_docs or has_frontend_static or has_frontend_tooling or has_config or has_other else "python_core"
    if has_frontend_static or has_frontend_tooling:
        if has_docs or has_config or has_other:
            return "mixed"
        return "frontend_app" if has_frontend_tooling else "frontend_static"
    if has_docs and not (has_config or has_other):
        return "docs_only"
    if has_config and not has_other:
        return "config_only"

    inferred = infer_scope_from_request(request)
    if inferred != "unknown":
        return inferred
    return "mixed" if has_other or has_docs or has_frontend_static or has_frontend_tooling or has_config else "unknown"


def infer_scope_from_request(request: IssueRequest | None) -> str:
    if request is None:
        return "unknown"

    text = " ".join(
        [
            request.issue_title,
            request.issue_body,
            request.comment_body,
            request.review_path or "",
            request.review_diff_hunk or "",
        ]
    ).lower()

    scores = score_slot_values(text, VERIFICATION_SCOPE_SLOT_LEXICON)
    has_frontend_hint = scores.get("frontend", 0) >= 2
    has_doc_hint = scores.get("docs", 0) >= 2
    has_python_hint = scores.get("python", 0) >= 2
    has_config_hint = scores.get("config", 0) >= 2

    if has_frontend_hint and not has_python_hint:
        return "frontend_static"
    if has_doc_hint and not has_python_hint and not has_frontend_hint:
        return "docs_only"
    if has_config_hint and not has_python_hint and not has_frontend_hint:
        return "config_only"
    if has_python_hint:
        return "python_core"
    return "unknown"


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def is_known_verification_path(path: str) -> bool:
    return (
        is_docs_path(path)
        or is_frontend_static_path(path)
        or is_frontend_tooling_path(path)
        or is_python_core_path(path)
        or is_config_only_path(path)
    )


def is_docs_path(path: str) -> bool:
    normalized = normalize_path(path)
    suffix = suffix_of(normalized)
    return suffix in DOC_EXTENSIONS


def is_frontend_static_path(path: str) -> bool:
    normalized = normalize_path(path)
    suffix = suffix_of(normalized)
    if suffix not in FRONTEND_EXTENSIONS:
        return False
    if normalized.startswith((".github/", "app/", "tests/")):
        return False
    return True


def is_frontend_tooling_path(path: str) -> bool:
    normalized = normalize_path(path)
    name = normalized.rsplit("/", 1)[-1].lower()
    return name in FRONTEND_TOOLING_FILES


def is_python_core_path(path: str) -> bool:
    normalized = normalize_path(path)
    suffix = suffix_of(normalized)
    name = normalized.rsplit("/", 1)[-1].lower()
    return suffix in PYTHON_EXTENSIONS or normalized.startswith(("app/", "tests/")) or name in PYTHON_CORE_FILES


def is_config_only_path(path: str) -> bool:
    normalized = normalize_path(path)
    name = normalized.rsplit("/", 1)[-1].lower()
    suffix = suffix_of(normalized)
    if name in CONFIG_ONLY_FILES:
        return True
    if normalized.startswith((".vscode/", "config/", "configs/", "infra/", "docker/", ".devcontainer/")):
        return True
    return suffix in {".yaml", ".yml", ".toml", ".ini", ".cfg"} and not normalized.startswith(("app/", "tests/"))


def suffix_of(path: str) -> str:
    dot = path.rfind(".")
    if dot == -1:
        return ""
    return path[dot:].lower()


def is_setup_command(command: str) -> bool:
    lowered = command.strip().lower()
    return any(pattern in lowered for pattern in SETUP_COMMAND_PATTERNS)


def lightweight_sanity_commands(commands: list[str]) -> list[str]:
    return [command for command in commands if is_lightweight_sanity_command(command)]


def frontend_specific_commands(commands: list[str]) -> list[str]:
    return [command for command in commands if is_frontend_verification_command(command)]


def config_specific_commands(commands: list[str]) -> list[str]:
    return [command for command in commands if is_config_verification_command(command)]


def is_lightweight_sanity_command(command: str) -> bool:
    lowered = command.strip().lower()
    return any(pattern in lowered for pattern in PYTHON_SANITY_PATTERNS) or is_frontend_verification_command(command) or is_config_verification_command(command)


def is_frontend_verification_command(command: str) -> bool:
    lowered = command.strip().lower()
    return any(pattern in lowered for pattern in FRONTEND_VERIFICATION_PATTERNS)


def is_config_verification_command(command: str) -> bool:
    lowered = command.strip().lower()
    return any(pattern in lowered for pattern in CONFIG_VERIFICATION_PATTERNS)

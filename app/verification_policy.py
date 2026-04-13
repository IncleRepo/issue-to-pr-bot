from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import IssueRequest


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
PYTHON_CORE_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
    "noxfile.py",
}
FRONTEND_HINTS = (
    "html",
    "css",
    "javascript",
    "frontend",
    "front-end",
    "landing",
    "page",
    "ui",
    "웹",
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
    elif profile == "frontend_static":
        selected = frontend_specific_commands(filtered_commands) or lightweight_sanity_commands(filtered_commands)
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
    has_docs = any(is_docs_path(path) for path in changed_files)
    has_frontend = any(is_frontend_static_path(path) for path in changed_files)
    has_other = any(not is_known_verification_path(path) for path in changed_files)

    if has_python:
        return "mixed" if has_docs or has_frontend or has_other else "python_core"
    if has_frontend and not has_other:
        return "frontend_static"
    if has_docs and not has_other:
        return "docs_only"

    inferred = infer_scope_from_request(request)
    if inferred != "unknown":
        return inferred
    return "mixed" if has_other or has_docs or has_frontend else "unknown"


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

    has_frontend_hint = any(keyword in text for keyword in FRONTEND_HINTS)
    has_doc_hint = any(keyword in text for keyword in DOC_HINTS)
    has_python_hint = any(keyword in text for keyword in PYTHON_HINTS)

    if has_frontend_hint and not has_python_hint:
        return "frontend_static"
    if has_doc_hint and not has_python_hint and not has_frontend_hint:
        return "docs_only"
    if has_python_hint:
        return "python_core"
    return "unknown"


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def is_known_verification_path(path: str) -> bool:
    return is_docs_path(path) or is_frontend_static_path(path) or is_python_core_path(path)


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


def is_python_core_path(path: str) -> bool:
    normalized = normalize_path(path)
    suffix = suffix_of(normalized)
    name = normalized.rsplit("/", 1)[-1].lower()
    return (
        suffix in PYTHON_EXTENSIONS
        or normalized.startswith(("app/", "tests/"))
        or name in PYTHON_CORE_FILES
    )


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


def is_lightweight_sanity_command(command: str) -> bool:
    lowered = command.strip().lower()
    return any(pattern in lowered for pattern in PYTHON_SANITY_PATTERNS) or is_frontend_verification_command(command)


def is_frontend_verification_command(command: str) -> bool:
    lowered = command.strip().lower()
    return any(pattern in lowered for pattern in FRONTEND_VERIFICATION_PATTERNS)

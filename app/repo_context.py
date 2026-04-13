import os
from dataclasses import dataclass
from pathlib import Path

from app.config import BotConfig


DEFAULT_EXTERNAL_CONTEXT_DIR = Path("/run/external-context")
EXTERNAL_CONTEXT_DIR_ENV = "BOT_EXTERNAL_CONTEXT_DIR"
EXTERNAL_CONTEXT_PREFIX = "external:"

MAX_CONTEXT_FILE_BYTES = 12_000
MAX_CONTEXT_TOTAL_BYTES = 40_000
MAX_PROJECT_TREE_ENTRIES = 140
MAX_PROJECT_TREE_DEPTH = 4

IGNORED_PROJECT_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "bot-output",
    "build",
    "dist",
    "node_modules",
}


@dataclass(frozen=True)
class ContextDocument:
    path: str
    content: str
    truncated: bool


class MissingContextError(RuntimeError):
    def __init__(self, missing_paths: list[str]) -> None:
        joined = ", ".join(missing_paths)
        super().__init__(f"필수 context 문서를 찾을 수 없습니다: {joined}")
        self.missing_paths = missing_paths


def collect_context_documents(workspace: Path, config: BotConfig) -> list[ContextDocument]:
    ensure_required_context_paths(workspace, config)

    documents: list[ContextDocument] = []
    total_bytes = 0
    seen_paths: set[str] = set()

    total_bytes = collect_documents_from_root(
        documents,
        seen_paths,
        total_bytes,
        workspace,
        config.context_paths,
        path_prefix="",
    )

    external_root = get_external_context_root()
    if external_root:
        total_bytes = collect_documents_from_root(
            documents,
            seen_paths,
            total_bytes,
            external_root,
            config.external_context_paths,
            path_prefix="external/",
        )

    return documents


def collect_documents_from_root(
    documents: list[ContextDocument],
    seen_paths: set[str],
    total_bytes: int,
    root: Path,
    configured_paths: list[str],
    path_prefix: str,
) -> int:
    for configured_path in configured_paths:
        for path in expand_context_path(root, configured_path):
            relative_path = path.relative_to(root.resolve()).as_posix()
            display_path = f"{path_prefix}{relative_path}"
            if display_path in seen_paths:
                continue

            raw = path.read_bytes()
            remaining = MAX_CONTEXT_TOTAL_BYTES - total_bytes
            if remaining <= 0:
                return total_bytes

            limit = min(MAX_CONTEXT_FILE_BYTES, remaining)
            truncated = len(raw) > limit
            content = raw[:limit].decode("utf-8", errors="replace")
            total_bytes += len(raw[:limit])
            documents.append(ContextDocument(path=display_path, content=content, truncated=truncated))
            seen_paths.add(display_path)

    return total_bytes


def ensure_required_context_paths(workspace: Path, config: BotConfig) -> None:
    missing = [
        configured_path
        for configured_path in config.required_context_paths
        if not required_context_exists(workspace, configured_path)
    ]
    if missing:
        raise MissingContextError(missing)


def required_context_exists(workspace: Path, configured_path: str) -> bool:
    if configured_path.startswith(EXTERNAL_CONTEXT_PREFIX):
        external_root = get_external_context_root()
        if not external_root:
            return False
        target = configured_path[len(EXTERNAL_CONTEXT_PREFIX) :].strip()
        return bool(expand_context_path(external_root, target))

    return bool(expand_context_path(workspace, configured_path))


def get_external_context_root() -> Path | None:
    configured = os.getenv(EXTERNAL_CONTEXT_DIR_ENV)
    path = Path(configured) if configured else DEFAULT_EXTERNAL_CONTEXT_DIR
    if path.exists() and path.is_dir():
        return path
    return None


def expand_context_path(root: Path, configured_path: str) -> list[Path]:
    if not configured_path.strip():
        return []

    root = root.resolve()
    path = (root / configured_path).resolve()

    try:
        path.relative_to(root)
    except ValueError:
        return []

    if not path.exists():
        return []

    if path.is_file():
        return [path]

    if not path.is_dir():
        return []

    return sorted(candidate for candidate in path.rglob("*") if candidate.is_file())[:30]


def format_context_documents(documents: list[ContextDocument]) -> str:
    if not documents:
        return "No repository guidance documents were found."

    sections = ["Repository guidance documents:"]
    for document in documents:
        suffix = " (truncated)" if document.truncated else ""
        sections.extend(
            [
                "",
                f"--- {document.path}{suffix} ---",
                "```text",
                document.content.strip() or "(empty)",
                "```",
            ]
        )
    return "\n".join(sections)


def collect_project_summary(workspace: Path) -> str:
    workspace = workspace.resolve()
    entries: list[str] = []

    for root, dir_names, file_names in os.walk(workspace):
        current_dir = Path(root)
        relative_dir = current_dir.relative_to(workspace)
        depth = 0 if str(relative_dir) == "." else len(relative_dir.parts)

        dir_names[:] = sorted(
            name
            for name in dir_names
            if name not in IGNORED_PROJECT_DIRS and not name.endswith(".egg-info")
        )
        if depth >= MAX_PROJECT_TREE_DEPTH:
            dir_names[:] = []

        for dir_name in dir_names:
            entries.append(format_project_entry(current_dir / dir_name, workspace, is_dir=True))
            if len(entries) >= MAX_PROJECT_TREE_ENTRIES:
                return "\n".join(entries) + "\n... (truncated)"

        for file_name in sorted(file_names):
            path = current_dir / file_name
            if should_skip_project_file(path):
                continue
            entries.append(format_project_entry(path, workspace, is_dir=False))
            if len(entries) >= MAX_PROJECT_TREE_ENTRIES:
                return "\n".join(entries) + "\n... (truncated)"

    return "\n".join(entries)


def format_project_entry(path: Path, workspace: Path, is_dir: bool) -> str:
    relative_path = path.relative_to(workspace).as_posix()
    marker = "/" if is_dir else ""
    return f"- {relative_path}{marker}"


def should_skip_project_file(path: Path) -> bool:
    if path.name.endswith((".pyc", ".pyo", ".log")):
        return True
    return path.name in {".DS_Store", "Thumbs.db"}

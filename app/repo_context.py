import os
from dataclasses import dataclass
from pathlib import Path

from app.config import BotConfig


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


def collect_context_documents(workspace: Path, config: BotConfig) -> list[ContextDocument]:
    documents: list[ContextDocument] = []
    total_bytes = 0

    for configured_path in config.context_paths:
        for path in expand_context_path(workspace, configured_path):
            relative_path = path.relative_to(workspace.resolve()).as_posix()
            if any(document.path == relative_path for document in documents):
                continue

            raw = path.read_bytes()
            remaining = MAX_CONTEXT_TOTAL_BYTES - total_bytes
            if remaining <= 0:
                return documents

            limit = min(MAX_CONTEXT_FILE_BYTES, remaining)
            truncated = len(raw) > limit
            content = raw[:limit].decode("utf-8", errors="replace")
            total_bytes += len(raw[:limit])
            documents.append(ContextDocument(path=relative_path, content=content, truncated=truncated))

    return documents


def expand_context_path(workspace: Path, configured_path: str) -> list[Path]:
    path = (workspace / configured_path).resolve()
    workspace = workspace.resolve()

    try:
        path.relative_to(workspace)
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

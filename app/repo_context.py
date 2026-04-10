from dataclasses import dataclass
from pathlib import Path

from app.config import BotConfig


MAX_CONTEXT_FILE_BYTES = 12_000
MAX_CONTEXT_TOTAL_BYTES = 40_000


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

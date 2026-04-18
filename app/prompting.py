from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable

from app.attachments import AttachmentContext, collect_attachment_context
from app.bot import IssueRequest, build_plan_prompt, build_task_prompt
from app.config import BotConfig
from app.repo_context import ContextDocument, collect_context_documents, collect_project_summary, format_context_documents
from app.runtime_secrets import load_runtime_secrets
from app.verification_policy import CONFIG_HINTS, DOC_HINTS, FRONTEND_HINTS, PYTHON_HINTS


MAX_PROMPT_CHARS = 18_000
MAX_REPOSITORY_CONTEXT_CHARS = 8_000
MAX_PROJECT_SUMMARY_CHARS = 3_200
MAX_CODE_CONTEXT_CHARS = 4_400
MAX_ATTACHMENT_CONTEXT_CHARS = 3_200
TOKEN_PATTERN = re.compile(r"[a-z0-9_/.-]{3,}")
CODE_CONTEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".sql",
}
CODE_CONTEXT_DIR_SKIP = {
    ".git",
    ".issue-to-pr-bot",
    ".mypy_cache",
    ".pytest_cache",
    ".runtime-output",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "bot-output",
    "build",
    "dist",
    "node_modules",
}


@dataclass(frozen=True)
class PromptMetrics:
    action: str
    prompt_chars: int
    document_count: int
    selected_document_count: int
    repository_context_chars: int
    project_summary_chars: int
    code_context_chars: int
    code_context_file_count: int
    attachment_context_chars: int
    attachment_count: int
    skipped_attachment_count: int
    secret_key_count: int
    collection_seconds: float


@dataclass(frozen=True)
class PreparedPrompt:
    prompt: str
    attachment_info: AttachmentContext
    available_secret_keys: list[str]
    repository_context: str
    project_summary: str
    code_context: str
    attachment_context: str
    metrics: PromptMetrics


def prepare_prompt(
    request: IssueRequest,
    workspace,
    config: BotConfig,
    action: str,
) -> PreparedPrompt:
    started_at = perf_counter()
    available_secret_keys = load_runtime_secrets(config)
    attachment_info = collect_attachment_context(request, workspace)
    documents = collect_context_documents(workspace, config)
    repository_context = build_repository_context(documents, request, action)
    project_summary = build_project_summary(collect_project_summary(workspace), request, action)
    code_context = build_code_context(workspace, request, action)
    attachment_context = build_attachment_context(attachment_info, action)

    builder = build_plan_prompt if action == "plan" else build_task_prompt
    prompt, repository_context, project_summary, code_context, attachment_context = fit_prompt_budget(
        builder=builder,
        request=request,
        config=config,
        repository_context=repository_context,
        project_summary=project_summary,
        code_context=code_context,
        available_secret_keys=available_secret_keys,
        attachment_context=attachment_context,
    )

    return PreparedPrompt(
        prompt=prompt,
        attachment_info=attachment_info,
        available_secret_keys=available_secret_keys,
        repository_context=repository_context,
        project_summary=project_summary,
        code_context=code_context,
        attachment_context=attachment_context,
        metrics=PromptMetrics(
            action=action,
            prompt_chars=len(prompt),
            document_count=len(documents),
            selected_document_count=count_rendered_documents(repository_context),
            repository_context_chars=len(repository_context),
            project_summary_chars=len(project_summary),
            code_context_chars=len(code_context),
            code_context_file_count=count_rendered_documents(code_context),
            attachment_context_chars=len(attachment_context),
            attachment_count=len(attachment_info.attachments),
            skipped_attachment_count=len(attachment_info.skipped),
            secret_key_count=len(available_secret_keys),
            collection_seconds=perf_counter() - started_at,
        ),
    )


def build_repository_context(documents: list[ContextDocument], request: IssueRequest, action: str) -> str:
    if not documents:
        return "No repository guidance documents were found."
    selected = select_context_documents(documents, request, action)
    return format_context_documents(selected)


def select_context_documents(documents: list[ContextDocument], request: IssueRequest, action: str) -> list[ContextDocument]:
    max_documents = 4 if request.is_pull_request else 6
    if action == "plan":
        max_documents = max(max_documents, 5)
    if request.review_path:
        max_documents = min(max_documents, 4)
    request_text = collect_request_text(request)
    if has_any_hint(request_text, FRONTEND_HINTS) and not has_any_hint(request_text, PYTHON_HINTS):
        max_documents = min(max_documents, 4)
    if has_any_hint(request_text, DOC_HINTS) and not has_any_hint(request_text, PYTHON_HINTS):
        max_documents = min(max_documents, 4)

    ranked = sorted(documents, key=lambda document: document_priority(document, request), reverse=True)
    selected = [trim_context_document(document, request, action) for document in ranked[:max_documents]]
    return selected


def document_priority(document: ContextDocument, request: IssueRequest) -> tuple[int, str]:
    path = document.path.lower()
    score = 0
    request_text = collect_request_text(request)
    request_tokens = extract_relevant_tokens(request)

    if path.endswith("agents.md"):
        score += 140
    elif path.endswith(".issue-to-pr-bot.yml"):
        score += 130
    elif path.endswith("contributing.md"):
        score += 120
    elif path.endswith("readme.md"):
        score += 95
    elif "pull_request_template" in path:
        score += 90
    elif "issue_template" in path:
        score += 80
    elif path.endswith(".editorconfig"):
        score += 65
    elif path.endswith("pyproject.toml"):
        score += 75 if has_any_hint(request_text, PYTHON_HINTS) else 55
    elif path.endswith("package.json"):
        score += 75 if has_any_hint(request_text, FRONTEND_HINTS) else 55
    elif path.endswith(("dockerfile", "docker-compose.yml", "docker-compose.yaml")):
        score += 70 if has_any_hint(request_text, CONFIG_HINTS) else 40

    if request.is_pull_request and "pull_request_template" in path:
        score += 20
    if request.review_path and any(part in path for part in tokenize_path(request.review_path)):
        score += 40
    if request.pull_request_number and "pull_request_template" in path:
        score += 10
    if has_any_hint(request_text, DOC_HINTS) and path.endswith("readme.md"):
        score += 20

    basename = path.rsplit("/", 1)[-1]
    if basename in request_text:
        score += 20
    if any(token in path for token in request_tokens):
        score += 15

    return score, path


def trim_context_document(document: ContextDocument, request: IssueRequest, action: str) -> ContextDocument:
    if request.review_path:
        limit = 1_000
    elif request.is_pull_request:
        limit = 1_200
    elif action == "plan":
        limit = 1_800
    else:
        limit = 1_600

    content = compact_text(document.content.strip(), limit)
    truncated = document.truncated or len(content) < len(document.content.strip())
    return ContextDocument(path=document.path, content=content, truncated=truncated)


def build_project_summary(summary: str, request: IssueRequest, action: str) -> str:
    if not summary.strip():
        return "No project structure summary was provided."

    lines = [line for line in summary.splitlines() if line.strip()]
    relevant_tokens = extract_relevant_tokens(request)
    max_lines = 24 if request.review_path else (28 if request.is_pull_request else 44)
    if action == "plan":
        max_lines += 8

    selected: list[str] = []
    seen: set[str] = set()

    for line in lines:
        normalized = line.strip()
        if normalized in seen:
            continue
        lowered = normalized.lower()
        if any(token in lowered for token in relevant_tokens):
            selected.append(normalized)
            seen.add(normalized)
        if len(selected) >= max_lines:
            break

    for line in lines:
        normalized = line.strip()
        if normalized in seen:
            continue
        if normalized.count("/") <= 1:
            selected.append(normalized)
            seen.add(normalized)
        if len(selected) >= max_lines:
            break

    for line in lines:
        normalized = line.strip()
        if normalized in seen:
            continue
        selected.append(normalized)
        seen.add(normalized)
        if len(selected) >= max_lines:
            break

    text = "\n".join(selected)
    if len(selected) < len(lines):
        text += "\n... (truncated)"
    return compact_text(text, MAX_PROJECT_SUMMARY_CHARS)


def build_code_context(workspace: Path, request: IssueRequest, action: str) -> str:
    candidates = collect_relevant_code_candidates(workspace, request, action)
    if not candidates:
        return "No issue-relevant code files were selected."

    sections = ["Issue-relevant code context:"]
    rendered_files = 0
    total_chars = len(sections[0])
    for path in candidates:
        rendered = render_code_context_file(workspace, path, request)
        if not rendered.strip():
            continue
        projected = total_chars + len(rendered) + 2
        if rendered_files >= 4 or projected > MAX_CODE_CONTEXT_CHARS:
            break
        sections.extend(["", rendered])
        total_chars = projected
        rendered_files += 1

    if rendered_files == 0:
        return "No issue-relevant code files were selected."
    return "\n".join(sections)


def collect_relevant_code_candidates(workspace: Path, request: IssueRequest, action: str) -> list[Path]:
    request_tokens = extract_relevant_tokens(request)
    review_tokens = tokenize_path(request.review_path) if request.review_path else set()
    request_text = collect_request_text(request)
    candidates: list[tuple[int, str, Path]] = []

    for root, dir_names, file_names in os.walk(workspace):
        current_dir = Path(root)
        dir_names[:] = sorted(
            name for name in dir_names if name not in CODE_CONTEXT_DIR_SKIP and not name.endswith(".egg-info")
        )
        for file_name in sorted(file_names):
            path = current_dir / file_name
            if not should_include_code_context_file(path):
                continue
            score = score_code_context_path(path, workspace, request, request_text, request_tokens, review_tokens)
            if score <= 0:
                continue
            relative = path.relative_to(workspace).as_posix()
            candidates.append((score, relative, path))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    max_candidates = 4 if request.review_path else (5 if action == "plan" else 6)
    return [path for _, _, path in candidates[:max_candidates]]


def should_include_code_context_file(path: Path) -> bool:
    if path.suffix.lower() not in CODE_CONTEXT_EXTENSIONS:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return size <= 64_000


def score_code_context_path(
    path: Path,
    workspace: Path,
    request: IssueRequest,
    request_text: str,
    request_tokens: set[str],
    review_tokens: set[str],
) -> int:
    relative = path.relative_to(workspace).as_posix().lower()
    basename = path.name.lower()
    score = 0

    if request.review_path:
        review_path = request.review_path.lower().replace("\\", "/")
        if relative == review_path:
            score += 200
        elif relative.startswith(str(Path(review_path).parent).replace("\\", "/")):
            score += 70
        if any(token in relative for token in review_tokens):
            score += 25

    token_hits = sum(1 for token in request_tokens if token in relative)
    score += min(token_hits, 6) * 18

    if basename in request_text:
        score += 30
    if path.suffix.lower() in {".html", ".css", ".js", ".ts", ".tsx", ".jsx"} and has_any_hint(request_text, FRONTEND_HINTS):
        score += 16
    if path.suffix.lower() == ".py" and has_any_hint(request_text, PYTHON_HINTS):
        score += 16
    if path.name.lower() in {"package.json", "vite.config.ts", "vite.config.js", "pyproject.toml"}:
        score += 14
    if path.suffix.lower() == ".html" and ("html" in request_text or "ui" in request_text):
        score += 18
    return score


def render_code_context_file(workspace: Path, path: Path, request: IssueRequest) -> str:
    relative = path.relative_to(workspace).as_posix()
    content = path.read_text(encoding="utf-8", errors="replace")
    snippet = extract_relevant_snippet(content, request)
    return "\n".join(
        [
            f"--- {relative} ---",
            "```text",
            compact_text(snippet.strip() or "(empty)", 1_200),
            "```",
        ]
    )


def extract_relevant_snippet(content: str, request: IssueRequest) -> str:
    lines = content.splitlines()
    if not lines:
        return ""

    snippet = extract_review_hunk_snippet(lines, request)
    if snippet:
        return snippet

    lowered_lines = [line.lower() for line in lines]
    tokens = [token for token in sorted(extract_relevant_tokens(request), key=len, reverse=True) if len(token) >= 4]
    match_indexes: list[int] = []
    for index, line in enumerate(lowered_lines):
        if any(token in line for token in tokens[:12]):
            match_indexes.append(index)
        if len(match_indexes) >= 3:
            break

    if match_indexes:
        return render_line_windows(lines, match_indexes, radius=3, max_lines=28)

    return "\n".join(lines[:40])


def extract_review_hunk_snippet(lines: list[str], request: IssueRequest) -> str:
    if request.review_line is None and request.review_start_line is None:
        return ""
    target_line = request.review_line or request.review_start_line
    if target_line is None or target_line <= 0:
        return ""
    zero_based = target_line - 1
    start = max(0, zero_based - 4)
    end = min(len(lines), zero_based + 5)
    return "\n".join(lines[start:end])


def render_line_windows(lines: list[str], indexes: list[int], radius: int, max_lines: int) -> str:
    selected: list[str] = []
    seen: set[int] = set()
    remaining = max_lines
    for index in indexes:
        start = max(0, index - radius)
        end = min(len(lines), index + radius + 1)
        for line_index in range(start, end):
            if line_index in seen:
                continue
            selected.append(lines[line_index])
            seen.add(line_index)
            remaining -= 1
            if remaining <= 0:
                break
        if remaining <= 0:
            break
    return "\n".join(selected)


def extract_relevant_tokens(request: IssueRequest) -> set[str]:
    tokens: set[str] = set()
    raw_values = [
        request.review_path or "",
        request.issue_title,
        request.issue_body,
        request.comment_body,
        request.review_diff_hunk or "",
    ]
    for raw in raw_values:
        lowered = raw.lower().replace("\\", "/")
        for token in TOKEN_PATTERN.findall(lowered):
            cleaned = token.strip("`'\".,:;!?()[]{}")
            if "/" in cleaned:
                tokens.update(part for part in cleaned.split("/") if len(part) >= 3)
            elif len(cleaned) >= 4:
                tokens.add(cleaned)
    return tokens


def collect_request_text(request: IssueRequest) -> str:
    return " ".join(
        [
            request.issue_title,
            request.issue_body,
            request.comment_body,
            request.review_path or "",
            request.review_diff_hunk or "",
        ]
    ).lower()


def tokenize_path(path: str) -> set[str]:
    normalized = path.lower().replace("\\", "/")
    parts: set[str] = set()
    for token in normalized.split("/"):
        if len(token) >= 3:
            parts.add(token)
        for sub_token in token.replace("-", "_").split("_"):
            if len(sub_token) >= 3:
                parts.add(sub_token)
    return parts


def has_any_hint(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def build_attachment_context(context: AttachmentContext, action: str) -> str:
    if not context.attachments and not context.skipped:
        return "No supported issue or comment attachments were collected."

    max_attachments = 2 if action == "plan" else 3
    max_content_chars = 800 if action == "plan" else 1_200

    sections = ["Issue and comment attachments:"]
    for attachment in context.attachments[:max_attachments]:
        sections.extend(
            [
                "",
                f"- {attachment.source}: {attachment.filename} ({attachment.kind})",
                f"  - saved at: {attachment.local_path}",
                f"  - source url: {attachment.url}",
            ]
        )
        if attachment.summary:
            sections.append(f"  - summary: {attachment.summary}")
        if attachment.kind in {"text", "web"} and attachment.content.strip():
            sections.extend(
                [
                    "  - content:",
                    "```text",
                    compact_text(attachment.content.strip(), max_content_chars),
                    "```",
                ]
            )

    if context.skipped:
        sections.extend(["", "Skipped attachment URLs:"])
        for skipped in context.skipped[:3]:
            sections.append(f"- {skipped.url} ({skipped.reason})")

    return compact_text("\n".join(sections), MAX_ATTACHMENT_CONTEXT_CHARS)


def fit_prompt_budget(
    builder: Callable[..., str],
    request: IssueRequest,
    config: BotConfig,
    repository_context: str,
    project_summary: str,
    code_context: str,
    available_secret_keys: list[str],
    attachment_context: str,
) -> tuple[str, str, str, str, str]:
    prompt = builder(
        request,
        config,
        repository_context,
        project_summary,
        code_context,
        available_secret_keys,
        attachment_context,
    )
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt, repository_context, project_summary, code_context, attachment_context

    repository_context = compact_text(repository_context, MAX_REPOSITORY_CONTEXT_CHARS)
    project_summary = compact_text(project_summary, 2_000)
    code_context = compact_text(code_context, 2_400)
    attachment_context = compact_text(attachment_context, 2_000)
    prompt = builder(
        request,
        config,
        repository_context,
        project_summary,
        code_context,
        available_secret_keys,
        attachment_context,
    )
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt, repository_context, project_summary, code_context, attachment_context

    project_summary = compact_text(project_summary, 1_200)
    code_context = compact_text(code_context, 1_600)
    attachment_context = summary_only_attachment_context(attachment_context)
    repository_context = compact_text(repository_context, 5_000)
    prompt = builder(
        request,
        config,
        repository_context,
        project_summary,
        code_context,
        available_secret_keys,
        attachment_context,
    )
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt, repository_context, project_summary, code_context, attachment_context

    repository_context = compact_text(repository_context, 3_000)
    code_context = compact_text(code_context, 1_000)
    prompt = builder(
        request,
        config,
        repository_context,
        project_summary,
        code_context,
        available_secret_keys,
        attachment_context,
    )
    return compact_text(prompt, MAX_PROMPT_CHARS), repository_context, project_summary, code_context, attachment_context


def summary_only_attachment_context(attachment_context: str) -> str:
    if "```text" not in attachment_context:
        return compact_text(attachment_context, 1_200)

    lines: list[str] = []
    in_code_block = False
    for line in attachment_context.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        lines.append(line)
    return compact_text("\n".join(lines).strip(), 1_200)


def compact_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    suffix = "\n... (truncated)"
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def count_rendered_documents(repository_context: str) -> int:
    return repository_context.count("--- ")

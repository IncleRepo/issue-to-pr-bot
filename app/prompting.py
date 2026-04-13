from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable

from app.attachments import AttachmentContext, collect_attachment_context
from app.bot import IssueRequest, build_plan_prompt, build_task_prompt
from app.config import BotConfig
from app.repo_context import ContextDocument, collect_context_documents, collect_project_summary, format_context_documents
from app.runtime_secrets import load_runtime_secrets

MAX_PROMPT_CHARS = 18_000
MAX_REPOSITORY_CONTEXT_CHARS = 8_000
MAX_PROJECT_SUMMARY_CHARS = 3_200
MAX_ATTACHMENT_CONTEXT_CHARS = 3_200


@dataclass(frozen=True)
class PromptMetrics:
    action: str
    prompt_chars: int
    document_count: int
    selected_document_count: int
    repository_context_chars: int
    project_summary_chars: int
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
    attachment_info = collect_attachment_context(request)
    documents = collect_context_documents(workspace, config)
    repository_context = build_repository_context(documents, request, action)
    project_summary = build_project_summary(collect_project_summary(workspace), request, action)
    attachment_context = build_attachment_context(attachment_info, action)

    builder = build_plan_prompt if action == "plan" else build_task_prompt
    prompt, repository_context, project_summary, attachment_context = fit_prompt_budget(
        builder=builder,
        request=request,
        config=config,
        repository_context=repository_context,
        project_summary=project_summary,
        available_secret_keys=available_secret_keys,
        attachment_context=attachment_context,
    )

    return PreparedPrompt(
        prompt=prompt,
        attachment_info=attachment_info,
        available_secret_keys=available_secret_keys,
        repository_context=repository_context,
        project_summary=project_summary,
        attachment_context=attachment_context,
        metrics=PromptMetrics(
            action=action,
            prompt_chars=len(prompt),
            document_count=len(documents),
            selected_document_count=count_rendered_documents(repository_context),
            repository_context_chars=len(repository_context),
            project_summary_chars=len(project_summary),
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

    ranked = sorted(documents, key=lambda document: document_priority(document, request), reverse=True)
    selected = [trim_context_document(document, request, action) for document in ranked[:max_documents]]
    return selected


def document_priority(document: ContextDocument, request: IssueRequest) -> tuple[int, str]:
    path = document.path.lower()
    score = 0

    if path.endswith("agents.md"):
        score += 120
    elif path.endswith(".issue-to-pr-bot.yml"):
        score += 110
    elif path.endswith("contributing.md"):
        score += 100
    elif path.endswith("readme.md"):
        score += 90
    elif "pull_request_template" in path:
        score += 80
    elif "issue_template" in path:
        score += 70
    elif path.endswith(".editorconfig"):
        score += 60
    elif path.endswith("pyproject.toml") or path.endswith("package.json"):
        score += 55

    if request.is_pull_request and "pull_request_template" in path:
        score += 15

    request_text = f"{request.issue_title} {request.issue_body} {request.comment_body}".lower()
    basename = path.rsplit("/", 1)[-1]
    if basename in request_text:
        score += 20

    return score, path


def trim_context_document(document: ContextDocument, request: IssueRequest, action: str) -> ContextDocument:
    if request.is_pull_request:
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
    max_lines = 28 if request.is_pull_request else 44
    if action == "plan":
        max_lines += 8

    selected: list[str] = []
    seen: set[str] = set()

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
        selected.append(normalized)
        seen.add(normalized)
        if len(selected) >= max_lines:
            break

    text = "\n".join(selected)
    if len(selected) < len(lines):
        text += "\n... (truncated)"
    return compact_text(text, MAX_PROJECT_SUMMARY_CHARS)


def extract_relevant_tokens(request: IssueRequest) -> set[str]:
    tokens: set[str] = set()
    for raw in (request.review_path or "", request.issue_title, request.comment_body):
        lowered = raw.lower()
        for token in lowered.replace("\\", "/").split():
            cleaned = token.strip("`'\".,:;!?()[]{}")
            if "/" in cleaned:
                tokens.update(part for part in cleaned.split("/") if len(part) >= 3)
            elif len(cleaned) >= 4:
                tokens.add(cleaned)
    return tokens


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
    available_secret_keys: list[str],
    attachment_context: str,
) -> tuple[str, str, str, str]:
    prompt = builder(
        request,
        config,
        repository_context,
        project_summary,
        available_secret_keys,
        attachment_context,
    )
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt, repository_context, project_summary, attachment_context

    repository_context = compact_text(repository_context, MAX_REPOSITORY_CONTEXT_CHARS)
    project_summary = compact_text(project_summary, 2_000)
    attachment_context = compact_text(attachment_context, 2_000)
    prompt = builder(
        request,
        config,
        repository_context,
        project_summary,
        available_secret_keys,
        attachment_context,
    )
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt, repository_context, project_summary, attachment_context

    project_summary = compact_text(project_summary, 1_200)
    attachment_context = summary_only_attachment_context(attachment_context)
    repository_context = compact_text(repository_context, 5_000)
    prompt = builder(
        request,
        config,
        repository_context,
        project_summary,
        available_secret_keys,
        attachment_context,
    )
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt, repository_context, project_summary, attachment_context

    repository_context = compact_text(repository_context, 3_000)
    prompt = builder(
        request,
        config,
        repository_context,
        project_summary,
        available_secret_keys,
        attachment_context,
    )
    return compact_text(prompt, MAX_PROMPT_CHARS), repository_context, project_summary, attachment_context


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

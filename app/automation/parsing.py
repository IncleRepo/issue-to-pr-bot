"""Natural-language mention parsing and runtime option resolution."""

import re

from app.config import BOT_MENTION, BotConfig
from app.domain.models import BotCommand, BotRuntimeOptions, IssueRequest


def should_run_bot(comment_body: str, config: BotConfig | None = None) -> bool:
    config = config or BotConfig()
    return parse_bot_command(comment_body, config) is not None


def parse_bot_command(comment_body: str, config: BotConfig | None = None) -> BotCommand | None:
    config = config or BotConfig()
    mention_match = find_mention(comment_body, config)
    if not mention_match:
        return None

    instruction = comment_body[mention_match.end() :].strip()
    action = infer_comment_action(instruction)
    return BotCommand(
        action=action,
        trigger=BOT_MENTION,
        instruction=instruction,
        options=infer_runtime_hints(instruction, action),
    )


def should_run_for_mention(comment_body: str, config: BotConfig | None = None) -> bool:
    config = config or BotConfig()
    return find_mention(comment_body, config) is not None


def find_mention(comment_body: str, config: BotConfig) -> re.Match[str] | None:
    del config
    return re.search(rf"(^|\s){re.escape(BOT_MENTION)}(\s|$|[,.!?])", comment_body, re.IGNORECASE)


def infer_comment_action(instruction: str) -> str:
    lowered = instruction.lower()
    implementation_request = has_implementation_intent(lowered)

    if not implementation_request and contains_any(
        lowered,
        (
            "help",
            "usage",
            "how do i use",
            "사용법",
            "헬프",
            "어떻게",
        ),
    ):
        return "help"

    if not implementation_request and contains_any(
        lowered,
        (
            "status",
            "state",
            "health check",
            "상태",
            "점검",
            "준비됐",
            "설정값",
            "괜찮",
        ),
    ):
        return "status"

    if has_plan_only_intent(lowered) or (
        not implementation_request
        and contains_any(
            lowered,
            (
                "plan",
                "planning",
                "implementation plan",
                "계획",
                "설계",
                "계획만",
                "순서만",
            ),
        )
    ):
        return "plan"

    if not implementation_request and infer_merge_action(lowered):
        return "merge"

    return "run"


def infer_runtime_hints(instruction: str, action: str) -> dict[str, str]:
    lowered = instruction.lower()
    options: dict[str, str] = {}

    if action == "plan":
        options["mode"] = "codex"
        options["verify"] = "false"
        provider = infer_provider(lowered)
        if provider:
            options["provider"] = provider
        effort = infer_effort(lowered)
        if effort:
            options["effort"] = effort
        return options

    mode = infer_mode(lowered)
    if mode:
        options["mode"] = mode

    provider = infer_provider(lowered)
    if provider:
        options["provider"] = provider

    effort = infer_effort(lowered)
    if effort:
        options["effort"] = effort

    verify = infer_verify(lowered)
    if verify is not None:
        options["verify"] = "true" if verify else "false"

    if infer_sync_base(lowered):
        options["sync_base"] = "true"

    if infer_merge_request(lowered):
        options["request_merge"] = "true"

    return options


def infer_mode(lowered: str) -> str | None:
    if contains_any(
        lowered,
        (
            "test-pr",
            "test pr",
            "marker pr",
            "브랜치와 pr만",
            "pr만",
            "코드 수정 없이",
            "마커만",
        ),
    ):
        return "test-pr"

    if contains_any(
        lowered,
        (
            "codex",
            "코덱스",
            "실제 구현",
            "코드까지",
            "수정해서",
        ),
    ):
        return "codex"

    return None


def infer_provider(lowered: str) -> str | None:
    if "claude" in lowered or "클로드" in lowered:
        return "claude"
    if "codex" in lowered or "코덱스" in lowered:
        return "codex"
    return None


def infer_effort(lowered: str) -> str | None:
    effort_patterns = (
        ("xhigh", (r"\bxhigh\b", r"extra[\s-]*high", r"very[\s-]*high", r"최대로 깊게", r"아주 깊게")),
        ("high", (r"\bhigh\b", r"high로", r"깊게", r"강하게")),
        ("medium", (r"\bmedium\b", r"medium으로", r"중간", r"보통", r"적당히")),
        ("low", (r"\blow\b", r"low로", r"가볍게", r"빠르게")),
    )
    for effort, patterns in effort_patterns:
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns):
            return effort
    return None


def infer_verify(lowered: str) -> bool | None:
    if contains_any(
        lowered,
        (
            "검증 없이",
            "검증은 생략",
            "테스트 없이",
            "테스트는 생략",
            "verify 없이",
            "skip verification",
            "without verification",
            "no verification",
        ),
    ):
        return False

    if contains_any(
        lowered,
        (
            "검증까지",
            "검증도",
            "테스트까지",
            "테스트도",
            "검증해줘",
            "테스트도 돌려줘",
            "verify too",
        ),
    ):
        return True

    return None


def infer_sync_base(lowered: str) -> bool:
    return contains_any(
        lowered,
        (
            "merge conflict",
            "conflict",
            "rebase",
            "sync with main",
            "sync with base",
            "merge main",
            "충돌",
            "충돌 해결",
            "메인 반영",
            "main 반영",
            "base 반영",
            "최신 main",
            "최신 반영",
        ),
    )


def infer_merge_request(lowered: str) -> bool:
    return contains_any(
        lowered,
        (
            "merge",
            "auto merge",
            "merge when ready",
            "merge if approved",
            "머지",
            "자동 머지",
            "승인되면 머지",
            "준비되면 머지",
        ),
    )


def infer_merge_action(lowered: str) -> bool:
    return infer_merge_request(lowered) and not has_implementation_intent(lowered)


def has_plan_only_intent(lowered: str) -> bool:
    return contains_any(
        lowered,
        (
            "plan only",
            "just plan",
            "계획만",
            "설계만",
            "정리만",
        ),
    )


def has_implementation_intent(lowered: str) -> bool:
    return contains_any(
        lowered,
        (
            "fix",
            "implement",
            "update",
            "change",
            "add",
            "resolve",
            "reflect",
            "edit",
            "modify",
            "write code",
            "수정",
            "구현",
            "추가",
            "반영",
            "해결",
            "작성",
            "고쳐",
            "만들",
            "충돌",
            "conflict",
            "rebase",
            "sync with main",
        ),
    )


def contains_any(text: str, candidates: tuple[str, ...]) -> bool:
    return any(candidate in text for candidate in candidates)


def resolve_runtime_options(command: BotCommand, config: BotConfig) -> BotRuntimeOptions:
    configured_mode = "codex" if command.action == "plan" else config.mode.strip().lower()
    mode = command.options.get("mode", configured_mode).strip().lower()
    if mode not in {"codex", "test-pr"}:
        raise ValueError(f"Unsupported mode value: {mode}")
    if command.action == "plan" and mode != "codex":
        raise ValueError("Plan requests are only supported in codex mode.")

    default_provider = config.provider.strip().lower() if mode == "codex" else "builtin"
    provider = command.options.get("provider", default_provider).strip().lower()
    if mode != "codex" and "provider" in command.options:
        raise ValueError("Provider selection is only supported in codex mode.")
    if mode == "codex":
        from app.llm_provider import ensure_supported_provider

        ensure_supported_provider(provider)
    elif provider != "builtin":
        raise ValueError(f"Unsupported provider value: {provider}")

    verify = parse_bool_option(command.options.get("verify"), default=True)
    if command.action == "plan":
        verify = False

    sync_base = parse_bool_option(command.options.get("sync_base"), default=False)
    if command.action == "plan":
        sync_base = False

    request_merge = parse_bool_option(command.options.get("request_merge"), default=command.action == "merge")
    if command.action == "plan":
        request_merge = False

    effort = command.options.get("effort")
    if not effort and mode == "codex" and command.action in {"run", "plan"}:
        effort = infer_default_effort(command.instruction, command.action)
    if effort and mode != "codex":
        raise ValueError("Effort selection is only supported in codex mode.")

    return BotRuntimeOptions(
        mode=mode,
        provider=provider,
        verify=verify,
        effort=effort.lower() if effort else None,
        sync_base=sync_base,
        request_merge=request_merge,
    )


def infer_default_effort(instruction: str, action: str) -> str:
    lowered = instruction.lower()
    if action == "plan":
        return "low"

    if contains_any(
        lowered,
        (
            "conflict",
            "merge conflict",
            "rebase",
            "sync with main",
            "sync with base",
            "migration",
            "schema",
            "refactor",
            "across files",
            "전역",
            "전체",
            "리팩토링",
            "충돌",
            "메인 반영",
            "스키마",
            "마이그레이션",
        ),
    ):
        return "high"

    if contains_any(
        lowered,
        (
            "readme",
            "docs",
            "documentation",
            "comment",
            "typo",
            "template",
            "문서",
            "오타",
            "주석",
            "템플릿",
            "문구",
        ),
    ):
        return "low"

    return "medium"


def parse_bool_option(raw_value: str | None, default: bool) -> bool:
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean option value: {raw_value}")


def build_issue_request(payload: dict) -> IssueRequest:
    issue = payload.get("issue", {})
    pull_request = payload.get("pull_request", {})
    review = payload.get("review", {})
    repo = payload.get("repository", {})
    comment = payload.get("comment") or review or {}
    is_pull_request = bool(issue.get("pull_request")) or bool(pull_request)
    issue_number = int(issue.get("number") or pull_request.get("number") or 0)
    issue_title = issue.get("title") or pull_request.get("title") or ""
    issue_body = issue.get("body") or pull_request.get("body") or ""

    return IssueRequest(
        repository=repo.get("full_name") or "unknown/unknown",
        issue_number=issue_number,
        issue_title=issue_title,
        issue_body=issue_body,
        comment_body=comment.get("body") or "",
        comment_author=(comment.get("user") or {}).get("login") or "unknown",
        comment_id=int(comment.get("id") or 0),
        is_pull_request=is_pull_request,
        pull_request_number=issue_number if is_pull_request else None,
        base_branch=(pull_request.get("base") or {}).get("ref"),
        head_branch=(pull_request.get("head") or {}).get("ref"),
        pull_request_url=pull_request.get("html_url"),
        review_path=comment.get("path"),
        review_line=comment.get("line"),
        review_start_line=comment.get("start_line"),
        review_side=comment.get("side"),
        review_diff_hunk=comment.get("diff_hunk"),
        review_comment_url=comment.get("html_url"),
    )

"""자연어 멘션을 파싱하고 런타임 옵션을 해석하는 도우미."""

from __future__ import annotations

import re

from app.config import BOT_MENTION, BotConfig
from app.domain.models import BotCommand, BotRuntimeOptions, IssueRequest
from app.slot_inference import (
    ACTION_SLOT_LEXICON,
    DEFAULT_EFFORT_HINTS,
    EFFORT_SLOT_LEXICON,
    IMPLEMENTATION_INTENT_TERMS,
    MODE_SLOT_LEXICON,
    PROVIDER_SLOT_LEXICON,
    SYNC_SLOT_LEXICON,
    VERIFY_SLOT_LEXICON,
    contains_any_term,
    pick_best_slot,
)


PLAN_ONLY_TERMS = (
    "plan only",
    "just plan",
    "계획만",
    "설계만",
    "정리만",
    "코드 말고 계획만",
)


def should_run_bot(comment_body: str, config: BotConfig | None = None) -> bool:
    """댓글 본문에 유효한 봇 멘션이 있으면 참을 반환한다."""

    config = config or BotConfig()
    return parse_bot_command(comment_body, config) is not None


def parse_bot_command(comment_body: str, config: BotConfig | None = None) -> BotCommand | None:
    """멘션 명령을 래퍼가 사용할 명령 구조로 파싱한다."""

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
    """설정된 봇 멘션이 댓글에 있으면 참을 반환한다."""

    config = config or BotConfig()
    return find_mention(comment_body, config) is not None


def find_mention(comment_body: str, config: BotConfig) -> re.Match[str] | None:
    """공개 멘션 문자열을 기준으로 봇 멘션 위치를 찾는다."""

    del config
    return re.search(rf"(^|\s){re.escape(BOT_MENTION)}(\s|$|[,.!?])", comment_body, re.IGNORECASE)


def infer_comment_action(instruction: str) -> str:
    """자유 형식 멘션 문장에서 상위 액션을 추론한다."""

    if not instruction.strip():
        return "help"

    lowered = instruction.lower()
    implementation_request = has_implementation_intent(lowered)
    action_decision = pick_best_slot(lowered, ACTION_SLOT_LEXICON)

    if has_plan_only_intent(lowered) or (not implementation_request and action_decision.value == "plan"):
        return "plan"
    if not implementation_request and action_decision.value in {"help", "status"}:
        return action_decision.value
    if not implementation_request and infer_merge_action(lowered):
        return "merge"
    return "run"


def infer_runtime_hints(instruction: str, action: str) -> dict[str, str]:
    """provider, effort, 검증 여부 같은 런타임 힌트를 추출한다."""

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
    """자연어 힌트에서 Codex 실행 모드를 추론한다."""

    return pick_best_slot(lowered, MODE_SLOT_LEXICON).value


def infer_provider(lowered: str) -> str | None:
    """자유 형식 문장에서 요청된 LLM provider를 추론한다."""

    return pick_best_slot(lowered, PROVIDER_SLOT_LEXICON).value


def infer_effort(lowered: str) -> str | None:
    """공용 슬롯 사전을 이용해 reasoning effort를 추론한다."""

    return pick_best_slot(lowered, EFFORT_SLOT_LEXICON).value


def infer_verify(lowered: str) -> bool | None:
    """검증 실행 여부를 추론한다."""

    decision = pick_best_slot(lowered, VERIFY_SLOT_LEXICON)
    if decision.value == "off":
        return False
    if decision.value == "on":
        return True
    return None


def infer_sync_base(lowered: str) -> bool:
    """rebase나 main 반영 같은 base sync 의도를 감지한다."""

    return pick_best_slot(lowered, SYNC_SLOT_LEXICON).value == "sync_base"


def infer_merge_request(lowered: str) -> bool:
    """정확한 문구 일치 없이도 머지 요청 의도를 감지한다."""

    return pick_best_slot(lowered, {"merge": ACTION_SLOT_LEXICON["merge"]}).value == "merge"


def infer_merge_action(lowered: str) -> bool:
    """구현 의도가 없을 때만 머지 표현을 전용 액션으로 본다."""

    return infer_merge_request(lowered) and not has_implementation_intent(lowered)


def has_plan_only_intent(lowered: str) -> bool:
    """구현 없이 계획만 원하는 요청인지 감지한다."""

    return contains_any_term(lowered, PLAN_ONLY_TERMS)


def has_implementation_intent(lowered: str) -> bool:
    """merge/help/status보다 구현 의도를 우선해야 하는지 감지한다."""

    return contains_any_term(lowered, IMPLEMENTATION_INTENT_TERMS)


def resolve_runtime_options(command: BotCommand, config: BotConfig) -> BotRuntimeOptions:
    """설정 기본값과 텍스트 힌트를 합쳐 최종 런타임 옵션을 만든다."""

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
    """사용자가 effort를 명시하지 않았을 때 기본값을 추론한다."""

    lowered = instruction.lower()
    if action == "plan":
        return "low"
    if contains_any_term(lowered, DEFAULT_EFFORT_HINTS["high"]):
        return "high"
    if contains_any_term(lowered, DEFAULT_EFFORT_HINTS["low"]):
        return "low"
    return "medium"


def parse_bool_option(raw_value: str | None, default: bool) -> bool:
    """멘션에서 추출한 문자열 불리언 값을 해석한다."""

    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean option value: {raw_value}")


def build_issue_request(payload: dict) -> IssueRequest:
    """GitHub webhook payload에서 이슈 요청 객체를 만든다."""

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

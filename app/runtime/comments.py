"""GitHub 댓글과 사용자-facing 실행 결과 포맷을 담당한다."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.attachments import AttachmentContext
from app.codex_provider import LATEST_CODEX_MODEL
from app.config import BOT_MENTION, BotConfig, get_check_commands
from app.domain.models import BotCommand, BotRuntimeOptions, IssueRequest
from app.github_pr import MergeRequestResult, PullRequestResult, create_issue_comment
from app.repo_context import MissingContextError
from app.runtime_secrets import MissingSecretError
from app.verification import VerificationError


@dataclass(frozen=True)
class BotStatusSnapshot:
    available_secret_keys: list[str]
    missing_secret_keys: list[str]
    context_document_count: int
    missing_context_paths: list[str]
    external_context_root: str | None
    secrets_file_path: str
    secrets_file_exists: bool


def format_provider_display(provider: str) -> str:
    if provider == "codex":
        return f"codex({LATEST_CODEX_MODEL})"
    return provider


def post_help_comment(request: IssueRequest, config: BotConfig) -> None:
    del config
    body = "\n".join(
        [
            "## 사용법",
            "",
            f"- `{BOT_MENTION}` 뒤에 자연어로 요청을 적으면 됩니다.",
            "- `계획`, `설계만`, `plan` -> 계획만 작성",
            "- `상태`, `status`, `state` -> 현재 설정과 누락 항목 확인",
            "- `도움말`, `사용법`, `help`, `usage` -> 사용법 보기",
            "- `머지`, `승인되면 머지`, `merge` -> PR 머지 요청 등록",
            "",
            "### 예시",
            f"- `{BOT_MENTION} README 로컬 실행 방법 추가해줘`",
            f"- `{BOT_MENTION} 이 리뷰 반영해줘. main 반영하고 충돌 해결해줘`",
            f"- `{BOT_MENTION} 승인되면 머지해줘`",
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_status_comment(request: IssueRequest, config: BotConfig, snapshot: BotStatusSnapshot) -> None:
    body = "\n".join(
        [
            "## 봇 상태",
            "",
            "### 기본값",
            f"- 멘션: `{BOT_MENTION}`",
            f"- 모드: `{config.mode}`",
            f"- provider: `{format_provider_display(config.provider)}`",
            f"- 브랜치 prefix: `{config.branch_prefix}`",
            "",
            "### 검증 명령",
            format_markdown_list(get_check_commands(config), code=True),
            "",
            "### 컨텍스트",
            f"- 저장소 컨텍스트 경로 수: `{len(config.context_paths)}`",
            f"- 외부 컨텍스트 경로 수: `{len(config.external_context_paths)}`",
            f"- 실제 로드된 문서 수: `{snapshot.context_document_count}`",
            f"- 외부 컨텍스트 루트: `{snapshot.external_context_root or '마운트되지 않음'}`",
            "",
            "### 시크릿",
            f"- 시크릿 env 파일: `{snapshot.secrets_file_path}`",
            f"- 시크릿 env 파일 존재 여부: `{'예' if snapshot.secrets_file_exists else '아니오'}`",
            f"- 사용 가능한 시크릿 키: {format_secret_keys_for_log(snapshot.available_secret_keys)}",
            "",
            "### 누락 항목",
            format_missing_status(snapshot),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_merge_request_comment(
    request: IssueRequest,
    command: BotCommand,
    runtime_options: BotRuntimeOptions,
    merge_result: MergeRequestResult,
) -> None:
    status = "merged" if merge_result.merged else "pending"
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            f"- 상태: `{status}`",
            f"- 액션: `{command.action}`",
            f"- 모드: `{runtime_options.mode}`",
            f"- provider: `{format_provider_display(runtime_options.provider)}`",
            "- 머지 요청: `예`",
            f"- PR: {merge_result.pull_request_url}",
            (
                f"- 머지 커밋: `{merge_result.merge_sha}`"
                if merge_result.merge_sha
                else "- 머지 상태: GitHub 보호 규칙 조건을 기다리는 중"
            ),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_plan_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    runtime_options: BotRuntimeOptions,
    attachment_info: AttachmentContext,
    plan_output: str,
) -> None:
    del config
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            "- 상태: `planned`",
            f"- 액션: `{command.action}`",
            f"- 모드: `{runtime_options.mode}`",
            f"- provider: `{format_provider_display(runtime_options.provider)}`",
            f"- 검증: `{'실행' if runtime_options.verify else '생략'}`",
            f"- 첨부 처리: {format_attachment_summary(attachment_info)}",
            "",
            "### 계획",
            trim_codex_output(plan_output),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_success_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    runtime_options: BotRuntimeOptions,
    attachment_info: AttachmentContext,
    result: PullRequestResult,
    merge_result: MergeRequestResult | None = None,
) -> None:
    verification_summary, verification_detail = format_verification_sections(runtime_options, result.verification_commands)
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            "- 상태: `success`",
            f"- 요청: `{command.action}` / `{format_provider_display(runtime_options.provider)}` / effort=`{runtime_options.effort or 'default'}`",
            f"- PR: {result.pull_request_url}",
            f"- 브랜치: `{result.branch_name}`",
            f"- 변경 파일: `{len(result.changed_files)}`개",
            f"- 첨부 처리: {format_attachment_summary(attachment_info)}",
            f"- 머지 요청: `{format_merge_request_status(merge_result)}`",
            "",
            "### 검증",
            f"- 상태: {verification_summary}",
            verification_detail,
            "",
            "### 변경 파일",
            format_changed_files(result.changed_files),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_no_changes_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    runtime_options: BotRuntimeOptions,
    attachment_info: AttachmentContext,
    result: PullRequestResult,
) -> None:
    del config
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            "- 상태: `no_changes`",
            f"- 요청: `{command.action}` / `{format_provider_display(runtime_options.provider)}`",
            f"- 브랜치: `{result.branch_name}`",
            f"- 첨부 처리: {format_attachment_summary(attachment_info)}",
            "- 사유: 스테이징된 변경사항이 없어 PR을 만들지 않았습니다.",
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_failure_comment(
    request: IssueRequest,
    config: BotConfig,
    error: Exception,
    command: BotCommand | None = None,
) -> None:
    summary_lines = [
        "- 상태: `failed`",
        f"- 실패 단계: `{classify_failure_stage(error)}`",
        f"- 액션: `{command.action if command else 'unknown'}`",
    ]
    if command:
        summary_lines.append(f"- 요청 멘션: `{BOT_MENTION}`")
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            *summary_lines,
            "",
            "### 상세",
            f"- 오류: `{type(error).__name__}`",
            "",
            format_failure_detail(error),
            "",
            "### 다음 단계",
            format_failure_next_steps(request, config, command, error),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_interrupted_comment(
    request: IssueRequest,
    command: BotCommand | None = None,
) -> None:
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            "- 상태: `interrupted`",
            f"- 액션: `{command.action if command else 'unknown'}`",
            "- 사유: 사용자 중단 요청으로 현재 작업을 멈췄습니다.",
            "",
            "### 다음 단계",
            f"- 같은 이슈/PR에 `{BOT_MENTION}`으로 다시 요청하면 새 작업으로 다시 시작할 수 있습니다.",
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def format_changed_files(changed_files: list[str]) -> str:
    if not changed_files:
        return "- 없음"

    displayed = changed_files[:20]
    lines = [f"- `{path}`" for path in displayed]
    if len(changed_files) > len(displayed):
        lines.append(f"- 추가 `{len(changed_files) - len(displayed)}`개 더 있음")
    return "\n".join(lines)


def format_runtime_options(runtime_options: BotRuntimeOptions) -> str:
    parts = [
        f"mode=`{runtime_options.mode}`",
        f"provider=`{format_provider_display(runtime_options.provider)}`",
        f"verify=`{'on' if runtime_options.verify else 'off'}`",
    ]
    if runtime_options.effort:
        parts.append(f"effort=`{runtime_options.effort}`")
    if runtime_options.sync_base:
        parts.append("sync_base=`on`")
    if runtime_options.request_merge:
        parts.append("request_merge=`on`")
    return ", ".join(parts)


def format_verification_status(
    config: BotConfig,
    runtime_options: BotRuntimeOptions,
    commands: list[str] | None = None,
) -> str:
    if not runtime_options.verify:
        return "`검증 생략`"
    if commands is not None:
        if not commands:
            return "`이 변경 범위에서는 추가 검증 없음`"
        return ", ".join(f"`{command}`" for command in commands)
    return format_check_commands(config)


def format_verification_sections(
    runtime_options: BotRuntimeOptions,
    commands: list[str] | None = None,
) -> tuple[str, str]:
    if not runtime_options.verify:
        return "`생략`", "- 요청에서 검증을 끈 상태입니다."
    if not commands:
        return "`실행할 검증 없음`", "- 이번 변경 범위에 맞는 검증 명령이 없어 실행하지 않았습니다."
    return "`실행 완료`", "\n".join(f"- `{command}`" for command in commands)


def format_merge_request_status(merge_result: MergeRequestResult | None) -> str:
    if merge_result is None:
        return "요청 안 함"
    if merge_result.merged:
        return f"머지 완료 ({merge_result.merge_sha})"
    return "요청됨"


def format_attachment_summary(context: AttachmentContext) -> str:
    return f"`{len(context.attachments)}`개 로드, `{len(context.skipped)}`개 스킵"


def format_secret_keys_for_log(secret_keys: list[str]) -> str:
    if not secret_keys:
        return "`없음`"
    return ", ".join(f"`{key}`" for key in secret_keys)


def format_markdown_list(items: list[str], code: bool = False) -> str:
    if not items:
        return "- 없음"
    if code:
        return "\n".join(f"- `{item}`" for item in items)
    return "\n".join(f"- {item}" for item in items)


def format_missing_status(snapshot: BotStatusSnapshot) -> str:
    lines: list[str] = []
    if snapshot.missing_context_paths:
        lines.append("- 누락된 컨텍스트")
        lines.extend(f"  - `{path}`" for path in snapshot.missing_context_paths)
    if snapshot.missing_secret_keys:
        lines.append("- 누락된 시크릿 env")
        lines.extend(f"  - `{key}`" for key in snapshot.missing_secret_keys)
    if not lines:
        return "- 없음"
    return "\n".join(lines)


def format_failure_detail(error: Exception) -> str:
    if isinstance(error, VerificationError):
        output = truncate_text(error.output.strip(), 1800)
        return "\n".join(
            [
                f"- 검증 명령: `{error.command}`",
                "",
                "",
                "```text",
                output or "(출력 없음)",
                "```",
            ]
        )

    if isinstance(error, MissingContextError):
        return "\n".join(["- 누락된 컨텍스트", "", "\n".join(f"  - `{path}`" for path in error.missing_paths)])

    if isinstance(error, MissingSecretError):
        return "\n".join(["- 누락된 시크릿 env", "", "\n".join(f"  - `{key}`" for key in error.missing_keys)])

    if isinstance(error, ValueError):
        return f"- {error}"

    return "\n".join(["```text", truncate_text(str(error), 1800), "```"])


def classify_failure_stage(error: Exception) -> str:
    if isinstance(error, VerificationError):
        return "verification"
    if isinstance(error, MissingContextError):
        return "context"
    if isinstance(error, MissingSecretError):
        return "secret"
    if isinstance(error, ValueError):
        return "options"

    message = str(error).lower()
    if "github api" in message or "pull request" in message:
        return "github"
    if "git " in message or "push" in message or "branch" in message:
        return "git"
    if "codex" in message:
        return "codex"
    return "runtime"


def format_failure_next_steps(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand | None,
    error: Exception,
) -> str:
    del config
    retry_text = request.comment_body.strip() or f"{BOT_MENTION} 다시 시도해줘"
    lines = [
        f"- 같은 멘션으로 다시 요청: `{retry_text}`",
        f"- 상태 확인: `{BOT_MENTION} status`",
    ]

    if isinstance(error, MissingContextError):
        lines.append("- 누락된 문서를 저장소 또는 외부 컨텍스트 경로에 추가하세요.")
    elif isinstance(error, MissingSecretError):
        lines.append("- 누락된 시크릿 env 키를 시크릿 파일 또는 CI 환경에 추가하세요.")
    elif isinstance(error, VerificationError):
        lines.append("- 검증 실패 출력을 먼저 수정한 뒤 다시 요청하세요.")
    elif isinstance(error, ValueError):
        lines.append(f"- `{BOT_MENTION} help`로 사용법을 확인한 뒤 더 명확하게 다시 요청하세요.")
    else:
        lines.append("- 로컬 agent 창 출력을 확인한 뒤 다시 요청하세요.")

    return "\n".join(lines)


def trim_codex_output(output: str) -> str:
    text = output.strip()
    if not text:
        return "(빈 계획 출력)"
    return truncate_text(text, 4000)


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n... (생략됨)"


def safe_create_issue_comment(request: IssueRequest, body: str) -> None:
    if os.getenv("BOT_CREATE_PR") != "1":
        return
    if request.issue_number <= 0:
        print("유효한 이슈/PR 번호가 없어 댓글 생성을 건너뜁니다.")
        return

    try:
        comment_url = create_issue_comment(request.repository, request.issue_number, body)
        if comment_url:
            print(f"이슈 댓글 생성됨: {comment_url}")
            write_comment_marker()
    except Exception as comment_error:
        print(f"이슈 댓글 생성 실패: {comment_error}")


def write_comment_marker() -> None:
    marker_path = os.getenv("BOT_COMMENT_MARKER_FILE", "").strip()
    if not marker_path:
        return

    try:
        path = Path(marker_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("comment-posted\n", encoding="utf-8")
    except Exception as marker_error:
        print(f"댓글 마커 파일 기록 실패: {marker_error}")


def configure_output_encoding() -> None:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleCP(65001)
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def format_check_commands(config: BotConfig) -> str:
    commands = get_check_commands(config)
    if not commands:
        return "`없음`"
    return ", ".join(f"`{command}`" for command in commands)

import json
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from app.attachments import collect_attachment_context, format_attachment_context
from app.bot import (
    BotCommand,
    IssueRequest,
    build_branch_name,
    build_issue_request,
    build_task_prompt,
    parse_bot_command,
)
from app.codex_runner import create_codex_pr, run_codex_plan
from app.config import BotConfig, get_check_commands, load_config
from app.github_pr import PullRequestResult, create_issue_comment, create_test_pr
from app.repo_context import (
    MissingContextError,
    collect_context_documents,
    collect_project_summary,
    format_context_documents,
    get_external_context_root,
)
from app.runtime_secrets import MissingSecretError, get_secrets_file_path, load_runtime_secrets
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


def load_event_payload() -> dict:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH가 없어 로컬 테스트 모드로 실행합니다.")
        return {
            "action": "created",
            "comment": {
                "body": "/bot run",
                "id": 1,
                "user": {"login": "local-user"},
            },
            "issue": {
                "number": 1,
                "title": "테스트 이슈",
                "body": "샘플 요구사항입니다.",
            },
            "repository": {"full_name": "example/issue-to-pr-bot"},
        }

    payload_text = Path(event_path).read_text(encoding="utf-8-sig")
    return json.loads(payload_text)


def main() -> None:
    configure_output_encoding()
    workspace = Path.cwd()
    config = load_config(workspace)
    payload = load_event_payload()
    request = build_issue_request(payload)

    try:
        run_bot(workspace, config, request)
    except Exception as error:
        print(traceback.format_exc())
        post_failure_comment(request, config, error)
        raise


def run_bot(workspace: Path, config: BotConfig, request: IssueRequest) -> None:
    command = parse_bot_command(request.comment_body, config)
    if not command:
        print("실행 명령이 없어 종료합니다.")
        return

    if command.action == "help":
        post_help_comment(request, config)
        return

    if command.action == "status":
        snapshot = collect_status_snapshot(workspace, config)
        post_status_comment(request, config, snapshot)
        return

    available_secret_keys = load_runtime_secrets(config)
    attachment_context = format_attachment_context(collect_attachment_context(request))
    branch_name = build_branch_name(request, config)
    documents = collect_context_documents(workspace, config)
    repository_context = format_context_documents(documents)
    project_summary = collect_project_summary(workspace)
    task_prompt = build_task_prompt(
        request,
        config,
        repository_context,
        project_summary,
        available_secret_keys,
        attachment_context,
    )

    print("봇 실행 시작")
    print(f"저장소: {request.repository}")
    print(f"이슈 번호: {request.issue_number}")
    print(f"이슈 제목: {request.issue_title}")
    print(f"이슈 본문: {request.issue_body}")
    print(f"댓글 작성자: {request.comment_author}")
    print(f"봇 모드: {config.mode}")
    print(f"봇 명령: {command.action}")
    print(f"검증 명령: {format_check_commands(config)}")
    print(f"사용 가능한 secret env: {format_secret_keys_for_log(available_secret_keys)}")
    print(f"작업 브랜치: {branch_name}")
    print(f"저장소 규칙 문서: {len(documents)}개")
    print("작업 프롬프트:")
    print(task_prompt)

    if os.getenv("BOT_CREATE_PR") != "1":
        print("BOT_CREATE_PR이 1이 아니므로 PR 생성은 건너뜁니다.")
        return

    if command.action == "plan":
        result = run_codex_plan(request, workspace, config, command)
        post_plan_comment(request, config, command, result.output)
        return

    result = run_configured_mode(config.mode, request, workspace, command)
    if result.created:
        print(f"PR 생성 완료: {result.pull_request_url}")
        post_success_comment(request, config, command, result)
        return

    print("PR 생성 건너뜀: 변경사항이 없습니다.")
    post_no_changes_comment(request, config, command, result)


def run_configured_mode(
    mode: str,
    request: IssueRequest,
    workspace: Path,
    command: BotCommand | None = None,
) -> PullRequestResult:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "test-pr":
        return create_test_pr(request, workspace)
    if normalized_mode == "codex":
        return create_codex_pr(request, workspace, command)
    raise RuntimeError(f"지원하지 않는 봇 모드입니다: {mode}")


def collect_status_snapshot(workspace: Path, config: BotConfig) -> BotStatusSnapshot:
    available_secret_keys: list[str] = []
    missing_secret_keys: list[str] = []
    try:
        available_secret_keys = load_runtime_secrets(config)
    except MissingSecretError as error:
        missing_secret_keys = error.missing_keys

    context_document_count = 0
    missing_context_paths: list[str] = []
    try:
        context_document_count = len(collect_context_documents(workspace, config))
    except MissingContextError as error:
        missing_context_paths = error.missing_paths

    external_context_root = get_external_context_root()
    secrets_file_path = get_secrets_file_path()
    return BotStatusSnapshot(
        available_secret_keys=available_secret_keys,
        missing_secret_keys=missing_secret_keys,
        context_document_count=context_document_count,
        missing_context_paths=missing_context_paths,
        external_context_root=str(external_context_root) if external_context_root else None,
        secrets_file_path=str(secrets_file_path),
        secrets_file_exists=secrets_file_path.exists(),
    )


def post_help_comment(request: IssueRequest, config: BotConfig) -> None:
    body = "\n".join(
        [
            "## 봇 도움말",
            "",
            "### 명령",
            f"- `{config.command} [option=value ...]`: Codex로 작업 후 PR 생성",
            f"- `{config.plan_command} [option=value ...]`: 작업 계획만 댓글로 작성",
            f"- `{config.status_command}`: 현재 설정과 런타임 준비 상태 확인",
            f"- `{config.help_command}`: 사용 가능한 명령 안내",
            f"- `{config.mention} run ...`: 멘션으로 실행",
            f"- `{config.mention} plan ...`: 멘션으로 계획 생성",
            f"- `{config.mention} status`: 멘션으로 상태 확인",
            "",
            "### 지원 옵션",
            "- `effort=low|medium|high|xhigh`",
            "",
            "### 현재 기본 검증",
            format_markdown_list(get_check_commands(config), code=True),
            "",
            "### 예시",
            f"- `{config.command} effort=high README에 로컬 실행 방법 추가`",
            f"- `{config.plan_command} DB 마이그레이션 작업 계획`",
            f"- `{config.mention} status`",
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_status_comment(request: IssueRequest, config: BotConfig, snapshot: BotStatusSnapshot) -> None:
    body = "\n".join(
        [
            "## 봇 상태",
            "",
            "### 설정",
            f"- 모드: `{config.mode}`",
            f"- 실행 명령: `{config.command}`",
            f"- 계획 명령: `{config.plan_command}`",
            f"- 도움말 명령: `{config.help_command}`",
            f"- 상태 명령: `{config.status_command}`",
            f"- 멘션: `{config.mention}`",
            f"- 브랜치 prefix: `{config.branch_prefix}`",
            "",
            "### 검증 명령",
            format_markdown_list(get_check_commands(config), code=True),
            "",
            "### Context",
            f"- 저장소 context_paths: {len(config.context_paths)}개",
            f"- 외부 external_context_paths: {len(config.external_context_paths)}개",
            f"- 실제 로드된 문서 수: {snapshot.context_document_count}개",
            f"- 외부 context 루트: `{snapshot.external_context_root or 'not mounted'}`",
            "",
            "### Secret",
            f"- secret env 파일: `{snapshot.secrets_file_path}`",
            f"- secret env 파일 존재: `{'yes' if snapshot.secrets_file_exists else 'no'}`",
            f"- 사용 가능한 secret key: {format_secret_keys_for_log(snapshot.available_secret_keys)}",
            "",
            "### 누락 항목",
            format_missing_status(snapshot),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_plan_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    plan_output: str,
) -> None:
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            "- 상태: `planned`",
            f"- 모드: `{config.mode}`",
            f"- 명령: `{command.action}`",
            f"- 트리거: `{command.trigger}`",
            "",
            "### 계획",
            trim_codex_output(plan_output),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_success_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    result: PullRequestResult,
) -> None:
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            "- 상태: `success`",
            f"- 모드: `{config.mode}`",
            f"- 명령: `{command.action}`",
            f"- 브랜치: `{result.branch_name}`",
            f"- PR: {result.pull_request_url}",
            f"- 검증: {format_check_commands(config)}",
            "",
            "### 변경 파일",
            format_changed_files(result.changed_files),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_no_changes_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    result: PullRequestResult,
) -> None:
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            "- 상태: `no_changes`",
            f"- 모드: `{config.mode}`",
            f"- 명령: `{command.action}`",
            f"- 브랜치: `{result.branch_name}`",
            "- 사유: Codex 실행 후 커밋할 변경사항이 없습니다.",
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_failure_comment(request: IssueRequest, config: BotConfig, error: Exception) -> None:
    body = "\n".join(
        [
            "## 실행 결과",
            "",
            "### 요약",
            "- 상태: `failed`",
            f"- 모드: `{config.mode}`",
            f"- 오류: `{type(error).__name__}: {error}`",
            "",
            "### 상세",
            format_failure_detail(error),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def format_changed_files(changed_files: list[str]) -> str:
    if not changed_files:
        return "- 없음"

    displayed = changed_files[:20]
    lines = [f"- `{path}`" for path in displayed]
    if len(changed_files) > len(displayed):
        lines.append(f"- 외 {len(changed_files) - len(displayed)}개")
    return "\n".join(lines)


def format_check_commands(config: BotConfig) -> str:
    commands = get_check_commands(config)
    if not commands:
        return "`none`"
    return ", ".join(f"`{command}`" for command in commands)


def format_secret_keys_for_log(secret_keys: list[str]) -> str:
    if not secret_keys:
        return "`none`"
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
        lines.append("- 누락된 context")
        lines.extend(f"  - `{path}`" for path in snapshot.missing_context_paths)
    if snapshot.missing_secret_keys:
        lines.append("- 누락된 secret env")
        lines.extend(f"  - `{key}`" for key in snapshot.missing_secret_keys)
    if not lines:
        return "- 없음"
    return "\n".join(lines)


def format_failure_detail(error: Exception) -> str:
    if isinstance(error, VerificationError):
        output = truncate_text(error.output.strip(), 1800)
        return "\n".join(
            [
                f"검증 명령 `{error.command}` 출력:",
                "",
                "```text",
                output or "(no output)",
                "```",
            ]
        )

    if isinstance(error, MissingContextError):
        return "\n".join(
            [
                "누락된 context:",
                "",
                "\n".join(f"- `{path}`" for path in error.missing_paths),
            ]
        )

    if isinstance(error, MissingSecretError):
        return "\n".join(
            [
                "누락된 secret env:",
                "",
                "\n".join(f"- `{key}`" for key in error.missing_keys),
            ]
        )

    return "Actions 로그에서 자세한 실패 지점을 확인해 주세요."


def format_run_url() -> str:
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if not repository or not run_id:
        return ""
    return f"Actions 로그: {server_url}/{repository}/actions/runs/{run_id}"


def trim_codex_output(output: str) -> str:
    text = output.strip()
    if not text:
        return "(계획 출력 없음)"
    return truncate_text(text, 4000)


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n... (truncated)"


def safe_create_issue_comment(request: IssueRequest, body: str) -> None:
    if os.getenv("BOT_CREATE_PR") != "1":
        return

    try:
        comment_url = create_issue_comment(request.repository, request.issue_number, body)
        if comment_url:
            print(f"이슈 댓글 작성 완료: {comment_url}")
    except Exception as comment_error:
        print(f"이슈 댓글 작성 실패: {comment_error}")


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    main()

import json
import os
import sys
import traceback
from pathlib import Path

from app.bot import (
    BotCommand,
    IssueRequest,
    build_branch_name,
    build_issue_request,
    build_task_prompt,
    parse_bot_command,
)
from app.codex_runner import create_codex_pr, run_codex_plan
from app.config import BotConfig, load_config
from app.github_pr import PullRequestResult, create_issue_comment, create_test_pr
from app.repo_context import collect_context_documents, format_context_documents
from app.verification import VerificationError


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
        print("봇 실행 명령이 없어 종료합니다.")
        return

    branch_name = build_branch_name(request, config)
    documents = collect_context_documents(workspace, config)
    repository_context = format_context_documents(documents)
    task_prompt = build_task_prompt(request, config, repository_context)

    print("봇 실행 시작")
    print(f"저장소: {request.repository}")
    print(f"이슈 번호: {request.issue_number}")
    print(f"이슈 제목: {request.issue_title}")
    print(f"이슈 본문: {request.issue_body}")
    print(f"댓글 작성자: {request.comment_author}")
    print(f"봇 모드: {config.mode}")
    print(f"봇 명령: {command.action}")
    print(f"검증 명령: {config.test_command}")
    print(f"작업 브랜치: {branch_name}")
    print(f"저장소 규칙 문서: {len(documents)}개")
    print("작업 프롬프트:")
    print(task_prompt)

    if os.getenv("BOT_CREATE_PR") != "1":
        print("BOT_CREATE_PR이 1이 아니므로 PR 생성을 건너뜁니다.")
        return

    if command.action == "plan":
        result = run_codex_plan(request, workspace, config)
        post_plan_comment(request, config, command, result.output)
        return

    result = run_configured_mode(config.mode, request, workspace)
    if result.created:
        print(f"PR 생성 완료: {result.pull_request_url}")
        post_success_comment(request, config, result)
        return

    print("PR 생성 건너뜀: 변경사항이 없습니다.")
    post_no_changes_comment(request, config, result)


def run_configured_mode(mode: str, request: IssueRequest, workspace: Path) -> PullRequestResult:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "test-pr":
        return create_test_pr(request, workspace)
    if normalized_mode == "codex":
        return create_codex_pr(request, workspace)
    raise RuntimeError(f"지원하지 않는 봇 모드입니다: {mode}")


def post_plan_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    plan_output: str,
) -> None:
    body = "\n".join(
        [
            "봇이 작업 계획을 작성했습니다.",
            "",
            f"- 모드: `{config.mode}`",
            f"- 명령: `{command.action}`",
            f"- 트리거: `{command.trigger}`",
            "",
            "계획:",
            "",
            trim_codex_output(plan_output),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_success_comment(request: IssueRequest, config: BotConfig, result: PullRequestResult) -> None:
    body = "\n".join(
        [
            "봇 작업이 완료되었습니다.",
            "",
            f"- 모드: `{config.mode}`",
            f"- 브랜치: `{result.branch_name}`",
            f"- PR: {result.pull_request_url}",
            f"- 검증: `{config.test_command}` 통과",
            "",
            "변경 파일:",
            format_changed_files(result.changed_files),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_no_changes_comment(request: IssueRequest, config: BotConfig, result: PullRequestResult) -> None:
    body = "\n".join(
        [
            "봇 작업을 중단했습니다.",
            "",
            f"- 모드: `{config.mode}`",
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
            "봇 작업이 실패해서 PR 생성을 중단했습니다.",
            "",
            f"- 모드: `{config.mode}`",
            f"- 사유: `{type(error).__name__}: {error}`",
            "",
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

import json
import os
from pathlib import Path

from app.bot import IssueRequest, build_branch_name, build_issue_request, build_task_prompt, should_run_bot
from app.codex_runner import create_codex_pr
from app.config import load_config
from app.github_pr import PullRequestResult, create_test_pr


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
    workspace = Path.cwd()
    config = load_config(workspace)
    payload = load_event_payload()
    request = build_issue_request(payload)

    if not should_run_bot(request.comment_body, config):
        print("봇 실행 명령이 없어 종료합니다.")
        return

    branch_name = build_branch_name(request, config)
    task_prompt = build_task_prompt(request, config)

    print("봇 실행 시작")
    print(f"저장소: {request.repository}")
    print(f"이슈 번호: {request.issue_number}")
    print(f"이슈 제목: {request.issue_title}")
    print(f"이슈 본문: {request.issue_body}")
    print(f"댓글 작성자: {request.comment_author}")
    print(f"봇 모드: {config.mode}")
    print(f"검증 명령: {config.test_command}")
    print(f"작업 브랜치: {branch_name}")
    print("작업 프롬프트:")
    print(task_prompt)

    if os.getenv("BOT_CREATE_PR") != "1":
        print("BOT_CREATE_PR이 1이 아니므로 PR 생성을 건너뜁니다.")
        return

    result = run_configured_mode(config.mode, request, workspace)
    if result.created:
        print(f"PR 생성 완료: {result.pull_request_url}")
        return

    print("PR 생성 건너뜀: 변경사항이 없습니다.")


def run_configured_mode(mode: str, request: IssueRequest, workspace: Path) -> PullRequestResult:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "test-pr":
        return create_test_pr(request, workspace)
    if normalized_mode == "codex":
        return create_codex_pr(request, workspace)
    raise RuntimeError(f"지원하지 않는 봇 모드입니다: {mode}")


if __name__ == "__main__":
    main()

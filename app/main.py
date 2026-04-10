import json
import os
from pathlib import Path


def load_event_payload():
    event_path = os.getenv("GITHUB_EVENT_PATH")

    if not event_path:
        print("GITHUB_EVENT_PATH가 없습니다. 로컬 테스트 모드로 실행합니다.")
        return {
            "action": "created",
            "comment": {"body": "/bot run"},
            "issue": {"number": 1, "title": "테스트 이슈", "body": "샘플 요구사항입니다."},
            "repository": {"full_name": "example/issue-to-pr-bot"},
        }

    payload_text = Path(event_path).read_text(encoding="utf-8")
    return json.loads(payload_text)


def should_run_bot(payload):
    comment_body = payload.get("comment", {}).get("body", "")
    return "/bot run" in comment_body


def main():
    payload = load_event_payload()

    if not should_run_bot(payload):
        print("봇 실행 명령이 없어서 종료합니다.")
        return

    issue = payload.get("issue", {})
    repo = payload.get("repository", {})

    print("봇 실행 시작")
    print(f"저장소: {repo.get('full_name')}")
    print(f"이슈 번호: {issue.get('number')}")
    print(f"이슈 제목: {issue.get('title')}")
    print(f"이슈 본문: {issue.get('body')}")


if __name__ == "__main__":
    main()

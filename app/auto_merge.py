import os

from app.github_pr import create_issue_comment, try_requested_auto_merge_pull_request


def handle_pull_request_review_event(payload: dict) -> None:
    review = payload.get("review") or {}
    pull_request = payload.get("pull_request") or {}
    repository = (payload.get("repository") or {}).get("full_name") or os.getenv("GITHUB_REPOSITORY")
    pull_request_number = pull_request.get("number")
    review_state = (review.get("state") or "").lower()

    if not repository or not pull_request_number:
        print("PR review payload가 불완전해서 auto-merge를 건너뜁니다.")
        return

    if review_state != "approved":
        print(f"review state가 approved가 아니어서 auto-merge를 건너뜁니다: {review_state}")
        return

    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("auto-merge를 수행하려면 GitHub token이 필요합니다.")

    merge_sha = try_requested_auto_merge_pull_request(repository, int(pull_request_number), token)
    if not merge_sha:
        return

    body = "\n".join(
        [
            "## 자동 머지 결과",
            "",
            "- 상태: `merged`",
            f"- PR: #{pull_request_number}",
            f"- merge commit: `{merge_sha}`",
        ]
    )
    create_issue_comment(repository, int(pull_request_number), body, token=token)

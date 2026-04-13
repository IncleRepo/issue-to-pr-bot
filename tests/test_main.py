import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.bot import BotCommand, IssueRequest
from app.config import BotConfig
from app.main import (
    classify_failure_stage,
    collect_status_snapshot,
    format_failure_next_steps,
    format_missing_status,
    handle_pull_request_review_payload,
    safe_create_issue_comment,
)
from app.verification import VerificationError


class MainTest(unittest.TestCase):
    def test_collect_status_snapshot_reports_missing_context_and_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            config = BotConfig(
                required_context_paths=["external:product/domain.md"],
                required_secret_env=["DB_URL"],
            )
            with patch.dict(os.environ, {}, clear=True):
                snapshot = collect_status_snapshot(workspace, config)

        self.assertEqual(snapshot.missing_secret_keys, ["DB_URL"])
        self.assertEqual(snapshot.missing_context_paths, ["external:product/domain.md"])
        self.assertEqual(snapshot.available_secret_keys, [])

    def test_format_missing_status_handles_empty_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch.dict(os.environ, {}, clear=True):
                snapshot = collect_status_snapshot(workspace, BotConfig())

        self.assertEqual(format_missing_status(snapshot), "- 없음")

    def test_classify_failure_stage_marks_verification_failures(self) -> None:
        error = VerificationError("python -m unittest", "bad", 1)
        self.assertEqual(classify_failure_stage(error), "verification")

    def test_format_failure_next_steps_includes_retry_command(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=1,
            issue_title="Failing issue",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 다시 진행해줘. codex high로",
            comment_author="IncleRepo",
            comment_id=1,
        )
        command = BotCommand("run", "@incle-issue-to-pr-bot", "다시 진행해줘. codex high로", {"effort": "high"})

        message = format_failure_next_steps(
            request,
            BotConfig(),
            command,
            VerificationError("python -m unittest", "bad", 1),
        )

        self.assertIn("@incle-issue-to-pr-bot 다시 진행해줘. codex high로", message)
        self.assertIn("@incle-issue-to-pr-bot status", message)

    @patch("app.main.run_bot")
    @patch("app.main.handle_pull_request_review_event")
    def test_pull_request_review_with_mention_runs_bot(self, auto_merge_mock, run_bot_mock) -> None:
        payload = {
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
            "pull_request": {
                "number": 7,
                "title": "Fix parser",
                "body": "PR body",
                "base": {"ref": "main"},
                "head": {"ref": "bot/issue-7-fix-parser"},
                "html_url": "https://github.com/IncleRepo/issue-to-pr-bot/pull/7",
            },
            "review": {
                "id": 11,
                "state": "changes_requested",
                "body": "@incle-issue-to-pr-bot 이 리뷰 반영해줘",
                "user": {"login": "reviewer"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            handle_pull_request_review_payload(Path(temp_dir), BotConfig(), payload)

        run_bot_mock.assert_called_once()
        auto_merge_mock.assert_not_called()

    @patch("app.main.run_bot")
    @patch("app.main.handle_pull_request_review_event")
    def test_pull_request_review_without_mention_uses_auto_merge_handler(self, auto_merge_mock, run_bot_mock) -> None:
        payload = {
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
            "pull_request": {"number": 7, "title": "Fix parser", "body": "PR body"},
            "review": {
                "id": 12,
                "state": "approved",
                "body": "Looks good",
                "user": {"login": "reviewer"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            handle_pull_request_review_payload(Path(temp_dir), BotConfig(), payload)

        auto_merge_mock.assert_called_once_with(payload)
        run_bot_mock.assert_not_called()

    @patch("app.runtime.comments.create_issue_comment", return_value="https://example.com/comment/1")
    def test_safe_create_issue_comment_writes_marker_when_comment_succeeds(self, create_comment_mock) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=3,
            issue_title="Failure case",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 다시 시도해줘",
            comment_author="IncleRepo",
            comment_id=3,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = Path(temp_dir) / "comment-posted"
            with patch.dict(
                os.environ,
                {
                    "BOT_CREATE_PR": "1",
                    "BOT_COMMENT_MARKER_FILE": str(marker_path),
                },
                clear=True,
            ):
                safe_create_issue_comment(request, "body")

            self.assertTrue(marker_path.exists())
            self.assertIn("comment-posted", marker_path.read_text(encoding="utf-8"))

        create_comment_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

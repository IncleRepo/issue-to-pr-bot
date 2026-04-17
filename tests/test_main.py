import os
import subprocess
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
    run_bot,
    safe_create_issue_comment,
)
from app.github_pr import MergeRequestResult
from app.runtime.orchestrator import is_auto_merge_signal_event
from app.verification import collect_workspace_changes
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

    @patch("app.verification.subprocess.run")
    def test_collect_workspace_changes_ignores_output_artifacts(self, subprocess_run_mock) -> None:
        subprocess_run_mock.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="?? bot-output/pr-body.md\n?? bot-output/pr-summary.md\n M src/main.js\n",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            changed_files = collect_workspace_changes(Path(temp_dir))

        self.assertEqual(changed_files, ["src/main.js"])

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

        message = format_failure_next_steps(request, BotConfig(), command, RuntimeError("boom"))

        self.assertIn("@incle-issue-to-pr-bot 다시 진행해줘. codex high로", message)
        self.assertIn("@incle-issue-to-pr-bot status", message)
        self.assertNotIn("logs\\agent.log", message)
        self.assertIn("로컬 agent 창 출력", message)

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

        auto_merge_mock.assert_called_once_with(payload, workspace=Path(temp_dir), config=BotConfig())
        run_bot_mock.assert_not_called()

    def test_is_auto_merge_signal_event_detects_check_run_completion(self) -> None:
        payload = {
            "action": "completed",
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
            "check_run": {
                "id": 9,
                "conclusion": "success",
                "pull_requests": [{"number": 7}],
            },
        }

        self.assertTrue(is_auto_merge_signal_event(payload, BotConfig()))

    def test_is_auto_merge_signal_event_detects_status_event(self) -> None:
        payload = {
            "state": "success",
            "sha": "deadbeef",
            "context": "CI / lint-and-format",
            "branches": [{"name": "bot/issue-7-fix-parser"}],
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
        }

        self.assertTrue(is_auto_merge_signal_event(payload, BotConfig()))

    def test_is_auto_merge_signal_event_ignores_review_mentions(self) -> None:
        payload = {
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
            "pull_request": {"number": 7, "title": "Fix parser", "body": "PR body"},
            "review": {
                "id": 12,
                "state": "approved",
                "body": "@incle-issue-to-pr-bot 이 리뷰 반영해줘",
                "user": {"login": "reviewer"},
            },
        }

        self.assertFalse(is_auto_merge_signal_event(payload, BotConfig()))

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

    @patch("app.runtime.comments.create_issue_comment")
    def test_safe_create_issue_comment_skips_missing_issue_number(self, create_comment_mock) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=0,
            issue_title="Failure case",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 다시 시도해줘",
            comment_author="IncleRepo",
            comment_id=3,
        )

        with patch.dict(os.environ, {"BOT_CREATE_PR": "1"}, clear=True):
            safe_create_issue_comment(request, "body")

        create_comment_mock.assert_not_called()

    @patch("app.runtime.orchestrator.post_merge_request_comment")
    @patch("app.runtime.orchestrator.request_pull_request_merge")
    @patch("app.runtime.orchestrator.prepare_prompt")
    def test_run_bot_skips_prompt_preparation_for_merge_action(
        self,
        prepare_prompt_mock,
        request_pull_request_merge_mock,
        post_merge_request_comment_mock,
    ) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=21,
            issue_title="Merge this",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 승인되면 머지해줘",
            comment_author="IncleRepo",
            comment_id=7,
            is_pull_request=True,
            pull_request_number=21,
        )
        request_pull_request_merge_mock.return_value = MergeRequestResult(
            pull_request_url="https://example.com/pr/21",
            requested=True,
            merged=False,
            merge_sha=None,
        )

        with patch.dict(os.environ, {"BOT_CREATE_PR": "1", "GITHUB_TOKEN": "token"}, clear=True):
            run_bot(Path("."), BotConfig(), request)

        prepare_prompt_mock.assert_not_called()
        request_pull_request_merge_mock.assert_called_once_with("IncleRepo/issue-to-pr-bot", 21, "token")
        post_merge_request_comment_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from unittest.mock import patch

from app.auto_merge import (
    handle_auto_merge_event,
    handle_pull_request_review_event,
    request_pull_request_merge_with_conflict_recovery,
    try_requested_auto_merge_pull_request_with_conflict_recovery,
)
from app.config import BotConfig, GitSyncRule
from app.github_pr import BOT_AUTO_MERGE_MARKER, BOT_PR_MARKER, MergeRequestResult, is_bot_pull_request, try_auto_merge_pull_request


class AutoMergeTest(unittest.TestCase):
    def test_is_bot_pull_request_requires_marker(self) -> None:
        self.assertTrue(is_bot_pull_request({"body": f"hello\n{BOT_PR_MARKER}"}))
        self.assertFalse(is_bot_pull_request({"body": "hello"}))

    @patch("app.github_pr.github_request")
    def test_try_auto_merge_pull_request_merges_when_marker_exists(self, github_request_mock) -> None:
        github_request_mock.side_effect = [
            {"number": 3, "state": "open", "body": f"{BOT_PR_MARKER}\n{BOT_AUTO_MERGE_MARKER}"},
            {"sha": "abc123"},
        ]

        sha = try_auto_merge_pull_request("IncleRepo/issue-to-pr-bot", 3, "token")

        self.assertEqual(sha, "abc123")

    @patch("app.github_pr.github_request")
    def test_try_auto_merge_pull_request_skips_non_bot_pr(self, github_request_mock) -> None:
        github_request_mock.return_value = {"number": 3, "state": "open", "body": "human"}

        sha = try_auto_merge_pull_request("IncleRepo/issue-to-pr-bot", 3, "token")

        self.assertIsNone(sha)

    @patch("app.auto_merge.create_issue_comment")
    @patch("app.auto_merge.request_pull_request_merge_with_conflict_recovery")
    def test_handle_pull_request_review_event_comments_after_merge(self, request_merge_mock, create_comment_mock) -> None:
        request_merge_mock.return_value = MergeRequestResult(
            pull_request_url="https://example.com/pr/7",
            requested=True,
            merged=True,
            merge_sha="abc123",
        )
        payload = {
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
            "review": {"state": "approved"},
            "pull_request": {"number": 7},
        }

        with patch.dict("os.environ", {"BOT_GITHUB_TOKEN": "token"}, clear=True):
            handle_pull_request_review_event(payload)

        request_merge_mock.assert_called_once_with(
            "IncleRepo/issue-to-pr-bot",
            7,
            "token",
            workspace=None,
            config=None,
        )
        create_comment_mock.assert_called_once()

    @patch("app.auto_merge.create_issue_comment")
    @patch("app.auto_merge.try_requested_auto_merge_pull_request")
    def test_handle_auto_merge_event_retries_on_successful_check_run(self, try_merge_mock, create_comment_mock) -> None:
        try_merge_mock.return_value = "abc123"
        payload = {
            "action": "completed",
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
            "check_run": {
                "id": 99,
                "conclusion": "success",
                "pull_requests": [{"number": 7}],
            },
        }

        with patch.dict("os.environ", {"BOT_GITHUB_TOKEN": "token"}, clear=True):
            handle_auto_merge_event(payload)

        try_merge_mock.assert_called_once_with("IncleRepo/issue-to-pr-bot", 7, "token")
        create_comment_mock.assert_called_once()

    @patch("app.auto_merge.create_issue_comment")
    @patch("app.auto_merge.try_requested_auto_merge_pull_request")
    def test_handle_auto_merge_event_skips_failed_check_suite(self, try_merge_mock, create_comment_mock) -> None:
        payload = {
            "action": "completed",
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
            "check_suite": {
                "id": 77,
                "conclusion": "failure",
                "pull_requests": [{"number": 8}],
            },
        }

        with patch.dict("os.environ", {"BOT_GITHUB_TOKEN": "token"}, clear=True):
            handle_auto_merge_event(payload)

        try_merge_mock.assert_not_called()
        create_comment_mock.assert_not_called()

    @patch("app.auto_merge.create_issue_comment")
    @patch("app.auto_merge.github_request")
    @patch("app.auto_merge.try_requested_auto_merge_pull_request")
    def test_handle_auto_merge_event_uses_status_event_branch_lookup(
        self,
        try_merge_mock,
        github_request_mock,
        create_comment_mock,
    ) -> None:
        try_merge_mock.return_value = "abc123"
        github_request_mock.return_value = [{"number": 21}]
        payload = {
            "state": "success",
            "sha": "deadbeef",
            "context": "CI / lint-and-format",
            "branches": [{"name": "bot/issue-21-feature"}],
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
        }

        with patch.dict("os.environ", {"BOT_GITHUB_TOKEN": "token"}, clear=True):
            handle_auto_merge_event(payload)

        try_merge_mock.assert_called_once_with("IncleRepo/issue-to-pr-bot", 21, "token")
        create_comment_mock.assert_called_once()

    @patch("app.auto_merge.create_issue_comment")
    @patch("app.auto_merge.request_pull_request_merge_with_conflict_recovery")
    @patch("app.auto_merge.maybe_prepare_pull_request_for_merge")
    def test_handle_pull_request_review_event_applies_before_merge_rule_when_configured(
        self,
        prepare_merge_mock,
        request_merge_mock,
        create_comment_mock,
    ) -> None:
        request_merge_mock.return_value = MergeRequestResult(
            pull_request_url="https://example.com/pr/7",
            requested=True,
            merged=True,
            merge_sha="abc123",
        )
        payload = {
            "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
            "review": {"state": "approved"},
            "pull_request": {"number": 7},
        }
        config = BotConfig(
            git_sync_rule=GitSyncRule(
                phase="before_merge",
                action="rebase",
                base_branch="main",
                confidence="high",
            ),
            git_sync_rules=[
                GitSyncRule(
                    phase="before_merge",
                    action="rebase",
                    base_branch="main",
                    confidence="high",
                )
            ],
        )

        with patch.dict("os.environ", {"BOT_GITHUB_TOKEN": "token"}, clear=True):
            handle_pull_request_review_event(payload, workspace=Path("."), config=config)

        prepare_merge_mock.assert_called_once_with(
            Path("."),
            config,
            "IncleRepo/issue-to-pr-bot",
            7,
            "token",
        )
        request_merge_mock.assert_called_once_with(
            "IncleRepo/issue-to-pr-bot",
            7,
            "token",
            workspace=Path("."),
            config=config,
        )
        create_comment_mock.assert_called_once()

    @patch("app.auto_merge.recover_pull_request_merge_conflicts_with_codex")
    @patch("app.auto_merge.get_pull_request")
    @patch("app.auto_merge.request_pull_request_merge")
    def test_request_pull_request_merge_with_conflict_recovery_retries_dirty_pr(
        self,
        request_merge_mock,
        get_pull_request_mock,
        recover_mock,
    ) -> None:
        request_merge_mock.side_effect = [
            MergeRequestResult(
                pull_request_url="https://example.com/pr/7",
                requested=True,
                merged=False,
                merge_sha=None,
            ),
            MergeRequestResult(
                pull_request_url="https://example.com/pr/7",
                requested=True,
                merged=True,
                merge_sha="abc123",
            ),
        ]
        get_pull_request_mock.return_value = {"number": 7, "mergeable_state": "dirty"}

        result = request_pull_request_merge_with_conflict_recovery(
            "IncleRepo/issue-to-pr-bot",
            7,
            "token",
            workspace=Path("."),
            config=BotConfig(),
        )

        self.assertEqual(result.merge_sha, "abc123")
        self.assertEqual(request_merge_mock.call_count, 2)
        recover_mock.assert_called_once_with(Path("."), BotConfig(), "IncleRepo/issue-to-pr-bot", 7, "token")

    @patch("app.auto_merge.recover_pull_request_merge_conflicts_with_codex")
    @patch("app.auto_merge.get_pull_request")
    @patch("app.auto_merge.try_requested_auto_merge_pull_request")
    def test_try_requested_auto_merge_pull_request_with_conflict_recovery_retries_dirty_pr(
        self,
        try_merge_mock,
        get_pull_request_mock,
        recover_mock,
    ) -> None:
        try_merge_mock.side_effect = [None, "abc123"]
        get_pull_request_mock.return_value = {"number": 7, "mergeable_state": "dirty"}

        merge_sha = try_requested_auto_merge_pull_request_with_conflict_recovery(
            "IncleRepo/issue-to-pr-bot",
            7,
            "token",
            workspace=Path("."),
            config=BotConfig(),
        )

        self.assertEqual(merge_sha, "abc123")
        self.assertEqual(try_merge_mock.call_count, 2)
        recover_mock.assert_called_once_with(Path("."), BotConfig(), "IncleRepo/issue-to-pr-bot", 7, "token")


if __name__ == "__main__":
    unittest.main()

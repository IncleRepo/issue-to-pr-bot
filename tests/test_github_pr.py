import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.bot import IssueRequest
from app.config import BotConfig
from app.github_pr import (
    BOT_AUTO_MERGE_MARKER,
    BOT_PR_MARKER,
    BaseSyncResult,
    build_pull_request_body,
    matches_protected_path,
    request_pull_request_merge,
    sync_pull_request_branch_with_base,
    write_marker_file,
)


class GitHubPrTest(unittest.TestCase):
    def test_write_marker_file_creates_issue_summary(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=3,
            issue_title="Add sample output",
            issue_body="요구사항 본문",
            comment_body="@incle-issue-to-pr-bot 샘플 출력 추가해줘",
            comment_author="IncleRepo",
            comment_id=99,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = write_marker_file(
                request,
                Path(temp_dir),
                BotConfig(output_dir="custom-output"),
            )
            content = output_file.read_text(encoding="utf-8")

        self.assertEqual(output_file.name, "issue-3.md")
        self.assertEqual(output_file.parent.name, "custom-output")
        self.assertIn("# Issue #3", content)
        self.assertIn("요구사항 본문", content)
        self.assertIn("bot/issue-3-comment-99-add-sample-output", content)

    def test_matches_protected_path(self) -> None:
        self.assertTrue(matches_protected_path(".github/workflows/ci.yml", ".github/workflows/**"))
        self.assertTrue(matches_protected_path("secret.pem", "*.pem"))
        self.assertFalse(matches_protected_path("README.md", ".github/workflows/**"))

    def test_build_pull_request_body_uses_template_placeholders(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=7,
            issue_title="Add docs",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot README 수정해줘. codex high로.",
            comment_author="IncleRepo",
            comment_id=10,
        )
        config = BotConfig(
            mode="codex",
            check_commands=["python -m compileall -q app tests", "python -m unittest discover -s tests"],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "\n".join(
                    [
                        "## Summary",
                        "",
                        "{{CHANGED_FILES}}",
                        "",
                        "## Verification",
                        "",
                        "{{VERIFICATION_COMMANDS}}",
                        "",
                        "## Issue",
                        "",
                        "Closes #{{ISSUE_NUMBER}}",
                        "",
                        "## Notes",
                        "",
                        "- Trigger comment: `{{TRIGGER_COMMAND}}`",
                        "- Bot mode: `{{BOT_MODE}}`",
                    ]
                ),
                encoding="utf-8",
            )

            body = build_pull_request_body(
                request,
                config,
                workspace,
                ["README.md", "app/main.py"],
            )

        self.assertIn("Closes #7", body)
        self.assertIn("- `README.md`", body)
        self.assertIn("- `app/main.py`", body)
        self.assertIn("- [x] `python -m compileall -q app tests`", body)
        self.assertIn("- [x] `python -m unittest discover -s tests`", body)
        self.assertIn("- Trigger comment: `@incle-issue-to-pr-bot README 수정해줘. codex high로.`", body)
        self.assertIn("- Bot mode: `codex`", body)
        self.assertIn(BOT_PR_MARKER, body)

    def test_build_pull_request_body_falls_back_without_template(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=8,
            issue_title="Fallback body",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot README 정리해줘",
            comment_author="IncleRepo",
            comment_id=11,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            body = build_pull_request_body(
                request,
                BotConfig(check_commands=["python -m unittest discover -s tests"]),
                Path(temp_dir),
                ["README.md"],
            )

        self.assertIn("## Summary", body)
        self.assertIn("Closes #8", body)
        self.assertIn("- `README.md`", body)
        self.assertIn(BOT_PR_MARKER, body)

    def test_build_pull_request_title_template_is_used(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=9,
            issue_title="Custom title",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 제목만 확인해줘",
            comment_author="IncleRepo",
            comment_id=12,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "Closes #{{ISSUE_NUMBER}}",
                encoding="utf-8",
            )

            config = BotConfig(pr_title_template="BOT-{issue_number}: {issue_title}")
            body = build_pull_request_body(request, config, workspace, ["README.md"])

        self.assertEqual(body, f"Closes #9\n\n{BOT_PR_MARKER}")

    def test_sync_pull_request_branch_with_base_detects_clean_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch("app.github_pr.configure_git"),
                patch("app.github_pr.run_git") as run_git_mock,
                patch(
                    "app.github_pr.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=["git"],
                        returncode=0,
                        stdout="Automatic merge went well; stopped before committing as requested.\n",
                    ),
                ),
            ):
                result = sync_pull_request_branch_with_base(workspace, "main")

        run_git_mock.assert_called_once_with(["fetch", "origin", "main"], workspace)
        self.assertEqual(result, BaseSyncResult(attempted=True, up_to_date=False, has_conflicts=False))

    def test_sync_pull_request_branch_with_base_detects_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch("app.github_pr.configure_git"),
                patch("app.github_pr.run_git"),
                patch(
                    "app.github_pr.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(args=["git"], returncode=1, stdout="CONFLICT\n"),
                        subprocess.CompletedProcess(args=["git"], returncode=0, stdout="app/main.py\n"),
                    ],
                ),
            ):
                result = sync_pull_request_branch_with_base(workspace, "main")

        self.assertEqual(result, BaseSyncResult(attempted=True, up_to_date=False, has_conflicts=True))

    @patch("app.github_pr.try_auto_merge_pull_request")
    @patch("app.github_pr.github_request")
    def test_request_pull_request_merge_records_marker_and_attempts_merge(self, github_request_mock, try_merge_mock) -> None:
        github_request_mock.side_effect = [
            {"number": 5, "body": BOT_PR_MARKER, "html_url": "https://example.com/pr/5"},
            {"number": 5, "body": f"{BOT_PR_MARKER}\n\n{BOT_AUTO_MERGE_MARKER}", "html_url": "https://example.com/pr/5"},
        ]
        try_merge_mock.return_value = "abc123"

        result = request_pull_request_merge("IncleRepo/issue-to-pr-bot", 5, "token")

        self.assertTrue(result.requested)
        self.assertTrue(result.merged)
        self.assertEqual(result.merge_sha, "abc123")
        self.assertEqual(result.pull_request_url, "https://example.com/pr/5")


if __name__ == "__main__":
    unittest.main()

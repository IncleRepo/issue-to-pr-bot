import unittest
from pathlib import Path
from unittest.mock import patch

from app.bot import BotRuntimeOptions, IssueRequest
from app.codex_runner import create_codex_pr
from app.codex_provider import build_codex_command, get_effort
from app.github_pr import BaseSyncResult, CheckoutTarget, PullRequestResult
from app.config import BotConfig


class CodexRunnerTest(unittest.TestCase):
    def test_build_codex_command_uses_ephemeral_noninteractive_exec(self) -> None:
        workspace = Path("/workspace")
        command = build_codex_command(workspace)

        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn("--cd", command)
        self.assertEqual(command[command.index("--cd") + 1], str(workspace))
        self.assertEqual(command[-1], "-")

    def test_build_codex_command_can_write_last_message(self) -> None:
        output_path = Path("/tmp/last-message.txt")
        command = build_codex_command(Path("/workspace"), output_last_message=output_path)

        self.assertEqual(command[command.index("--output-last-message") + 1], str(output_path))
        self.assertEqual(command[-1], "-")

    def test_build_codex_command_can_set_effort(self) -> None:
        command = build_codex_command(Path("/workspace"), effort="high")

        self.assertIn("-c", command)
        self.assertIn('reasoning_effort="high"', command)

    def test_get_effort_validates_values(self) -> None:
        runtime_options = BotRuntimeOptions(mode="codex", provider="codex", verify=True, effort="xhigh")
        invalid_runtime_options = BotRuntimeOptions(mode="codex", provider="codex", verify=True, effort="turbo")

        self.assertEqual(get_effort(None, runtime_options), "xhigh")
        with self.assertRaises(ValueError):
            get_effort(None, invalid_runtime_options)

    def test_create_codex_pr_syncs_pr_branch_before_running_codex(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title="Resolve conflicts",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot sync with main and resolve conflict.",
            comment_author="IncleRepo",
            comment_id=1,
            is_pull_request=True,
            pull_request_number=12,
        )
        runtime_options = BotRuntimeOptions(mode="codex", provider="codex", verify=False, sync_base=True)

        with (
            patch(
                "app.codex_runner.checkout_request_target",
                return_value=CheckoutTarget(branch_name="bot/pr-12", base_branch="main"),
            ),
            patch(
                "app.codex_runner.sync_pull_request_branch_with_base",
                return_value=BaseSyncResult(attempted=True, has_conflicts=True),
            ) as sync_mock,
            patch("app.codex_runner.run_codex"),
            patch(
                "app.codex_runner.commit_push_and_open_pr",
                return_value=PullRequestResult(
                    branch_name="bot/pr-12",
                    pull_request_url="https://example.com/pr/12",
                    created=True,
                    changed_files=["app/main.py"],
                ),
            ),
        ):
            result = create_codex_pr(request, Path("."), BotConfig(), runtime_options=runtime_options)

        sync_mock.assert_called_once_with(Path("."), "main")
        self.assertEqual(result.branch_name, "bot/pr-12")


if __name__ == "__main__":
    unittest.main()

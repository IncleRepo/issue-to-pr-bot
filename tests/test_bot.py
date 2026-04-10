import unittest

from app.bot import (
    IssueRequest,
    build_branch_name,
    build_issue_request,
    parse_bot_command,
    should_run_bot,
    should_run_for_mention,
)
from app.config import BotConfig


class BotTest(unittest.TestCase):
    def test_should_run_bot_requires_command(self) -> None:
        self.assertTrue(should_run_bot("/bot run"))
        self.assertTrue(should_run_bot("please /bot run this"))
        self.assertFalse(should_run_bot("/bot help"))
        self.assertTrue(should_run_bot("/ai go", BotConfig(command="/ai go")))

    def test_should_run_bot_accepts_configured_mention(self) -> None:
        config = BotConfig(mention="@incle-issue-to-pr-bot")

        self.assertTrue(should_run_bot("@incle-issue-to-pr-bot README를 고쳐줘", config))
        self.assertTrue(should_run_for_mention("please @incle-issue-to-pr-bot, run this", config))
        self.assertFalse(should_run_for_mention("@someone-else run", config))

    def test_parse_bot_command_supports_run_and_plan(self) -> None:
        run_command = parse_bot_command("/bot run effort=high", BotConfig())
        plan_command = parse_bot_command("/bot plan README 수정 계획", BotConfig())

        self.assertIsNotNone(run_command)
        self.assertIsNotNone(plan_command)
        assert run_command is not None
        assert plan_command is not None
        self.assertEqual(run_command.action, "run")
        self.assertEqual(run_command.options["effort"], "high")
        self.assertEqual(plan_command.action, "plan")
        self.assertEqual(plan_command.instruction, "README 수정 계획")

    def test_parse_bot_command_supports_mention_plan(self) -> None:
        command = parse_bot_command("@incle-issue-to-pr-bot plan README 수정", BotConfig())

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.action, "plan")
        self.assertEqual(command.trigger, "@incle-issue-to-pr-bot")
        self.assertEqual(command.instruction, "README 수정")

    def test_build_issue_request_handles_missing_values(self) -> None:
        request = build_issue_request({})

        self.assertEqual(request.repository, "unknown/unknown")
        self.assertEqual(request.issue_number, 0)
        self.assertEqual(request.issue_title, "")
        self.assertEqual(request.issue_body, "")
        self.assertEqual(request.comment_body, "")
        self.assertEqual(request.comment_author, "unknown")
        self.assertEqual(request.comment_id, 0)

    def test_build_issue_request_reads_event_payload(self) -> None:
        request = build_issue_request(
            {
                "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
                "issue": {
                    "number": 12,
                    "title": "테스트 기능 추가",
                    "body": "요구사항",
                },
                "comment": {
                    "body": "/bot run",
                    "id": 34,
                    "user": {"login": "IncleRepo"},
                },
            }
        )

        self.assertEqual(request.repository, "IncleRepo/issue-to-pr-bot")
        self.assertEqual(request.issue_number, 12)
        self.assertEqual(request.issue_title, "테스트 기능 추가")
        self.assertEqual(request.issue_body, "요구사항")
        self.assertEqual(request.comment_body, "/bot run")
        self.assertEqual(request.comment_author, "IncleRepo")
        self.assertEqual(request.comment_id, 34)

    def test_build_branch_name_is_stable(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title="Add GitHub PR flow!",
            issue_body="",
            comment_body="/bot run",
            comment_author="IncleRepo",
            comment_id=34,
        )

        self.assertEqual(build_branch_name(request), "bot/issue-12-comment-34-add-github-pr-flow")

    def test_build_branch_name_uses_configured_prefix(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title="Add GitHub PR flow!",
            issue_body="",
            comment_body="/bot run",
            comment_author="IncleRepo",
            comment_id=34,
        )

        config = BotConfig(branch_prefix="agent")

        self.assertEqual(build_branch_name(request, config), "agent/issue-12-comment-34-add-github-pr-flow")


if __name__ == "__main__":
    unittest.main()

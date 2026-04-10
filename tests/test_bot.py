import unittest

from app.bot import IssueRequest, build_branch_name, build_issue_request, should_run_bot, should_run_for_mention
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

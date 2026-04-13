import tempfile
import unittest
from pathlib import Path

from app.bot import IssueRequest
from app.config import BotConfig
from app.verification import VerificationError, run_verification
from app.verification_policy import build_verification_plan


class VerificationTest(unittest.TestCase):
    def test_run_verification_runs_all_configured_commands(self) -> None:
        config = BotConfig(
            check_commands=[
                'python -c "print(\'first\')"',
                'python -c "print(\'second\')"',
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_verification(config, Path(temp_dir))

        self.assertEqual([result.command for result in results], config.check_commands)
        self.assertIn("first", results[0].output)
        self.assertIn("second", results[1].output)

    def test_run_verification_raises_on_failure(self) -> None:
        config = BotConfig(
            check_commands=[
                'python -c "import sys; print(\'bad\'); sys.exit(3)"',
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(VerificationError) as context:
                run_verification(config, Path(temp_dir))

        self.assertEqual(context.exception.returncode, 3)
        self.assertIn("bad", context.exception.output)

    def test_build_verification_plan_uses_lightweight_checks_for_static_html_changes(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=9,
            issue_title="html hello world 구현",
            issue_body="폴더 아무거나 하나 파고 html hello world 구현해줘",
            comment_body="@incle-issue-to-pr-bot 이거 구현해주라",
            comment_author="IncleRepo",
            comment_id=1,
        )

        plan = build_verification_plan(
            [
                "python -m compileall -q app tests",
                "python -m unittest discover -s tests",
                "python -m venv .venv",
            ],
            ["examples/hello-world/index.html"],
            request,
        )

        self.assertEqual(plan.profile, "frontend_static")
        self.assertEqual(plan.commands, ["python -m compileall -q app tests"])

    def test_build_verification_plan_skips_verification_for_docs_only_changes(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=10,
            issue_title="README 정리",
            issue_body="문서 문구를 정리해줘",
            comment_body="@incle-issue-to-pr-bot README 문구만 다듬어줘",
            comment_author="IncleRepo",
            comment_id=2,
        )

        plan = build_verification_plan(
            [
                "python -m compileall -q app tests",
                "python -m unittest discover -s tests",
            ],
            ["README.md"],
            request,
        )

        self.assertEqual(plan.profile, "docs_only")
        self.assertEqual(plan.commands, [])


if __name__ == "__main__":
    unittest.main()

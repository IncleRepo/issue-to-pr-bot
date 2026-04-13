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
            comment_body="/bot run effort=high",
            comment_author="IncleRepo",
            comment_id=1,
        )
        command = BotCommand("run", "/bot run", "effort=high", {"effort": "high"})

        message = format_failure_next_steps(
            request,
            BotConfig(),
            command,
            VerificationError("python -m unittest", "bad", 1),
        )

        self.assertIn("/bot run effort=high", message)
        self.assertIn("/bot status", message)


if __name__ == "__main__":
    unittest.main()

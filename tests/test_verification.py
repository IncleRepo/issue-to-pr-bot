import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from app.bot import IssueRequest
from app.config import BotConfig
from app.verification import (
    VerificationError,
    collect_workspace_changes,
    resolve_verification_command,
    run_verification,
)
from app.verification_policy import build_verification_plan


class VerificationTest(unittest.TestCase):
    @patch("app.verification.subprocess.run")
    def test_run_verification_uses_utf8_replace(self, subprocess_run_mock) -> None:
        subprocess_run_mock.return_value = subprocess.CompletedProcess(
            args=["python"],
            returncode=0,
            stdout="ok\n",
        )
        config = BotConfig(check_commands=['python -c "print(\'ok\')"'])

        with tempfile.TemporaryDirectory() as temp_dir:
            run_verification(config, Path(temp_dir))

        _, kwargs = subprocess_run_mock.call_args
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    @patch("app.verification.subprocess.run")
    def test_collect_workspace_changes_uses_utf8_replace(self, subprocess_run_mock) -> None:
        subprocess_run_mock.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="?? .runtime-output/issue-1/pr-body.md\n?? Microsoft/\n M README.md\n",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            changed_files = collect_workspace_changes(Path(temp_dir))

        self.assertEqual(changed_files, ["README.md"])
        _, kwargs = subprocess_run_mock.call_args
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

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

    def test_resolve_verification_command_uses_full_windows_wrapper_path(self) -> None:
        with (
            patch("app.verification.os.name", "nt"),
            patch("app.verification.shutil.which", side_effect=lambda value: "C:\\Program Files\\nodejs\\npm.CMD" if value == "npm" else None),
        ):
            resolved = resolve_verification_command(["npm", "run", "lint"])

        self.assertEqual(resolved, ["C:\\Program Files\\nodejs\\npm.CMD", "run", "lint"])

    def test_run_verification_surfaces_missing_executable_as_verification_error(self) -> None:
        config = BotConfig(check_commands=["missing-tool --check"])

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("app.verification.subprocess.run", side_effect=FileNotFoundError("missing-tool")),
        ):
            with self.assertRaises(VerificationError) as context:
                run_verification(config, Path(temp_dir))

        self.assertEqual(context.exception.returncode, 127)
        self.assertIn("missing-tool", context.exception.output)

    def test_build_verification_plan_skips_irrelevant_python_checks_for_static_html_changes(self) -> None:
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
        self.assertEqual(plan.commands, [])

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

    def test_build_verification_plan_selects_frontend_commands_for_frontend_tooling_changes(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=11,
            issue_title="프론트 빌드 설정 수정",
            issue_body="vite 설정과 package.json을 정리해줘",
            comment_body="@incle-issue-to-pr-bot 프론트 설정을 정리해줘",
            comment_author="IncleRepo",
            comment_id=3,
        )

        plan = build_verification_plan(
            [
                "python -m compileall -q app tests",
                "npm run build",
                "npm run lint",
            ],
            ["package.json", "vite.config.ts"],
            request,
        )

        self.assertEqual(plan.profile, "frontend_app")
        self.assertEqual(plan.commands, ["npm run build", "npm run lint"])

    def test_build_verification_plan_skips_irrelevant_checks_for_config_only_changes(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title="설정 파일 정리",
            issue_body="yaml 설정만 정리해줘",
            comment_body="@incle-issue-to-pr-bot 설정만 정리해줘",
            comment_author="IncleRepo",
            comment_id=4,
        )

        plan = build_verification_plan(
            [
                "python -m compileall -q app tests",
                "python -m unittest discover -s tests",
                "yamllint .",
            ],
            ["config/app.yml"],
            request,
        )

        self.assertEqual(plan.profile, "config_only")
        self.assertEqual(plan.commands, ["yamllint ."])


if __name__ == "__main__":
    unittest.main()

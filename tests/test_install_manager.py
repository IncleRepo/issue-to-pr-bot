import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.install_manager import (
    DEFAULT_ENGINE_REF,
    DEFAULT_ENGINE_REPOSITORY,
    DoctorOptions,
    GithubConfigurationOptions,
    InstallManagerOptions,
    bootstrap_repository_environment,
    configure_repository_settings,
    install_repository_environment,
    main,
    render_workflow_template,
    run_doctor,
)


class InstallManagerTest(unittest.TestCase):
    def test_render_workflow_template_injects_centralized_values(self) -> None:
        template = "\n".join(
            [
                "jobs:",
                "  run-bot:",
                "    uses: IncleRepo/issue-to-pr-bot/.github/workflows/reusable-bot.yml@main",
                "    with:",
                '      runner_labels_json: \'["self-hosted","Windows"]\'',
                '      engine_repository: "IncleRepo/issue-to-pr-bot"',
                '      engine_ref: "main"',
            ]
        )

        rendered = render_workflow_template(
            template,
            engine_repository="Acme/bot-engine",
            engine_ref="release",
            runner_labels=["self-hosted", "linux", "x64"],
        )

        self.assertIn("uses: Acme/bot-engine/.github/workflows/reusable-bot.yml@release", rendered)
        self.assertIn('runner_labels_json: \'["self-hosted","linux","x64"]\'', rendered)
        self.assertIn('engine_repository: "Acme/bot-engine"', rendered)
        self.assertIn('engine_ref: "release"', rendered)

    def test_install_repository_environment_writes_minimal_workflow_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            result = install_repository_environment(
                InstallManagerOptions(
                    target=target,
                    engine_repository="Acme/bot-engine",
                    engine_ref="release",
                    runner_labels=["self-hosted", "linux"],
                    include_review_workflows=False,
                    write_config=True,
                )
            )

            issue_workflow = target / ".github/workflows/issue-comment.yml"
            review_workflow = target / ".github/workflows/pull-request-review.yml"
            config_file = target / ".issue-to-pr-bot.yml"

            self.assertTrue(issue_workflow.exists())
            self.assertFalse(review_workflow.exists())
            self.assertTrue(config_file.exists())
            self.assertIn("Acme/bot-engine", issue_workflow.read_text(encoding="utf-8"))
            self.assertIn('runner_labels_json: \'["self-hosted","linux"]\'', issue_workflow.read_text(encoding="utf-8"))
            self.assertEqual(
                [operation.action for operation in result.operations],
                ["created", "created"],
            )

    def test_install_repository_environment_skips_existing_files_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            workflow_path = target / ".github/workflows/issue-comment.yml"
            workflow_path.parent.mkdir(parents=True, exist_ok=True)
            workflow_path.write_text("manual change\n", encoding="utf-8")

            result = install_repository_environment(
                InstallManagerOptions(
                    target=target,
                    include_review_workflows=False,
                )
            )

            self.assertEqual(workflow_path.read_text(encoding="utf-8"), "manual change\n")
            self.assertEqual(result.operations[0].action, "skipped")

    def test_install_repository_environment_overwrites_existing_files_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            workflow_path = target / ".github/workflows/issue-comment.yml"
            workflow_path.parent.mkdir(parents=True, exist_ok=True)
            workflow_path.write_text("manual change\n", encoding="utf-8")

            result = install_repository_environment(
                InstallManagerOptions(
                    target=target,
                    force=True,
                    include_review_workflows=False,
                )
            )

            self.assertIn(DEFAULT_ENGINE_REPOSITORY, workflow_path.read_text(encoding="utf-8"))
            self.assertEqual(result.operations[0].action, "overwritten")

    def test_main_supports_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            exit_code = main(
                [
                    "init",
                    "--target",
                    str(target),
                    "--engine-repository",
                    DEFAULT_ENGINE_REPOSITORY,
                    "--engine-ref",
                    DEFAULT_ENGINE_REF,
                    "--skip-review-workflows",
                    "--dry-run",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertFalse((target / ".github/workflows/issue-comment.yml").exists())

    @patch("app.install_manager.shutil.which")
    @patch("app.install_manager.run_command")
    @patch("app.install_manager.resolve_codex_home")
    @patch("app.install_manager.detect_runner_root")
    def test_doctor_reports_machine_and_repository_readiness(
        self,
        mock_detect_runner_root,
        mock_resolve_codex_home,
        mock_run_command,
        mock_which,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as codex_dir:
            target = Path(temp_dir)
            (target / ".github/workflows").mkdir(parents=True, exist_ok=True)
            (target / ".github/workflows/issue-comment.yml").write_text("workflow\n", encoding="utf-8")
            runner_root = target / "actions-runner"
            runner_root.mkdir()
            (runner_root / "run.cmd").write_text("@echo off\n", encoding="utf-8")
            codex_home = Path(codex_dir)
            (codex_home / "auth.json").write_text("{}", encoding="utf-8")
            (codex_home / "config.toml").write_text("mode='chatgpt'\n", encoding="utf-8")

            mock_detect_runner_root.return_value = runner_root
            mock_resolve_codex_home.return_value = codex_home
            mock_which.side_effect = lambda name: f"C:/{name}.exe"
            mock_run_command.side_effect = [
                _completed(stdout="Python 3.11.0\n"),
                _completed(stdout="git version 2.47.0.windows.1\n"),
                _completed(stdout="Docker version 27.0.0\n"),
                _completed(stdout="27.0.0\n"),
                _completed(stdout="codex-cli 0.118.0\n"),
                _completed(stdout="gh version 2.0.0\n"),
                _completed(stdout=json.dumps([{"name": "BOT_MENTION"}, {"name": "BOT_APP_ID"}])),
                _completed(stdout=json.dumps([{"name": "BOT_APP_PRIVATE_KEY"}])),
            ]

            result = run_doctor(
                DoctorOptions(
                    target=target,
                    repository="Acme/example",
                )
            )

        status_by_name = {check.name: check.status for check in result.checks}
        self.assertEqual(status_by_name["Python"], "pass")
        self.assertEqual(status_by_name["Docker daemon"], "pass")
        self.assertEqual(status_by_name["Codex auth"], "pass")
        self.assertEqual(status_by_name["Issue workflow"], "pass")
        self.assertEqual(status_by_name["Repository variables"], "pass")
        self.assertEqual(status_by_name["Repository secrets"], "pass")

    @patch("app.install_manager.shutil.which", return_value="C:/gh.exe")
    @patch("app.install_manager.run_command")
    def test_configure_repository_settings_updates_variables_and_secret(self, mock_run_command, _mock_which) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_file = Path(temp_dir) / "app.pem"
            key_file.write_text("PRIVATE KEY\n", encoding="utf-8")

            mock_run_command.side_effect = [
                _completed(),
                _completed(),
                _completed(),
            ]

            result = configure_repository_settings(
                GithubConfigurationOptions(
                    repository="Acme/example",
                    bot_mention="@acme-bot",
                    bot_app_id="12345",
                    bot_app_private_key_file=key_file,
                )
            )

        self.assertEqual([operation.action for operation in result.operations], ["set", "set", "set"])
        self.assertEqual(mock_run_command.call_args_list[0].args[0][:4], ["gh", "variable", "set", "BOT_MENTION"])
        self.assertEqual(mock_run_command.call_args_list[1].args[0][:4], ["gh", "variable", "set", "BOT_APP_ID"])
        self.assertEqual(mock_run_command.call_args_list[2].args[0][:4], ["gh", "secret", "set", "BOT_APP_PRIVATE_KEY"])
        self.assertEqual(mock_run_command.call_args_list[2].kwargs["input_text"], "PRIVATE KEY\n")

    @patch("app.install_manager.run_doctor")
    @patch("app.install_manager.configure_repository_settings")
    @patch("app.install_manager.install_repository_environment")
    def test_bootstrap_combines_install_github_configuration_and_doctor(
        self,
        mock_install,
        mock_configure,
        mock_doctor,
    ) -> None:
        mock_install.return_value = install_repository_environment(
            InstallManagerOptions(target=Path(tempfile.gettempdir()), include_review_workflows=False, dry_run=True)
        )
        mock_configure.return_value = configure_repository_settings_for_test()
        mock_doctor.return_value = run_doctor_for_test()

        result = bootstrap_repository_environment(
            InstallManagerOptions(target=Path(tempfile.gettempdir()), include_review_workflows=False, dry_run=True),
            DoctorOptions(target=Path(tempfile.gettempdir())),
            GithubConfigurationOptions(
                repository="Acme/example",
                bot_mention="@acme-bot",
                bot_app_id="12345",
                bot_app_private_key_file=Path(tempfile.gettempdir()) / "missing.pem",
                dry_run=True,
            ),
        )

        self.assertIsNotNone(result.github_result)
        self.assertEqual(result.doctor_result.checks[0].status, "pass")


def _completed(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def configure_repository_settings_for_test():
    from app.install_manager import GithubConfigurationOperation, GithubConfigurationResult

    return GithubConfigurationResult(
        repository="Acme/example",
        operations=[GithubConfigurationOperation(name="BOT_MENTION", action="would_set")],
        next_steps=[],
    )


def run_doctor_for_test():
    from app.install_manager import DoctorCheck, DoctorResult

    return DoctorResult(checks=[DoctorCheck(name="Python", status="pass", detail="Python 3.11.0")])


if __name__ == "__main__":
    unittest.main()

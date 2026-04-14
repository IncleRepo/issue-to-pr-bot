import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.install_manager import (
    AgentBootstrapOptions,
    ControlPlaneOptions,
    DoctorOptions,
    TargetRepositoryOptions,
    auto_install_command_if_missing,
    bootstrap_agent_environment,
    init_control_plane_environment,
    init_target_repository,
    main,
    probe_command,
    run_doctor,
)


class InstallManagerTest(unittest.TestCase):
    def test_init_control_plane_environment_writes_worker_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)

            result = init_control_plane_environment(
                ControlPlaneOptions(
                    target=target,
                    worker_name="issue-to-pr-bot-control",
                    bot_mention="@issue-to-pr-bot",
                )
            )

            self.assertTrue((target / "package.json").exists())
            self.assertTrue((target / "wrangler.jsonc").exists())
            self.assertTrue((target / "src/index.js").exists())
            self.assertIn("issue-to-pr-bot-control", (target / "package.json").read_text(encoding="utf-8"))
            self.assertIn("@issue-to-pr-bot", (target / "wrangler.jsonc").read_text(encoding="utf-8"))
            self.assertEqual([operation.action for operation in result.operations], ["created", "created", "created", "created"])

    def test_bootstrap_agent_environment_writes_local_agent_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "agent/agent-config.json"
            workspace_root = temp_path / "workspaces"

            result = bootstrap_agent_environment(
                AgentBootstrapOptions(
                    control_plane_url="https://example.workers.dev",
                    agent_token="token-123",
                    repositories=["Acme/repo-a", "Acme/repo-b"],
                    workspace_root=workspace_root,
                    config_path=config_path,
                )
            )

            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config_data["control_plane_url"], "https://example.workers.dev")
            self.assertEqual(config_data["agent_token"], "token-123")
            self.assertEqual(config_data["repositories"], ["Acme/repo-a", "Acme/repo-b"])
            self.assertEqual(config_data["workspace_root"], str(workspace_root))
            self.assertEqual(result.operations[0].action, "created")

    def test_init_target_repository_writes_minimal_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)

            result = init_target_repository(TargetRepositoryOptions(target=target))

            self.assertTrue((target / ".issue-to-pr-bot.yml").exists())
            self.assertTrue((target / "AGENTS.md").exists())
            self.assertEqual([operation.action for operation in result.operations], ["created", "created"])

    def test_main_supports_bootstrap_agent_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "agent/agent-config.json"
            exit_code = main(
                [
                    "bootstrap-agent",
                    "--control-plane-url",
                    "https://example.workers.dev",
                    "--agent-token",
                    "token-123",
                    "--repository",
                    "Acme/repo-a",
                    "--workspace-root",
                    str(temp_path / "workspaces"),
                    "--config-path",
                    str(config_path),
                    "--dry-run",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertFalse(config_path.exists())

    def test_main_supports_init_target_repo_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            exit_code = main(
                [
                    "init-target-repo",
                    "--target",
                    str(target),
                    "--dry-run",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertFalse((target / ".issue-to-pr-bot.yml").exists())
            self.assertFalse((target / "AGENTS.md").exists())

    @patch("app.install_manager.shutil.which")
    @patch("app.install_manager.run_command")
    @patch("app.install_manager.resolve_codex_home")
    def test_doctor_reports_local_agent_readiness(
        self,
        mock_resolve_codex_home,
        mock_run_command,
        mock_which,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as codex_dir:
            target = Path(temp_dir)
            (target / "AGENTS.md").write_text("guide\n", encoding="utf-8")
            (target / ".issue-to-pr-bot.yml").write_text("bot:\n  output_dir: bot-output\n", encoding="utf-8")
            config_path = target / "agent-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "control_plane_url": "https://example.workers.dev",
                        "agent_token": "token-123",
                        "repositories": ["Acme/example"],
                        "workspace_root": str(target / "workspaces"),
                    }
                ),
                encoding="utf-8",
            )

            codex_home = Path(codex_dir)
            (codex_home / "auth.json").write_text("{}", encoding="utf-8")
            (codex_home / "config.toml").write_text("mode='chatgpt'\n", encoding="utf-8")
            mock_resolve_codex_home.return_value = codex_home
            mock_which.side_effect = lambda name: f"C:/{name}.exe"
            mock_run_command.side_effect = [
                _completed(stdout="Python 3.11.0\n"),
                _completed(stdout="git version 2.47.0.windows.1\n"),
                _completed(stdout="gh version 2.0.0\n"),
                _completed(stdout="codex-cli 0.118.0\n"),
            ]

            result = run_doctor(
                DoctorOptions(
                    target=target,
                    workspace_root=target / "workspaces",
                    control_plane_url="https://example.workers.dev",
                    config_path=config_path,
                )
            )

        status_by_name = {check.name: check.status for check in result.checks}
        self.assertEqual(status_by_name["Python"], "pass")
        self.assertEqual(status_by_name["Git"], "pass")
        self.assertEqual(status_by_name["Codex CLI"], "pass")
        self.assertEqual(status_by_name["Codex auth"], "pass")
        self.assertEqual(status_by_name["Control plane URL"], "pass")
        self.assertEqual(status_by_name["Agent config"], "pass")

    @patch("app.install_manager.refresh_process_path")
    @patch("app.install_manager.run_command")
    @patch("app.install_manager.detect_package_manager", return_value="winget")
    @patch("app.install_manager.shutil.which")
    def test_auto_install_command_if_missing_uses_winget(
        self,
        mock_which,
        _mock_detect_package_manager,
        mock_run_command,
        _mock_refresh_process_path,
    ) -> None:
        mock_which.side_effect = [None, "C:/Program Files/GitHub CLI/gh.exe"]
        mock_run_command.return_value = _completed()

        installed = auto_install_command_if_missing("gh")

        self.assertTrue(installed)
        self.assertEqual(
            mock_run_command.call_args.args[0][:4],
            ["winget", "install", "--id", "GitHub.cli"],
        )

    @patch("app.install_manager.run_command")
    @patch("app.install_manager.shutil.which", return_value=None)
    def test_probe_command_fails_when_binary_is_missing(self, _mock_which, _mock_run_command) -> None:
        result = probe_command("Git", "git", ["--version"])
        self.assertEqual(result.status, "fail")


def _completed(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    unittest.main()

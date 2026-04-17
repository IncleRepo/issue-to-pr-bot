import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.install_manager import (
    AgentBootstrapOptions,
    BootstrapAllOptions,
    ControlPlaneBootstrapOptions,
    ControlPlaneOptions,
    DoctorOptions,
    TargetRepositoryOptions,
    auto_install_command_if_missing,
    bootstrap_all_environment,
    bootstrap_agent_environment,
    bootstrap_control_plane_environment,
    build_agent_launch_command,
    init_control_plane_environment,
    init_target_repository,
    main,
    probe_command,
    register_agent_scheduled_task,
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
                    agent_repositories=["Acme/repo-a", "Acme/repo-b"],
                    agent_poll_interval_seconds=25,
                )
            )

            self.assertTrue((target / "package.json").exists())
            self.assertTrue((target / "wrangler.jsonc").exists())
            self.assertTrue((target / "src/index.js").exists())
            self.assertIn("issue-to-pr-bot-control", (target / "package.json").read_text(encoding="utf-8"))
            self.assertIn("@issue-to-pr-bot", (target / "wrangler.jsonc").read_text(encoding="utf-8"))
            self.assertIn("Acme/repo-a,Acme/repo-b", (target / "wrangler.jsonc").read_text(encoding="utf-8"))
            self.assertIn('"AGENT_POLL_INTERVAL_SECONDS": 25', (target / "wrangler.jsonc").read_text(encoding="utf-8"))
            self.assertIn('"AGENT_MAX_CONCURRENCY": 2', (target / "wrangler.jsonc").read_text(encoding="utf-8"))
            self.assertEqual([operation.action for operation in result.operations], ["created", "created", "created", "created"])

    @patch("app.install_manager.ensure_command_available")
    @patch("app.install_manager.run_wrangler_secret_put")
    @patch("app.install_manager.run_checked_command")
    def test_bootstrap_control_plane_runs_deploy_steps(
        self,
        mock_run_checked_command,
        mock_run_wrangler_secret_put,
        _mock_ensure_command_available,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            pem_path = target / "app.pem"
            pem_path.write_text("PRIVATE KEY", encoding="utf-8")
            mock_run_checked_command.side_effect = [
                "npm install ok",
                "kv ok",
                "Deployed to https://issue-to-pr-bot-control.example.workers.dev",
            ]

            result = bootstrap_control_plane_environment(
                ControlPlaneBootstrapOptions(
                    target=target,
                    worker_name="issue-to-pr-bot-control",
                    bot_mention="@issue-to-pr-bot",
                    github_app_id="12345",
                    github_app_private_key_file=pem_path,
                    agent_repositories=["Acme/repo-a"],
                    agent_poll_interval_seconds=20,
                    agent_token="agent-token",
                    webhook_secret="webhook-secret",
                )
            )

            self.assertEqual(result.worker_url, "https://issue-to-pr-bot-control.example.workers.dev")
            self.assertEqual(result.agent_token, "agent-token")
            self.assertEqual(result.webhook_secret, "webhook-secret")
            self.assertEqual(mock_run_wrangler_secret_put.call_count, 4)

    @patch("app.install_manager.check_codex_auth")
    @patch("app.install_manager.ensure_command_available")
    @patch("app.install_manager.register_agent_scheduled_task", return_value="created")
    @patch(
        "app.install_manager.install_standalone_binary",
        return_value=(Path(r"C:\agent-bin\issue-to-pr-bot-agent.exe"), "created", "1.2.3"),
    )
    def test_bootstrap_agent_environment_writes_local_agent_config(
        self,
        _mock_install_runtime,
        _mock_register_task,
        _mock_ensure_command,
        mock_check_codex_auth,
    ) -> None:
        mock_check_codex_auth.return_value = type("DoctorCheckStub", (), {"status": "pass"})()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "agent/agent-config.json"
            workspace_root = temp_path / "workspaces"
            log_path = temp_path / "logs/agent.log"

            result = bootstrap_agent_environment(
                AgentBootstrapOptions(
                    control_plane_url="https://example.workers.dev",
                    agent_token="token-123",
                    repositories=["Acme/repo-a", "Acme/repo-b"],
                    workspace_root=workspace_root,
                    config_path=config_path,
                    log_path=log_path,
                )
            )

            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config_data["control_plane_url"], "https://example.workers.dev")
            self.assertEqual(config_data["agent_token"], "token-123")
            self.assertEqual(config_data["repositories"], ["Acme/repo-a", "Acme/repo-b"])
            self.assertEqual(config_data["workspace_root"], str(workspace_root))
            self.assertEqual(config_data["log_path"], str(log_path))
            self.assertEqual(config_data["managed_runtime_path"], r"C:\agent-bin\issue-to-pr-bot-agent.exe")
            self.assertEqual(config_data["release_repository"], "IncleRepo/issue-to-pr-bot")
            self.assertEqual(config_data["managed_runtime_version"], "1.2.3")
            self.assertEqual(config_data["max_concurrency"], 2)
            self.assertEqual(result.operations[0].action, "created")
            self.assertEqual(result.runtime_path, Path(r"C:\agent-bin\issue-to-pr-bot-agent.exe"))
            self.assertEqual(result.runtime_version, "1.2.3")
            self.assertEqual(result.task_name, "issue-to-pr-bot-agent")

    @patch("app.install_manager.resolve_agent_entrypoint", return_value=Path(r"C:\venv\Scripts\issue-to-pr-bot-agent.exe"))
    def test_build_agent_launch_command_prefers_agent_executable(self, _mock_entrypoint) -> None:
        command = build_agent_launch_command(Path(r"C:\agent\config.json"))
        self.assertIn(r'"C:\venv\Scripts\issue-to-pr-bot-agent.exe"', command)
        self.assertIn(r'--config "C:\agent\config.json"', command)

    @patch("app.install_manager.is_windows_platform", return_value=False)
    @patch("app.install_manager.sys.executable", "/usr/bin/python3")
    @patch("app.install_manager.resolve_agent_entrypoint", return_value=None)
    @patch("app.install_manager.Path.exists", return_value=False)
    def test_build_agent_launch_command_uses_python_on_posix(
        self,
        _mock_exists,
        _mock_resolve_agent_entrypoint,
        _mock_is_windows_platform,
    ) -> None:
        command = build_agent_launch_command(Path("/tmp/agent-config.json"))
        normalized = command.replace("\\", "/")
        self.assertIn('"/usr/bin/python3" -m app.agent_runner --config "/tmp/agent-config.json"', normalized)

    @patch("app.install_manager.bootstrap_control_plane_environment")
    @patch("app.install_manager.bootstrap_agent_environment")
    @patch("app.install_manager.init_target_repository")
    def test_bootstrap_all_combines_three_steps(
        self,
        mock_init_target_repository,
        mock_bootstrap_agent_environment,
        mock_bootstrap_control_plane_environment,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "repo").mkdir()
            mock_bootstrap_control_plane_environment.return_value = type(
                "ControlPlaneBootstrapResultStub",
                (),
                {
                    "worker_url": "https://issue-to-pr-bot-control.example.workers.dev",
                    "agent_token": "agent-token",
                    "webhook_secret": "webhook-secret",
                    "install_result": init_control_plane_environment(
                        ControlPlaneOptions(
                            target=temp_path / "control",
                            worker_name="issue-to-pr-bot-control",
                            bot_mention="@bot",
                            agent_repositories=[],
                            agent_poll_interval_seconds=10,
                            dry_run=True,
                        )
                    ),
                    "operations": [],
                },
            )()
            mock_bootstrap_agent_environment.return_value = bootstrap_agent_environment(
                AgentBootstrapOptions(
                    control_plane_url="https://issue-to-pr-bot-control.example.workers.dev",
                    agent_token="agent-token",
                    repositories=["Acme/repo"],
                    install_root=temp_path / "bin",
                    workspace_root=temp_path / "workspaces",
                    config_path=temp_path / "agent.json",
                    dry_run=True,
                )
            )
            mock_init_target_repository.return_value = init_target_repository(
                TargetRepositoryOptions(target=temp_path / "repo", dry_run=True)
            )

            result = bootstrap_all_environment(
                BootstrapAllOptions(
                    control_plane=ControlPlaneBootstrapOptions(
                        target=temp_path / "control",
                        worker_name="issue-to-pr-bot-control",
                        bot_mention="@bot",
                        github_app_id="12345",
                        github_app_private_key_file=temp_path / "app.pem",
                        agent_repositories=["Acme/repo"],
                        agent_poll_interval_seconds=12,
                        dry_run=True,
                    ),
                    agent=AgentBootstrapOptions(
                        control_plane_url="https://issue-to-pr-bot-control.example.workers.dev",
                        agent_token="",
                        repositories=["Acme/repo"],
                        install_root=temp_path / "bin",
                        workspace_root=temp_path / "workspaces",
                        config_path=temp_path / "agent.json",
                        dry_run=True,
                    ),
                    target_repository=TargetRepositoryOptions(target=temp_path / "repo", dry_run=True),
                )
            )

            self.assertEqual(result[0].agent_token, "agent-token")
            mock_bootstrap_control_plane_environment.assert_called_once()
            mock_bootstrap_agent_environment.assert_called_once()
            mock_init_target_repository.assert_called_once()

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

    @patch("app.install_manager.bootstrap_control_plane_environment")
    def test_main_supports_bootstrap_control_plane_dry_run(self, mock_bootstrap_control_plane_environment) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            pem_path = temp_path / "app.pem"
            pem_path.write_text("PRIVATE KEY", encoding="utf-8")
            mock_bootstrap_control_plane_environment.return_value = type(
                "ControlPlaneBootstrapResultStub",
                (),
                {
                    "install_result": init_control_plane_environment(
                        ControlPlaneOptions(
                            target=temp_path / "control",
                            worker_name="issue-to-pr-bot-control",
                            bot_mention="@bot",
                            agent_repositories=[],
                            agent_poll_interval_seconds=10,
                            dry_run=True,
                        )
                    ),
                    "operations": [],
                    "worker_url": "https://issue-to-pr-bot-control.example.workers.dev",
                    "agent_token": "agent-token",
                    "webhook_secret": "webhook-secret",
                },
            )()

            exit_code = main(
                [
                    "bootstrap-control-plane",
                    "--target",
                    str(temp_path / "control"),
                    "--worker-name",
                    "issue-to-pr-bot-control",
                    "--bot-mention",
                    "@bot",
                    "--github-app-id",
                    "12345",
                    "--github-app-private-key-file",
                    str(pem_path),
                    "--agent-repository",
                    "Acme/repo-a",
                    "--agent-poll-interval-seconds",
                    "25",
                    "--dry-run",
                ]
            )

            self.assertEqual(exit_code, 0)

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

    @patch("app.install_manager.refresh_process_path")
    @patch("app.install_manager.run_command")
    @patch("app.install_manager.detect_package_manager", return_value="apt")
    @patch("app.install_manager.shutil.which")
    def test_auto_install_command_if_missing_uses_apt(
        self,
        mock_which,
        _mock_detect_package_manager,
        mock_run_command,
        _mock_refresh_process_path,
    ) -> None:
        def which_side_effect(name: str):
            if name == "git":
                if not hasattr(which_side_effect, "called"):
                    which_side_effect.called = True
                    return None
                return "/usr/bin/git"
            if name == "sudo":
                return "/usr/bin/sudo"
            return None

        mock_which.side_effect = which_side_effect
        mock_run_command.return_value = _completed()

        installed = auto_install_command_if_missing("git")

        self.assertTrue(installed)
        self.assertEqual(
            mock_run_command.call_args.args[0],
            ["sudo", "apt-get", "install", "-y", "git"],
        )

    @patch("app.install_manager.is_posix_platform", return_value=True)
    @patch("app.install_manager.shutil.which", return_value="/usr/bin/systemctl")
    def test_register_agent_scheduled_task_returns_would_create_on_posix_dry_run(
        self,
        _mock_which,
        _mock_is_posix_platform,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            result = register_agent_scheduled_task(
                AgentBootstrapOptions(
                    control_plane_url="https://example.workers.dev",
                    agent_token="token-123",
                    repositories=["Acme/repo-a"],
                    workspace_root=temp_path / "workspaces",
                    config_path=temp_path / "agent/agent-config.json",
                    dry_run=True,
                )
            )

        self.assertEqual(result, "would_create")

    @patch("app.install_manager.run_command")
    @patch("app.install_manager.shutil.which", return_value=None)
    def test_probe_command_fails_when_binary_is_missing(self, _mock_which, _mock_run_command) -> None:
        result = probe_command("Git", "git", ["--version"])
        self.assertEqual(result.status, "fail")


def _completed(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    unittest.main()

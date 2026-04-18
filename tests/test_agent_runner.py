import json
import io
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_runner import (
    AgentConfig,
    CentralAgentConfig,
    ClaimedTask,
    TaskInterrupted,
    build_parser,
    cancel_running_task,
    clear_pid_file,
    collect_runtime_update_wait_pids,
    dispatch_console_command,
    extract_issue_number,
    extract_pull_request_number,
    fetch_central_agent_config,
    handle_console_logs_command,
    install_latest_agent_runtime,
    merge_agent_config,
    prepare_repository_workspace,
    read_running_pid,
    resolve_pid_path,
    resolve_requested_log_path,
    resolve_task_lock_key,
    run_task_subprocess,
    resolve_workspace_path,
    run_claimed_task,
    run_command,
    run_console_update,
    stream_task_logs,
    try_resolve_log_path,
    build_task_subprocess_command,
)
from app.agent.service import execute_task_in_workspace
from app.output_artifacts import (
    get_legacy_workspace_output_artifact_root,
    get_workspace_input_root,
    get_workspace_output_root,
)
from app.workspace_state import resolve_workspace_codex_home_root


class AgentRunnerTest(unittest.TestCase):
    @patch("app.agent.service.main", return_value=0)
    def test_agent_runner_module_executes_service_main(self, main_mock) -> None:
        with self.assertRaises(SystemExit) as context:
            runpy.run_module("app.agent_runner", run_name="__main__")
        self.assertEqual(context.exception.code, 0)
        main_mock.assert_called_once()

    def test_default_cli_runs_serve_mode_without_subcommand(self) -> None:
        parser = build_parser(include_internal=False)
        args = parser.parse_args([])
        self.assertEqual(args.command, "serve")

    def test_resolve_pid_path_uses_config_sibling(self) -> None:
        config_path = Path(r"C:\agent\agent-config.json")
        self.assertEqual(resolve_pid_path(config_path), Path(r"C:\agent\agent-config.pid"))

    def test_try_resolve_log_path_reads_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            config_path.write_text('{"log_path":"C:\\\\logs\\\\agent.log"}', encoding="utf-8")
            self.assertEqual(str(try_resolve_log_path(config_path)), r"C:\logs\agent.log")

    @patch("app.agent.service.urllib.request.urlopen")
    def test_fetch_central_agent_config_reads_remote_settings(self, mock_urlopen) -> None:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "repositories": ["Acme/repo-a", "Acme/repo-b"],
                "pollIntervalSeconds": 25,
                "maxConcurrency": 3,
            }
        ).encode("utf-8")
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            log_path=Path(r"C:\logs\agent.log"),
        )

        central = fetch_central_agent_config(config)

        self.assertEqual(central.repositories, ["Acme/repo-a", "Acme/repo-b"])
        self.assertEqual(central.poll_interval_seconds, 25)
        self.assertEqual(central.max_concurrency, 3)

    def test_merge_agent_config_prefers_central_values(self) -> None:
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            repositories=["Local/repo"],
            poll_interval_seconds=10,
            log_path=Path(r"C:\logs\agent.log"),
            max_concurrency=2,
        )

        merged = merge_agent_config(
            config,
            CentralAgentConfig(
                repositories=["Acme/repo-a"],
                poll_interval_seconds=30,
                max_concurrency=4,
            ),
        )

        self.assertEqual(merged.repositories, ["Acme/repo-a"])
        self.assertEqual(merged.poll_interval_seconds, 30)
        self.assertEqual(merged.max_concurrency, 4)

    def test_extract_pull_request_number_prefers_pull_request_payload(self) -> None:
        payload = {"pull_request": {"number": 12}, "issue": {"number": 99, "pull_request": {}}}
        self.assertEqual(extract_pull_request_number(payload), 12)

    def test_extract_issue_number_reads_issue_payload(self) -> None:
        payload = {"issue": {"number": 21}}
        self.assertEqual(extract_issue_number(payload), 21)

    def test_resolve_task_lock_key_uses_issue_or_pr_scope(self) -> None:
        issue_task = ClaimedTask(
            task_id="task-issue",
            event_name="issue_comment",
            delivery_id=None,
            repository="IncleRepo/example",
            default_branch="main",
            payload={"issue": {"number": 9}},
            github_token="token",
        )
        pr_task = ClaimedTask(
            task_id="task-pr",
            event_name="pull_request_review",
            delivery_id=None,
            repository="IncleRepo/example",
            default_branch="main",
            payload={"pull_request": {"number": 12}},
            github_token="token",
        )

        self.assertEqual(resolve_task_lock_key(issue_task), "IncleRepo/example:issue-9")
        self.assertEqual(resolve_task_lock_key(pr_task), "IncleRepo/example:pr-12")

    @patch("app.agent.service.is_process_running", return_value=False)
    def test_read_running_pid_cleans_stale_pid_file(self, _mock_running) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            config_path.write_text("{}", encoding="utf-8")
            pid_path = resolve_pid_path(config_path)
            pid_path.write_text("12345", encoding="utf-8")
            self.assertIsNone(read_running_pid(config_path))
            self.assertFalse(pid_path.exists())

    def test_clear_pid_file_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            config_path.write_text("{}", encoding="utf-8")
            pid_path = resolve_pid_path(config_path)
            pid_path.write_text("12345", encoding="utf-8")
            clear_pid_file(config_path)
            self.assertFalse(pid_path.exists())

    @patch("app.agent.service.report_task_completion")
    @patch("app.agent.service.execute_task_in_workspace")
    @patch("app.agent.service.prepare_repository_workspace")
    @patch("app.agent.service.log_message")
    def test_run_claimed_task_skips_deleted_issue_comment(self, mock_log, mock_prepare, mock_execute, mock_report) -> None:
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            log_path=Path(r"C:\logs\agent.log"),
        )
        task = ClaimedTask(
            task_id="task-1",
            event_name="issue_comment",
            delivery_id="delivery-1",
            repository="IncleRepo/example",
            default_branch="main",
            payload={"action": "deleted", "comment": {"id": 123}},
            github_token="token",
        )

        exit_code = run_claimed_task(config, task)

        self.assertEqual(exit_code, 0)
        mock_prepare.assert_not_called()
        mock_execute.assert_not_called()
        mock_report.assert_called_once_with(
            config,
            "task-1",
            "completed",
            "ignored",
            "Skipped unsupported webhook action: event=issue_comment, action=deleted",
        )
        self.assertTrue(any("건너뜀" in call.args[1] for call in mock_log.call_args_list))

    @patch("app.agent.service.report_task_completion")
    @patch("app.agent.service.execute_task_in_workspace")
    @patch("app.agent.service.prepare_repository_workspace", return_value=Path(r"C:\work\repo"))
    @patch("app.agent.service.log_message")
    def test_run_claimed_task_executes_created_issue_comment(self, _mock_log, mock_prepare, mock_execute, mock_report) -> None:
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            log_path=Path(r"C:\logs\agent.log"),
        )
        task = ClaimedTask(
            task_id="task-2",
            event_name="issue_comment",
            delivery_id="delivery-2",
            repository="IncleRepo/example",
            default_branch="main",
            payload={"action": "created", "comment": {"id": 456}},
            github_token="token",
        )

        exit_code = run_claimed_task(config, task)

        self.assertEqual(exit_code, 0)
        mock_prepare.assert_called_once_with(config, task, log_path=None)
        mock_execute.assert_called_once_with(config, Path(r"C:\work\repo"), task, log_path=None)
        mock_report.assert_called_once_with(config, "task-2", "completed", "completed", "")

    @patch("app.agent.service.report_task_completion")
    @patch("app.agent.service.execute_task_in_workspace", side_effect=TaskInterrupted("사용자가 중단했습니다."))
    @patch("app.agent.service.prepare_repository_workspace", return_value=Path(r"C:\work\repo"))
    @patch("app.agent.service.log_message")
    def test_run_claimed_task_marks_interrupted_task_as_completed(self, mock_log, mock_prepare, mock_execute, mock_report) -> None:
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            log_path=Path(r"C:\logs\agent.log"),
        )
        task = ClaimedTask(
            task_id="task-3",
            event_name="issue_comment",
            delivery_id="delivery-3",
            repository="IncleRepo/example",
            default_branch="main",
            payload={"action": "created", "comment": {"id": 789}},
            github_token="token",
        )

        exit_code = run_claimed_task(config, task)

        self.assertEqual(exit_code, 0)
        mock_prepare.assert_called_once_with(config, task, log_path=None)
        mock_execute.assert_called_once_with(config, Path(r"C:\work\repo"), task, log_path=None)
        mock_report.assert_called_once_with(config, "task-3", "completed", "interrupted", "사용자가 중단했습니다.")
        self.assertTrue(any("작업 중단" in call.args[1] for call in mock_log.call_args_list))

    @patch("app.agent.service.run_command")
    def test_prepare_repository_workspace_resets_and_cleans_before_checkout(self, mock_run_command) -> None:
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            log_path=Path(r"C:\logs\agent.log"),
        )
        task = ClaimedTask(
            task_id="task-4",
            event_name="issue_comment",
            delivery_id="delivery-4",
            repository="IncleRepo/example",
            default_branch="main",
            payload={"action": "created", "comment": {"id": 321}, "issue": {"number": 9}},
            github_token="token",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            repo_dir = workspace_root / "IncleRepo__example" / "issue-9"
            repo_dir.joinpath(".git", "info").mkdir(parents=True)
            output_dir = get_workspace_output_root(repo_dir)
            output_dir.mkdir(parents=True)
            (output_dir / "pr-body.md").write_text("draft", encoding="utf-8")
            legacy_output_dir = get_legacy_workspace_output_artifact_root(repo_dir)
            legacy_output_dir.mkdir(parents=True)
            (legacy_output_dir / "pr-body.md").write_text("legacy", encoding="utf-8")
            local_config = AgentConfig(
                control_plane_url=config.control_plane_url,
                agent_token=config.agent_token,
                workspace_root=workspace_root,
                log_path=config.log_path,
            )
            prepared = prepare_repository_workspace(local_config, task)
            self.assertEqual(prepared, repo_dir)
            self.assertFalse(output_dir.exists())
            self.assertFalse(legacy_output_dir.exists())
            exclude_text = repo_dir.joinpath(".git", "info", "exclude").read_text(encoding="utf-8")
            self.assertIn("/.issue-to-pr-bot/input/", exclude_text)
            self.assertIn("/.issue-to-pr-bot/output/", exclude_text)

        commands = [call.args[0] for call in mock_run_command.call_args_list]
        checkout_index = commands.index(["git", "-C", str(repo_dir), "checkout", "-B", "main", "FETCH_HEAD"])
        reset_index = commands.index(["git", "-C", str(repo_dir), "reset", "--hard"])
        clean_index = commands.index(["git", "-C", str(repo_dir), "clean", "-fd"])
        self.assertLess(reset_index, checkout_index)
        self.assertLess(clean_index, checkout_index)

    def test_resolve_workspace_path_separates_issue_scopes(self) -> None:
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            log_path=Path(r"C:\logs\agent.log"),
        )
        task = ClaimedTask(
            task_id="task-5",
            event_name="issue_comment",
            delivery_id=None,
            repository="IncleRepo/example",
            default_branch="main",
            payload={"issue": {"number": 7}},
            github_token="token",
        )
        normalized = str(resolve_workspace_path(config, task)).replace("\\", "/")
        self.assertEqual(normalized, "C:/work/IncleRepo__example/issue-7")

    @patch("app.agent.service.subprocess.run")
    def test_run_command_uses_utf8_replace(self, mock_run) -> None:
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0

        run_command(["git", "status"])

        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    @patch("app.agent.service.subprocess.run")
    def test_run_command_hides_windows_console(self, mock_run) -> None:
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0
        startupinfo = type("StartupInfo", (), {"dwFlags": 0, "wShowWindow": 1})()

        with patch("app.agent.service.os.name", "nt"), patch(
            "app.agent.service.subprocess.CREATE_NO_WINDOW",
            0x8000000,
            create=True,
        ), patch(
            "app.agent.service.subprocess.STARTF_USESHOWWINDOW",
            0x1,
            create=True,
        ), patch(
            "app.agent.service.subprocess.STARTUPINFO",
            return_value=startupinfo,
            create=True,
        ):
            run_command(["git", "status"])

        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs["creationflags"], 0x8000000)
        self.assertIs(kwargs["startupinfo"], startupinfo)
        self.assertEqual(startupinfo.dwFlags, 0x1)
        self.assertEqual(startupinfo.wShowWindow, 0)

    @patch("app.agent.service.resolve_task_python", return_value=Path(r"C:\Python\python.exe"))
    @patch("app.agent.service.subprocess.Popen")
    def test_run_task_subprocess_detaches_console_on_windows(self, mock_popen, _mock_python) -> None:
        process = mock_popen.return_value
        process.pid = 321
        process.wait.return_value = 0
        startupinfo = type("StartupInfo", (), {"dwFlags": 0, "wShowWindow": 1})()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            task_file = Path(temp_dir) / "task.json"
            log_path = Path(temp_dir) / "task.log"
            pid_file = Path(temp_dir) / "task.pid"
            config_path.write_text("{}", encoding="utf-8")
            task_file.write_text("{}", encoding="utf-8")
            with patch("app.agent.service.os.name", "nt"), patch(
                "app.agent.service.subprocess.CREATE_NEW_PROCESS_GROUP",
                0x200,
                create=True,
            ), patch(
                "app.agent.service.subprocess.DETACHED_PROCESS",
                0x8,
                create=True,
            ), patch(
                "app.agent.service.subprocess.CREATE_NO_WINDOW",
                0x8000000,
                create=True,
            ), patch(
                "app.agent.service.subprocess.STARTF_USESHOWWINDOW",
                0x1,
                create=True,
            ), patch(
                "app.agent.service.subprocess.STARTUPINFO",
                return_value=startupinfo,
                create=True,
            ):
                exit_code = run_task_subprocess(config_path, task_file, log_path, pid_file)
                written_pid = pid_file.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        _args, kwargs = mock_popen.call_args
        self.assertEqual(kwargs["creationflags"], 0x8000208)
        self.assertIs(kwargs["startupinfo"], startupinfo)
        self.assertEqual(startupinfo.dwFlags, 0x1)
        self.assertEqual(startupinfo.wShowWindow, 0)
        self.assertEqual(written_pid, "321")

    @patch("app.agent.service.sys.frozen", True, create=True)
    @patch("app.agent.service.sys.executable", r"C:\agent\issue-to-pr-bot-agent.exe")
    def test_build_task_subprocess_command_uses_embedded_agent_binary_when_frozen(self) -> None:
        command = build_task_subprocess_command(
            Path(r"C:\agent\agent-config.json"),
            Path(r"C:\agent\task.json"),
            Path(r"C:\agent\task.log"),
        )

        self.assertEqual(
            command,
            [
                r"C:\agent\issue-to-pr-bot-agent.exe",
                "run-task",
                "--config",
                r"C:\agent\agent-config.json",
                "--task-file",
                r"C:\agent\task.json",
                "--log-path",
                r"C:\agent\task.log",
            ],
        )

    @patch("app.agent.service.print_running_tasks")
    def test_dispatch_console_command_supports_ps(self, mock_ps) -> None:
        keep_running = dispatch_console_command(Path(r"C:\agent\agent-config.json"), "ps")
        self.assertTrue(keep_running)
        mock_ps.assert_called_once()

    @patch("app.agent.service.get_running_entries", return_value=[{"task_id": "task-1"}])
    def test_dispatch_console_command_quit_warns_when_tasks_running(self, mock_running) -> None:
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            keep_running = dispatch_console_command(Path(r"C:\agent\agent-config.json"), "quit")
        self.assertTrue(keep_running)
        self.assertIn("quit now", stdout.getvalue())
        mock_running.assert_called_once()

    @patch("app.agent.service.get_running_entries", return_value=[{"task_id": "task-1"}])
    def test_dispatch_console_command_quit_now_exits_even_when_tasks_running(self, mock_running) -> None:
        keep_running = dispatch_console_command(Path(r"C:\agent\agent-config.json"), "quit now")
        self.assertFalse(keep_running)
        mock_running.assert_called_once()

    @patch("app.agent.service.stop_all_running_tasks")
    def test_dispatch_console_command_supports_stop_all(self, mock_stop_all) -> None:
        keep_running = dispatch_console_command(Path(r"C:\agent\agent-config.json"), "stop all")
        self.assertTrue(keep_running)
        mock_stop_all.assert_called_once_with(Path(r"C:\agent\agent-config.json"))

    @patch("app.agent.service.run_console_update", return_value=True)
    def test_dispatch_console_command_supports_update(self, mock_update) -> None:
        keep_running = dispatch_console_command(Path(r"C:\agent\agent-config.json"), "update")
        self.assertTrue(keep_running)
        mock_update.assert_called_once_with(Path(r"C:\agent\agent-config.json"))

    @patch("app.agent.service.log_message")
    @patch("app.agent.service.report_task_completion")
    @patch("app.agent.service.terminate_process_tree")
    def test_cancel_running_task_accepts_prefix_task_id(self, mock_terminate, mock_report, mock_log) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            state_path = config_path.with_suffix(".state.json")
            config_path.write_text(
                json.dumps(
                    {
                        "control_plane_url": "https://example.com",
                        "agent_token": "token",
                        "workspace_root": r"C:\work",
                        "log_path": r"C:\logs\agent.log",
                    }
                ),
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps(
                    {
                        "running": [
                            {
                                "task_id": "b6a18c8d-9a6d-4e00-9c49-acde11111111",
                                "pid": 1748,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = cancel_running_task(config_path, "b6a18c8d-9a6")

        self.assertEqual(result, 0)
        mock_terminate.assert_called_once_with(1748)
        mock_report.assert_called_once_with(
            unittest.mock.ANY,
            "b6a18c8d-9a6d-4e00-9c49-acde11111111",
            "completed",
            "cancelled",
            "사용자 요청으로 task를 취소했습니다.",
        )
        self.assertTrue(any("task를 취소했습니다." in call.args[1] for call in mock_log.call_args_list))

    @patch("app.agent.service.stream_task_logs")
    def test_handle_console_logs_command_supports_latest_follow(self, mock_logs) -> None:
        keep_running = handle_console_logs_command(Path(r"C:\agent\agent-config.json"), ["logs", "latest", "-f"])
        self.assertTrue(keep_running)
        mock_logs.assert_called_once_with(Path(r"C:\agent\agent-config.json"), task_id=None, latest=True, follow=True)

    @patch("app.agent.service.should_stop_log_stream")
    def test_stream_task_logs_follow_stops_on_q_request(self, mock_stop) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            tasks_root = config_path.parent / "tasks"
            tasks_root.mkdir(parents=True)
            log_path = tasks_root / "task-1.log"
            log_path.write_text("line-1\n", encoding="utf-8")
            mock_stop.side_effect = [False, True]

            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                result = stream_task_logs(config_path, task_id="task-1", latest=False, follow=True)

        self.assertEqual(result, 0)
        self.assertIn("로그 스트리밍을 종료하고 agent 프롬프트로 돌아갑니다.", stdout.getvalue())

    @patch("app.agent.service.should_stop_log_stream")
    def test_stream_task_logs_follow_ignores_ctrl_c_until_q_request(self, mock_stop) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            tasks_root = config_path.parent / "tasks"
            tasks_root.mkdir(parents=True)
            log_path = tasks_root / "task-1.log"
            log_path.write_text("line-1\n", encoding="utf-8")
            mock_stop.side_effect = [KeyboardInterrupt(), True]

            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                result = stream_task_logs(config_path, task_id="task-1", latest=False, follow=True)

        self.assertEqual(result, 0)
        self.assertIn("Ctrl+C는 비활성화되어 있습니다. 로그 스트리밍 종료는 q를 사용하세요.", stdout.getvalue())
        self.assertIn("로그 스트리밍을 종료하고 agent 프롬프트로 돌아갑니다.", stdout.getvalue())

    @patch("app.agent.service.install_standalone_binary")
    def test_run_console_update_reports_latest_version(self, mock_install) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            runtime_path = Path(temp_dir) / "bin" / "issue-to-pr-bot-agent.exe"
            runtime_path.parent.mkdir(parents=True)
            runtime_path.write_text("binary", encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "control_plane_url": "https://example.com",
                        "agent_token": "token",
                        "workspace_root": str(Path(temp_dir) / "work"),
                        "log_path": str(Path(temp_dir) / "agent.log"),
                        "managed_runtime_path": str(runtime_path),
                        "managed_runtime_version": "0.3.4",
                        "release_repository": "IncleRepo/issue-to-pr-bot",
                    }
                ),
                encoding="utf-8",
            )
            staged_path = runtime_path.parent / ".staged-issue-to-pr-bot-agent.exe"
            mock_install.return_value = (staged_path, "updated", "0.3.4")

            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_console_update(config_path)

        self.assertIn("이미 최신 버전입니다", stdout.getvalue())

    @patch("app.agent.service.spawn_runtime_replacement_helper")
    @patch("app.agent.service.install_standalone_binary")
    def test_install_latest_agent_runtime_schedules_replacement_when_newer_version_exists(
        self,
        mock_install,
        mock_spawn_helper,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            runtime_path = Path(temp_dir) / "bin" / "issue-to-pr-bot-agent"
            runtime_path.parent.mkdir(parents=True)
            runtime_path.write_text("binary", encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "control_plane_url": "https://example.com",
                        "agent_token": "token",
                        "workspace_root": str(Path(temp_dir) / "work"),
                        "log_path": str(Path(temp_dir) / "agent.log"),
                        "managed_runtime_path": str(runtime_path),
                        "managed_runtime_version": "0.3.1",
                        "release_repository": "IncleRepo/issue-to-pr-bot",
                    }
                ),
                encoding="utf-8",
            )
            staged_path = runtime_path.parent / ".staged-issue-to-pr-bot-agent"
            staged_path.write_text("new-binary", encoding="utf-8")
            mock_install.return_value = (staged_path, "updated", "0.3.4")
            config = AgentConfig(
                control_plane_url="https://example.com",
                agent_token="token",
                workspace_root=Path(temp_dir) / "work",
                log_path=Path(temp_dir) / "agent.log",
                managed_runtime_path=runtime_path,
                managed_runtime_version="0.3.1",
                release_repository="IncleRepo/issue-to-pr-bot",
            )

            message = install_latest_agent_runtime(config, config_path)

        self.assertIn("업데이트를 예약했습니다: 0.3.4", message)
        mock_spawn_helper.assert_called_once_with(staged_path, runtime_path, config_path)

    def test_collect_runtime_update_wait_pids_includes_running_task_pids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            state_path = config_path.with_suffix(".state.json")
            state_path.write_text(
                json.dumps(
                    {
                        "running": [
                            {"task_id": "task-a", "pid": 1234},
                            {"task_id": "task-b", "pid": "4567"},
                            {"task_id": "task-c", "pid": "invalid"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            pids = collect_runtime_update_wait_pids(config_path)

        self.assertIn(1234, pids)
        self.assertIn(4567, pids)
        self.assertTrue(any(pid > 0 for pid in pids))

    def test_resolve_requested_log_path_accepts_task_id_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            state_path = config_path.with_suffix(".state.json")
            log_path = Path(temp_dir) / "tasks" / "f9f7995b-214a-4cfc-b5c1-871f4369fb8a.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("task log", encoding="utf-8")
            state_path.write_text(
                json.dumps(
                    {
                        "running": [
                            {
                                "task_id": "f9f7995b-214a-4cfc-b5c1-871f4369fb8a",
                                "log_path": str(log_path),
                                "started_at": "2026-04-17T15:51:27",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            resolved = resolve_requested_log_path(config_path, task_id="f9f7995b-214", latest=False)

        self.assertEqual(resolved, log_path)

    def test_resolve_requested_log_path_prefers_running_latest_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            state_path = config_path.with_suffix(".state.json")
            tasks_root = Path(temp_dir) / "tasks"
            tasks_root.mkdir(parents=True, exist_ok=True)
            older_log = tasks_root / "older.log"
            newer_log = tasks_root / "newer.log"
            older_log.write_text("older", encoding="utf-8")
            newer_log.write_text("newer", encoding="utf-8")
            state_path.write_text(
                json.dumps(
                    {
                        "running": [
                            {
                                "task_id": "task-old",
                                "log_path": str(older_log),
                                "started_at": "2026-04-17T15:00:00",
                            },
                            {
                                "task_id": "task-new",
                                "log_path": str(newer_log),
                                "started_at": "2026-04-17T16:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            resolved = resolve_requested_log_path(config_path, task_id=None, latest=True)

        self.assertEqual(resolved, newer_log)

    @patch("app.agent.service.bot_main.main")
    @patch("app.agent.service.invalidate_codex_session")
    @patch("app.agent.service.shutil.rmtree")
    def test_execute_task_in_workspace_resets_codex_runtime_for_fresh_request(
        self,
        mock_rmtree,
        mock_invalidate_session,
        mock_bot_main,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir(parents=True)
            config = AgentConfig(
                control_plane_url="https://example.com",
                agent_token="token",
                workspace_root=Path(temp_dir) / "work",
                log_path=Path(temp_dir) / "agent.log",
            )
            task = ClaimedTask(
                task_id="task-fresh",
                event_name="issue_comment",
                delivery_id="delivery-fresh",
                repository="IncleRepo/example",
                default_branch="main",
                payload={
                    "action": "created",
                    "repository": {"full_name": "IncleRepo/example"},
                    "issue": {"number": 28, "title": "Title", "body": "Body"},
                    "comment": {
                        "id": 1,
                        "body": "@incle-issue-to-pr-bot 기존에있는 워크스페이스 상관없이 다시 새로 구현부탁해",
                        "user": {"login": "IncleRepo"},
                    },
                },
                github_token="token",
            )

            execute_task_in_workspace(config, workspace, task)

        mock_invalidate_session.assert_called_once_with(workspace)
        removed_paths = [call.args[0] for call in mock_rmtree.call_args_list]
        self.assertIn(get_workspace_input_root(workspace), removed_paths)
        self.assertIn(get_workspace_output_root(workspace), removed_paths)
        self.assertIn(get_legacy_workspace_output_artifact_root(workspace), removed_paths)
        self.assertIn(resolve_workspace_codex_home_root(workspace), removed_paths)
        mock_bot_main.assert_called_once()

    @patch("app.agent.service.bot_main.main")
    @patch("app.agent.service.invalidate_codex_session")
    def test_execute_task_in_workspace_invalidates_previous_codex_session_for_every_new_task(
        self,
        mock_invalidate_session,
        mock_bot_main,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir(parents=True)
            config = AgentConfig(
                control_plane_url="https://example.com",
                agent_token="token",
                workspace_root=Path(temp_dir) / "work",
                log_path=Path(temp_dir) / "agent.log",
            )
            task = ClaimedTask(
                task_id="task-normal",
                event_name="issue_comment",
                delivery_id="delivery-normal",
                repository="IncleRepo/example",
                default_branch="main",
                payload={
                    "action": "created",
                    "repository": {"full_name": "IncleRepo/example"},
                    "issue": {"number": 28, "title": "Title", "body": "Body"},
                    "comment": {
                        "id": 1,
                        "body": "@incle-issue-to-pr-bot 다시 진행해줘",
                        "user": {"login": "IncleRepo"},
                    },
                },
                github_token="token",
            )

            execute_task_in_workspace(config, workspace, task)

        mock_invalidate_session.assert_called_once_with(workspace)
        mock_bot_main.assert_called_once()


if __name__ == "__main__":
    unittest.main()

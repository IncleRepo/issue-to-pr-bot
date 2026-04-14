import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_runner import (
    AgentConfig,
    clear_pid_file,
    read_running_pid,
    resolve_pid_path,
    run_agent_loop,
    try_resolve_log_path,
)


class AgentRunnerTest(unittest.TestCase):
    def test_resolve_pid_path_uses_config_sibling(self) -> None:
        config_path = Path(r"C:\agent\agent-config.json")
        self.assertEqual(resolve_pid_path(config_path), Path(r"C:\agent\agent-config.pid"))

    def test_try_resolve_log_path_reads_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-config.json"
            config_path.write_text('{"log_path":"C:\\\\logs\\\\agent.log"}', encoding="utf-8")
            self.assertEqual(str(try_resolve_log_path(config_path)), r"C:\logs\agent.log")

    @patch("app.agent_runner.is_process_running", return_value=False)
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

    @patch("app.agent_runner.clear_pid_file")
    @patch("app.agent_runner.time.sleep")
    @patch("app.agent_runner.log_message")
    @patch("app.agent_runner.claim_task")
    @patch("app.agent_runner.ensure_single_instance")
    def test_run_agent_loop_keeps_running_after_claim_error(
        self,
        _mock_lock,
        mock_claim,
        mock_log,
        _mock_sleep,
        mock_clear,
    ) -> None:
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            log_path=Path(r"C:\logs\agent.log"),
        )
        config_path = Path(r"C:\agent\agent-config.json")
        mock_claim.side_effect = [RuntimeError("boom"), KeyboardInterrupt()]

        with self.assertRaises(KeyboardInterrupt):
            run_agent_loop(config, config_path)

        self.assertTrue(any("작업 조회 실패: boom" in call.args[1] for call in mock_log.call_args_list))
        mock_clear.assert_called_once_with(config_path)

    @patch("app.agent_runner.clear_pid_file")
    @patch("app.agent_runner.log_message")
    @patch("app.agent_runner.run_claimed_task")
    @patch("app.agent_runner.claim_task")
    @patch("app.agent_runner.ensure_single_instance")
    def test_run_agent_loop_keeps_running_after_task_error(
        self,
        _mock_lock,
        mock_claim,
        mock_run_task,
        mock_log,
        mock_clear,
    ) -> None:
        config = AgentConfig(
            control_plane_url="https://example.com",
            agent_token="token",
            workspace_root=Path(r"C:\work"),
            log_path=Path(r"C:\logs\agent.log"),
        )
        config_path = Path(r"C:\agent\agent-config.json")
        mock_claim.side_effect = ["task", KeyboardInterrupt()]
        mock_run_task.side_effect = RuntimeError("task failed")

        with self.assertRaises(KeyboardInterrupt):
            run_agent_loop(config, config_path)

        self.assertTrue(any("작업 실행 실패: task failed" in call.args[1] for call in mock_log.call_args_list))
        mock_clear.assert_called_once_with(config_path)

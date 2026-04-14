import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_runner import (
    clear_pid_file,
    read_running_pid,
    resolve_pid_path,
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

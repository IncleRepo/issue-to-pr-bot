import unittest
from pathlib import Path

from app.codex_runner import build_codex_command


class CodexRunnerTest(unittest.TestCase):
    def test_build_codex_command_uses_ephemeral_noninteractive_exec(self) -> None:
        workspace = Path("/workspace")
        command = build_codex_command(workspace)

        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn("--cd", command)
        self.assertEqual(command[command.index("--cd") + 1], str(workspace))
        self.assertEqual(command[-1], "-")

    def test_build_codex_command_can_write_last_message(self) -> None:
        output_path = Path("/tmp/last-message.txt")
        command = build_codex_command(Path("/workspace"), output_last_message=output_path)

        self.assertEqual(command[command.index("--output-last-message") + 1], str(output_path))
        self.assertEqual(command[-1], "-")


if __name__ == "__main__":
    unittest.main()

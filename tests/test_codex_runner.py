import unittest
from pathlib import Path

from app.bot import BotCommand
from app.codex_runner import build_codex_command, get_effort


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

    def test_build_codex_command_can_set_effort(self) -> None:
        command = build_codex_command(Path("/workspace"), effort="high")

        self.assertIn("-c", command)
        self.assertIn('reasoning_effort="high"', command)

    def test_get_effort_validates_values(self) -> None:
        command = BotCommand("run", "/bot run", "", {"effort": "xhigh"})
        invalid_command = BotCommand("run", "/bot run", "", {"effort": "turbo"})

        self.assertEqual(get_effort(command), "xhigh")
        with self.assertRaises(ValueError):
            get_effort(invalid_command)


if __name__ == "__main__":
    unittest.main()

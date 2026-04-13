import tempfile
import unittest
from pathlib import Path

from app.install_manager import (
    DEFAULT_ENGINE_REF,
    DEFAULT_ENGINE_REPOSITORY,
    InstallManagerOptions,
    install_repository_environment,
    main,
    render_workflow_template,
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


if __name__ == "__main__":
    unittest.main()

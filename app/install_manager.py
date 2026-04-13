from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_ENGINE_REPOSITORY = "IncleRepo/issue-to-pr-bot"
DEFAULT_ENGINE_REF = "main"
DEFAULT_RUNNER_LABELS = ["self-hosted", "Windows"]
CONFIG_TEMPLATE_NAME = ".issue-to-pr-bot.yml.example"
WORKFLOW_SPECS = (
    ("issue-comment.yml.example", ".github/workflows/issue-comment.yml"),
    ("pull-request-review.yml.example", ".github/workflows/pull-request-review.yml"),
    ("pull-request-review-comment.yml.example", ".github/workflows/pull-request-review-comment.yml"),
)
USES_LINE_PREFIX = "    uses: "
RUNNER_LABEL_LINE_PREFIX = "      runner_labels_json: "
ENGINE_REPOSITORY_LINE_PREFIX = "      engine_repository: "
ENGINE_REF_LINE_PREFIX = "      engine_ref: "


@dataclass(frozen=True)
class InstallManagerOptions:
    target: Path
    engine_repository: str = DEFAULT_ENGINE_REPOSITORY
    engine_ref: str = DEFAULT_ENGINE_REF
    runner_labels: list[str] | None = None
    include_review_workflows: bool = True
    write_config: bool = False
    force: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class FileOperation:
    path: Path
    action: str


@dataclass(frozen=True)
class InstallManagerResult:
    target: Path
    operations: list[FileOperation]
    next_steps: list[str]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command != "init":
        raise ValueError(f"Unsupported command: {args.command}")

    options = InstallManagerOptions(
        target=Path(args.target).resolve(),
        engine_repository=args.engine_repository,
        engine_ref=args.engine_ref,
        runner_labels=args.runner_label or DEFAULT_RUNNER_LABELS.copy(),
        include_review_workflows=not args.skip_review_workflows,
        write_config=args.write_config,
        force=args.force,
        dry_run=args.dry_run,
    )
    result = install_repository_environment(options)
    print(format_install_result(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.install_manager",
        description="Install the minimal issue-to-pr-bot workflow environment into a target repository.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="Write the reusable workflow callers and optional config into a target repository.",
    )
    init_parser.add_argument("--target", required=True, help="Path to the target repository root.")
    init_parser.add_argument(
        "--engine-repository",
        default=DEFAULT_ENGINE_REPOSITORY,
        help="Repository that hosts the reusable bot workflow and engine.",
    )
    init_parser.add_argument(
        "--engine-ref",
        default=DEFAULT_ENGINE_REF,
        help="Ref of the central engine repository to call from the generated workflows.",
    )
    init_parser.add_argument(
        "--runner-label",
        action="append",
        help="Runner label to include. Repeat to build a custom label list.",
    )
    init_parser.add_argument(
        "--skip-review-workflows",
        action="store_true",
        help="Install only the issue comment workflow.",
    )
    init_parser.add_argument(
        "--write-config",
        action="store_true",
        help="Also write a minimal .issue-to-pr-bot.yml file when it does not exist.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing generated files.",
    )
    init_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without changing files.",
    )
    return parser


def install_repository_environment(options: InstallManagerOptions) -> InstallManagerResult:
    ensure_target_exists(options.target)

    operations: list[FileOperation] = []
    template_dir = repository_root() / "templates"
    runner_labels = options.runner_labels or DEFAULT_RUNNER_LABELS.copy()

    for template_name, relative_output_path in selected_workflow_specs(options.include_review_workflows):
        target_path = options.target / relative_output_path
        template_text = (template_dir / template_name).read_text(encoding="utf-8")
        rendered = render_workflow_template(
            template_text,
            engine_repository=options.engine_repository,
            engine_ref=options.engine_ref,
            runner_labels=runner_labels,
        )
        action = write_managed_file(target_path, rendered, force=options.force, dry_run=options.dry_run)
        operations.append(FileOperation(path=target_path, action=action))

    if options.write_config:
        config_template_path = template_dir / CONFIG_TEMPLATE_NAME
        config_target_path = options.target / ".issue-to-pr-bot.yml"
        action = write_managed_file(
            config_target_path,
            config_template_path.read_text(encoding="utf-8"),
            force=options.force,
            dry_run=options.dry_run,
        )
        operations.append(FileOperation(path=config_target_path, action=action))

    return InstallManagerResult(
        target=options.target,
        operations=operations,
        next_steps=build_next_steps(options, operations),
    )


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_target_exists(target: Path) -> None:
    if not target.exists():
        raise FileNotFoundError(f"Target repository path does not exist: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"Target repository path is not a directory: {target}")


def selected_workflow_specs(include_review_workflows: bool) -> list[tuple[str, str]]:
    if include_review_workflows:
        return list(WORKFLOW_SPECS)
    return [WORKFLOW_SPECS[0]]


def render_workflow_template(
    template_text: str,
    *,
    engine_repository: str,
    engine_ref: str,
    runner_labels: list[str],
) -> str:
    lines: list[str] = []
    runner_labels_json = json.dumps(runner_labels, ensure_ascii=True, separators=(",", ":"))

    for raw_line in template_text.splitlines():
        if raw_line.startswith(USES_LINE_PREFIX):
            lines.append(f'{USES_LINE_PREFIX}{engine_repository}/.github/workflows/reusable-bot.yml@{engine_ref}')
        elif raw_line.startswith(RUNNER_LABEL_LINE_PREFIX):
            lines.append(f"{RUNNER_LABEL_LINE_PREFIX}'{runner_labels_json}'")
        elif raw_line.startswith(ENGINE_REPOSITORY_LINE_PREFIX):
            lines.append(f'{ENGINE_REPOSITORY_LINE_PREFIX}"{engine_repository}"')
        elif raw_line.startswith(ENGINE_REF_LINE_PREFIX):
            lines.append(f'{ENGINE_REF_LINE_PREFIX}"{engine_ref}"')
        else:
            lines.append(raw_line)
    return "\n".join(lines) + "\n"


def write_managed_file(target_path: Path, content: str, *, force: bool, dry_run: bool) -> str:
    if target_path.exists():
        existing = target_path.read_text(encoding="utf-8")
        if existing == content:
            return "unchanged"
        if not force:
            return "skipped"
        if dry_run:
            return "would_overwrite"
        target_path.write_text(content, encoding="utf-8", newline="\n")
        return "overwritten"

    if dry_run:
        return "would_create"

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8", newline="\n")
    return "created"


def build_next_steps(options: InstallManagerOptions, operations: list[FileOperation]) -> list[str]:
    workflow_paths = [operation for operation in operations if operation.path.name.endswith(".yml")]
    config_written = any(operation.path.name == ".issue-to-pr-bot.yml" for operation in operations)
    created_anything = any(operation.action in {"created", "overwritten"} for operation in operations)

    steps = [
        "GitHub App이 아직 없으면 App 생성 후 대상 저장소에 설치",
        "대상 저장소 Settings > Secrets and variables > Actions에 `BOT_MENTION`, `BOT_APP_ID`, `BOT_APP_PRIVATE_KEY` 등록",
        "self-hosted runner와 Docker, Codex CLI 로그인이 된 머신 준비",
    ]
    if workflow_paths:
        steps.append("생성된 workflow 파일을 커밋하고 push")
    if config_written:
        steps.append("필요한 경우 `.issue-to-pr-bot.yml`에 외부 context나 필수 시크릿만 추가")
    if not created_anything and not options.dry_run:
        steps.append("이미 설치된 파일이 있으면 `--force`로 다시 생성 가능")
    if options.dry_run:
        steps.append("실제 적용하려면 같은 명령에서 `--dry-run` 제거")
    return steps


def format_install_result(result: InstallManagerResult) -> str:
    lines = [
        "## 설치 결과",
        "",
        f"- 대상 저장소: `{result.target}`",
        "",
        "### 파일 작업",
    ]
    if not result.operations:
        lines.append("- 없음")
    else:
        for operation in result.operations:
            relative = operation.path.name if operation.path.parent == result.target else operation.path.relative_to(result.target).as_posix()
            lines.append(f"- `{relative}` -> `{operation.action}`")
    lines.extend(["", "### 다음 단계"])
    for step in result.next_steps:
        lines.append(f"- {step}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

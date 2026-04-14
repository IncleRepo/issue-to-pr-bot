from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Sequence


DEFAULT_ENGINE_REPOSITORY = "IncleRepo/issue-to-pr-bot"
DEFAULT_ENGINE_REF = "main"
DEFAULT_RUNNER_LABELS = ["self-hosted", "Windows"]
DEFAULT_RUNNER_WORK_DIRECTORY = "_work"
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
EXPECTED_REPOSITORY_VARIABLES = ("BOT_MENTION", "BOT_APP_ID")
EXPECTED_REPOSITORY_SECRETS = ("BOT_APP_PRIVATE_KEY",)
DEFAULT_RUNNER_ROOT_CANDIDATES = (
    Path("C:/actions-runner"),
    Path("C:/actions-runner-live"),
    Path.home() / "actions-runner",
    Path.home() / "actions-runner-live",
)
LATEST_RUNNER_RELEASE_API = "https://api.github.com/repos/actions/runner/releases/latest"
RUNNER_RELEASE_USER_AGENT = "issue-to-pr-bot-manager"
DEFAULT_SUPPORT_ROOT = Path.home() / "issue-to-pr-bot-data"
DEFAULT_AGENT_CONFIG_PATH = Path.home() / ".issue-to-pr-bot-agent" / "agent-config.json"
AUTO_INSTALL_PACKAGES = {
    "gh": {"label": "GitHub CLI", "winget": "GitHub.cli", "choco": "gh"},
    "git": {"label": "Git", "winget": "Git.Git", "choco": "git"},
}
CRITICAL_HOST_CHECKS = {
    "Python",
    "Git",
    "Docker CLI",
    "Docker daemon",
    "Codex CLI",
    "Codex auth",
}


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


@dataclass(frozen=True)
class DoctorOptions:
    target: Path | None = None
    repository: str | None = None
    runner_root: Path | None = None


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DoctorResult:
    checks: list[DoctorCheck]


@dataclass(frozen=True)
class GithubConfigurationOptions:
    repository: str
    bot_mention: str
    bot_app_id: str
    bot_app_private_key_file: Path
    dry_run: bool = False


@dataclass(frozen=True)
class GithubConfigurationOperation:
    name: str
    action: str


@dataclass(frozen=True)
class GithubConfigurationResult:
    repository: str
    operations: list[GithubConfigurationOperation]
    next_steps: list[str]


@dataclass(frozen=True)
class BootstrapResult:
    install_result: InstallManagerResult
    doctor_result: DoctorResult
    github_result: GithubConfigurationResult | None


@dataclass(frozen=True)
class HostBootstrapOptions:
    runner_root: Path
    repository: str | None = None
    organization: str | None = None
    runner_url: str | None = None
    runner_token: str | None = None
    runner_name: str | None = None
    runner_labels: list[str] | None = None
    runner_archive: Path | None = None
    runner_download_url: str | None = None
    runner_work_directory: str = DEFAULT_RUNNER_WORK_DIRECTORY
    run_as_service: bool = False
    replace_existing: bool = True
    dry_run: bool = False


@dataclass(frozen=True)
class HostBootstrapOperation:
    name: str
    action: str
    detail: str


@dataclass(frozen=True)
class HostBootstrapResult:
    runner_root: Path
    doctor_result: DoctorResult
    operations: list[HostBootstrapOperation]
    next_steps: list[str]


@dataclass(frozen=True)
class RunnerArchiveSource:
    url: str
    name: str
    description: str


@dataclass(frozen=True)
class RepositorySupportPaths:
    context_dir: Path
    secrets_file: Path


@dataclass(frozen=True)
class ControlPlaneOptions:
    target: Path
    worker_name: str
    bot_mention: str
    force: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class AgentBootstrapOptions:
    control_plane_url: str
    agent_token: str
    repositories: list[str]
    workspace_root: Path
    config_path: Path = DEFAULT_AGENT_CONFIG_PATH
    poll_interval_seconds: int = 10
    force: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class AgentBootstrapResult:
    config_path: Path
    operations: list[FileOperation]
    next_steps: list[str]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "init":
            result = install_repository_environment(build_install_options(args))
            print(format_install_result(result))
            return 0
        if args.command == "update":
            result = install_repository_environment(build_install_options(args, force_default=True))
            print(format_install_result(result))
            return 0
        if args.command == "doctor":
            result = run_doctor(build_doctor_options(args))
            print(format_doctor_result(result))
            return 0
        if args.command == "configure-github":
            result = configure_repository_settings(build_github_configuration_options(args, required=True))
            print(format_github_configuration_result(result))
            return 0
        if args.command == "bootstrap":
            result = bootstrap_repository_environment(
                build_install_options(args),
                build_doctor_options(args),
                build_github_configuration_options(args, required=False),
            )
            print(format_bootstrap_result(result))
            return 0
        if args.command == "bootstrap-host":
            result = bootstrap_host_environment(build_host_bootstrap_options(args))
            print(format_host_bootstrap_result(result))
            return 0
        if args.command == "init-control-plane":
            result = init_control_plane_environment(build_control_plane_options(args))
            print(format_install_result(result))
            return 0
        if args.command == "bootstrap-agent":
            result = bootstrap_agent_environment(build_agent_bootstrap_options(args))
            print(format_agent_bootstrap_result(result))
            return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue-to-pr-bot-manager",
        description="Centralized installer and operations manager for issue-to-pr-bot target repositories.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="Write thin reusable-workflow callers into a target repository.",
    )
    add_install_arguments(init_parser)

    update_parser = subparsers.add_parser(
        "update",
        help="Refresh the managed workflow files in a target repository.",
    )
    add_install_arguments(update_parser)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check local machine prerequisites and optional target repository wiring.",
    )
    add_doctor_arguments(doctor_parser)

    configure_parser = subparsers.add_parser(
        "configure-github",
        help="Configure repository variables and secrets through GitHub CLI.",
    )
    add_github_configuration_arguments(configure_parser)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Install workflows, optionally configure GitHub settings, and run readiness checks.",
    )
    add_install_arguments(bootstrap_parser)
    add_doctor_arguments(bootstrap_parser)
    add_github_configuration_arguments(bootstrap_parser)

    bootstrap_host_parser = subparsers.add_parser(
        "bootstrap-host",
        help="Prepare the Windows host: validate prerequisites and register a self-hosted runner.",
    )
    add_host_bootstrap_arguments(bootstrap_host_parser)

    control_plane_parser = subparsers.add_parser(
        "init-control-plane",
        help="Scaffold a Cloudflare Worker control-plane project.",
    )
    control_plane_parser.add_argument("--target", required=True, help="Path to write the Worker project into.")
    control_plane_parser.add_argument("--worker-name", required=True, help="Cloudflare Worker name.")
    control_plane_parser.add_argument("--bot-mention", default="@incle-issue-to-pr-bot")
    control_plane_parser.add_argument("--force", action="store_true")
    control_plane_parser.add_argument("--dry-run", action="store_true")

    agent_parser = subparsers.add_parser(
        "bootstrap-agent",
        help="Write a local polling-agent config for the Cloudflare control plane.",
    )
    agent_parser.add_argument("--control-plane-url", required=True)
    agent_parser.add_argument("--agent-token", required=True)
    agent_parser.add_argument("--repository", action="append", dest="repositories", default=[])
    agent_parser.add_argument("--workspace-root", default=str(DEFAULT_SUPPORT_ROOT / "agent-workspaces"))
    agent_parser.add_argument("--config-path", default=str(DEFAULT_AGENT_CONFIG_PATH))
    agent_parser.add_argument("--poll-interval-seconds", type=int, default=10)
    agent_parser.add_argument("--force", action="store_true")
    agent_parser.add_argument("--dry-run", action="store_true")

    return parser


def add_install_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", required=True, help="Path to the target repository root.")
    parser.add_argument(
        "--engine-repository",
        default=DEFAULT_ENGINE_REPOSITORY,
        help="Repository that hosts the reusable bot workflow and engine.",
    )
    parser.add_argument(
        "--engine-ref",
        default=DEFAULT_ENGINE_REF,
        help="Ref of the central engine repository to call from the generated workflows.",
    )
    parser.add_argument(
        "--runner-label",
        action="append",
        help="Runner label to include. Repeat to build a custom label list.",
    )
    parser.add_argument(
        "--skip-review-workflows",
        action="store_true",
        help="Install only the issue comment workflow.",
    )
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="Also write a minimal .issue-to-pr-bot.yml file when it does not exist.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing managed files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying files or GitHub settings.",
    )


def add_doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        help="GitHub repository slug like owner/name. When provided, doctor also checks Actions variables and secrets through `gh`.",
    )
    parser.add_argument(
        "--runner-root",
        help="Optional self-hosted runner root path. If omitted, common Windows runner locations are probed.",
    )


def add_github_configuration_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bot-mention", help="Bot mention to write into repository variable BOT_MENTION.")
    parser.add_argument("--bot-app-id", help="GitHub App ID to write into repository variable BOT_APP_ID.")
    parser.add_argument(
        "--bot-app-private-key-file",
        help="Path to the GitHub App private key PEM file to upload as BOT_APP_PRIVATE_KEY.",
    )


def add_host_bootstrap_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runner-root", required=True, help="Path where the self-hosted runner should live.")
    parser.add_argument("--repo", help="Repository slug like owner/name for a repository-level runner.")
    parser.add_argument("--org", help="Organization name for an organization-level runner.")
    parser.add_argument("--runner-url", help="Explicit runner registration URL. Overrides --repo/--org.")
    parser.add_argument("--runner-token", help="Registration token. If omitted, `gh api` is used when possible.")
    parser.add_argument("--runner-name", help="Runner name. Defaults to this machine name.")
    parser.add_argument(
        "--runner-label",
        action="append",
        help="Optional custom runner label. Repeat to add multiple labels.",
    )
    parser.add_argument("--runner-archive", help="Path to a pre-downloaded runner zip archive.")
    parser.add_argument("--runner-download-url", help="Explicit runner zip download URL.")
    parser.add_argument(
        "--runner-work-directory",
        default=DEFAULT_RUNNER_WORK_DIRECTORY,
        help="Runner work directory passed to config.cmd.",
    )
    parser.add_argument(
        "--run-as-service",
        action="store_true",
        help="Install and start the runner as a Windows service after configuration.",
    )
    parser.add_argument(
        "--no-replace-existing",
        action="store_true",
        help="Do not pass --replace when configuring the runner.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without downloading or configuring the runner.",
    )


def build_install_options(args: argparse.Namespace, *, force_default: bool = False) -> InstallManagerOptions:
    return InstallManagerOptions(
        target=Path(args.target).resolve(),
        engine_repository=args.engine_repository,
        engine_ref=args.engine_ref,
        runner_labels=args.runner_label or DEFAULT_RUNNER_LABELS.copy(),
        include_review_workflows=not args.skip_review_workflows,
        write_config=args.write_config,
        force=args.force or force_default,
        dry_run=args.dry_run,
    )


def build_doctor_options(args: argparse.Namespace) -> DoctorOptions:
    target = Path(args.target).resolve() if getattr(args, "target", None) else None
    runner_root = Path(args.runner_root).resolve() if getattr(args, "runner_root", None) else None
    repository = getattr(args, "repo", None)
    return DoctorOptions(target=target, repository=repository, runner_root=runner_root)


def build_github_configuration_options(
    args: argparse.Namespace,
    *,
    required: bool,
) -> GithubConfigurationOptions | None:
    repository = getattr(args, "repo", None)
    mention = getattr(args, "bot_mention", None)
    app_id = getattr(args, "bot_app_id", None)
    private_key_file = getattr(args, "bot_app_private_key_file", None)
    any_value = any(value for value in (repository, mention, app_id, private_key_file))

    if not any_value and not required:
        return None

    missing: list[str] = []
    if not repository:
        missing.append("--repo")
    if not mention:
        missing.append("--bot-mention")
    if not app_id:
        missing.append("--bot-app-id")
    if not private_key_file:
        missing.append("--bot-app-private-key-file")
    if missing:
        raise ValueError(f"Missing GitHub configuration arguments: {', '.join(missing)}")

    return GithubConfigurationOptions(
        repository=repository,
        bot_mention=mention,
        bot_app_id=app_id,
        bot_app_private_key_file=Path(private_key_file).resolve(),
        dry_run=args.dry_run,
    )


def build_host_bootstrap_options(args: argparse.Namespace) -> HostBootstrapOptions:
    repository = getattr(args, "repo", None)
    organization = getattr(args, "org", None)
    runner_url = getattr(args, "runner_url", None)

    if repository and organization and not runner_url:
        raise ValueError("--repo와 --org는 동시에 사용할 수 없습니다.")
    if not any(value for value in (repository, organization, runner_url)):
        raise ValueError("--repo, --org, --runner-url 중 하나는 필요합니다.")

    runner_archive = Path(args.runner_archive).resolve() if getattr(args, "runner_archive", None) else None
    return HostBootstrapOptions(
        runner_root=Path(args.runner_root).resolve(),
        repository=repository,
        organization=organization,
        runner_url=runner_url,
        runner_token=getattr(args, "runner_token", None),
        runner_name=getattr(args, "runner_name", None) or socket.gethostname(),
        runner_labels=getattr(args, "runner_label", None),
        runner_archive=runner_archive,
        runner_download_url=getattr(args, "runner_download_url", None),
        runner_work_directory=args.runner_work_directory,
        run_as_service=args.run_as_service,
        replace_existing=not args.no_replace_existing,
        dry_run=args.dry_run,
    )


def build_control_plane_options(args: argparse.Namespace) -> ControlPlaneOptions:
    return ControlPlaneOptions(
        target=Path(args.target).resolve(),
        worker_name=args.worker_name,
        bot_mention=args.bot_mention,
        force=args.force,
        dry_run=args.dry_run,
    )


def build_agent_bootstrap_options(args: argparse.Namespace) -> AgentBootstrapOptions:
    return AgentBootstrapOptions(
        control_plane_url=args.control_plane_url.rstrip("/"),
        agent_token=args.agent_token,
        repositories=list(args.repositories),
        workspace_root=Path(args.workspace_root).resolve(),
        config_path=Path(args.config_path).resolve(),
        poll_interval_seconds=args.poll_interval_seconds,
        force=args.force,
        dry_run=args.dry_run,
    )


def install_repository_environment(options: InstallManagerOptions) -> InstallManagerResult:
    ensure_target_exists(options.target)

    operations: list[FileOperation] = []
    runner_labels = options.runner_labels or DEFAULT_RUNNER_LABELS.copy()

    for template_name, relative_output_path in selected_workflow_specs(options.include_review_workflows):
        target_path = options.target / relative_output_path
        template_text = load_template_text(template_name)
        rendered = render_workflow_template(
            template_text,
            engine_repository=options.engine_repository,
            engine_ref=options.engine_ref,
            runner_labels=runner_labels,
        )
        action = write_managed_file(target_path, rendered, force=options.force, dry_run=options.dry_run)
        operations.append(FileOperation(path=target_path, action=action))

    if options.write_config:
        config_target_path = options.target / ".issue-to-pr-bot.yml"
        action = write_managed_file(
            config_target_path,
            load_template_text(CONFIG_TEMPLATE_NAME),
            force=options.force,
            dry_run=options.dry_run,
        )
        operations.append(FileOperation(path=config_target_path, action=action))

    return InstallManagerResult(
        target=options.target,
        operations=operations,
        next_steps=build_install_next_steps(options, operations),
    )


def init_control_plane_environment(options: ControlPlaneOptions) -> InstallManagerResult:
    target = options.target
    if not options.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    replacements = {
        "{{WORKER_NAME}}": options.worker_name,
        "{{BOT_MENTION}}": options.bot_mention,
    }
    specs = [
        ("package.json.example", "package.json"),
        ("wrangler.jsonc.example", "wrangler.jsonc"),
        ("README.md.example", "README.md"),
        ("src/index.js.example", "src/index.js"),
    ]

    operations: list[FileOperation] = []
    for template_name, relative_output_path in specs:
        template_text = load_worker_template_text(template_name)
        rendered = render_text_template(template_text, replacements)
        action = write_managed_file(
            target / relative_output_path,
            rendered,
            force=options.force,
            dry_run=options.dry_run,
        )
        operations.append(FileOperation(path=target / relative_output_path, action=action))

    return InstallManagerResult(
        target=target,
        operations=operations,
        next_steps=[
            "Worker 디렉터리에서 `npm install` 실행",
            "Cloudflare KV namespace를 만들고 `wrangler.jsonc`의 TASK_QUEUE id를 채우기",
            "`npx wrangler secret put CONTROL_PLANE_AGENT_TOKEN` 등 필수 secret 등록",
            "`npx wrangler deploy`로 배포",
        ],
    )


def bootstrap_agent_environment(options: AgentBootstrapOptions) -> AgentBootstrapResult:
    if not options.repositories:
        raise ValueError("--repository를 하나 이상 지정해야 합니다.")

    if not options.dry_run:
        options.workspace_root.mkdir(parents=True, exist_ok=True)
        options.config_path.parent.mkdir(parents=True, exist_ok=True)

    config_body = json.dumps(
        {
            "control_plane_url": options.control_plane_url,
            "agent_token": options.agent_token,
            "repositories": options.repositories,
            "workspace_root": str(options.workspace_root),
            "poll_interval_seconds": options.poll_interval_seconds,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    action = write_managed_file(
        options.config_path,
        config_body,
        force=options.force,
        dry_run=options.dry_run,
    )
    return AgentBootstrapResult(
        config_path=options.config_path,
        operations=[FileOperation(path=options.config_path, action=action)],
        next_steps=[
            "Codex 로그인 상태 확인",
            f"`issue-to-pr-bot-agent serve --config {options.config_path}` 로 agent 시작",
            "필요하면 Windows 작업 스케줄러나 서비스로 agent를 상시 실행",
        ],
    )


def load_template_text(template_name: str) -> str:
    return resources.files("app.manager_templates").joinpath(template_name).read_text(encoding="utf-8")


def load_worker_template_text(template_name: str) -> str:
    return resources.files("app.worker_templates").joinpath(template_name).read_text(encoding="utf-8")


def render_text_template(template_text: str, replacements: dict[str, str]) -> str:
    rendered = template_text
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


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
            lines.append(f"{USES_LINE_PREFIX}{engine_repository}/.github/workflows/reusable-bot.yml@{engine_ref}")
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


def build_install_next_steps(options: InstallManagerOptions, operations: list[FileOperation]) -> list[str]:
    created_anything = any(operation.action in {"created", "overwritten"} for operation in operations)
    steps = [
        "가능하면 organization-level runner, reusable workflow, secrets/variables로 운영을 중앙화하세요.",
        "대상 저장소 Settings > Secrets and variables > Actions에 `BOT_MENTION`, `BOT_APP_ID`, `BOT_APP_PRIVATE_KEY`가 준비되어 있는지 확인하세요.",
    ]
    if created_anything:
        steps.append("생성되거나 갱신된 파일을 커밋하고 push하세요.")
    if options.write_config:
        steps.append("`.issue-to-pr-bot.yml`은 필요할 때만 추가 설정을 넣고, 기본값은 최대한 단순하게 유지하세요.")
    if not created_anything and not options.dry_run:
        steps.append("기존 관리 파일을 다시 반영하려면 `update` 명령 또는 `--force`를 사용하세요.")
    if options.dry_run:
        steps.append("실제 적용하려면 같은 명령에서 `--dry-run`을 제거하세요.")
    return steps


def detect_package_manager() -> str | None:
    if shutil.which("winget"):
        return "winget"
    if shutil.which("choco"):
        return "choco"
    return None


def refresh_process_path() -> None:
    if os.name != "nt":
        return

    machine = run_command(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "[Environment]::GetEnvironmentVariable('Path','Machine')",
        ]
    )
    user = run_command(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "[Environment]::GetEnvironmentVariable('Path','User')",
        ]
    )
    if machine.returncode != 0 and user.returncode != 0:
        return

    current_parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    merged: list[str] = []
    for raw in (machine.stdout, user.stdout, os.environ.get("PATH", "")):
        for part in raw.split(os.pathsep):
            normalized = part.strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
    if merged:
        os.environ["PATH"] = os.pathsep.join(merged + [part for part in current_parts if part not in merged])


def auto_install_command_if_missing(executable: str, *, dry_run: bool = False) -> bool:
    if shutil.which(executable):
        return False

    package = AUTO_INSTALL_PACKAGES.get(executable)
    if package is None:
        return False

    manager = detect_package_manager()
    if manager is None:
        return False

    if manager == "winget":
        command = [
            "winget",
            "install",
            "--id",
            package["winget"],
            "-e",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ]
    else:
        command = ["choco", "install", package["choco"], "-y"]

    label = package["label"]
    if dry_run:
        print(f"{label} 자동 설치 예정: {' '.join(command)}")
        return True

    print(f"{label} 자동 설치 시도: {' '.join(command)}")
    completed = run_command(command)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} 자동 설치 실패: {summarize_command_failure(completed)}")

    refresh_process_path()
    if not shutil.which(executable):
        raise RuntimeError(f"{label} 설치 후에도 `{executable}`를 찾지 못했습니다. 새 터미널을 열고 다시 시도하세요.")
    return True


def ensure_bootstrap_tooling(
    *,
    requires_github_cli: bool,
    dry_run: bool,
) -> list[str]:
    attempted: list[str] = []

    if auto_install_command_if_missing("git", dry_run=dry_run):
        attempted.append("git")

    if requires_github_cli and auto_install_command_if_missing("gh", dry_run=dry_run):
        attempted.append("gh")

    return attempted


def build_repository_support_paths(repository: str) -> RepositorySupportPaths:
    slug = repository.replace("/", "__")
    base = DEFAULT_SUPPORT_ROOT / slug
    return RepositorySupportPaths(
        context_dir=base / "context",
        secrets_file=base / "secrets.env",
    )


def ensure_repository_support_paths(repository: str, *, dry_run: bool) -> RepositorySupportPaths:
    paths = build_repository_support_paths(repository)
    if dry_run:
        return paths

    paths.context_dir.mkdir(parents=True, exist_ok=True)
    paths.secrets_file.parent.mkdir(parents=True, exist_ok=True)
    if not paths.secrets_file.exists():
        paths.secrets_file.write_text("", encoding="utf-8")
    return paths


def run_doctor(options: DoctorOptions) -> DoctorResult:
    checks: list[DoctorCheck] = []
    checks.append(probe_command("Python", "python", ["--version"]))
    checks.append(probe_command("Git", "git", ["--version"]))

    docker_path = shutil.which("docker")
    if docker_path:
        checks.append(probe_command("Docker CLI", "docker", ["--version"]))
        checks.append(check_docker_daemon())
    else:
        checks.append(DoctorCheck("Docker CLI", "fail", "`docker`가 PATH에 없습니다. Docker Desktop 또는 Engine 설치가 필요합니다."))
        checks.append(DoctorCheck("Docker daemon", "warn", "Docker CLI가 없어 daemon 상태를 확인하지 못했습니다."))

    codex_path = shutil.which("codex")
    if codex_path:
        checks.append(probe_command("Codex CLI", "codex", ["--version"]))
    else:
        checks.append(DoctorCheck("Codex CLI", "fail", "`codex`가 PATH에 없습니다. OpenAI Codex CLI 설치가 필요합니다."))
    checks.append(check_codex_auth_files())
    checks.extend(check_runner_root(options.runner_root))
    checks.append(probe_command("GitHub CLI", "gh", ["--version"], required=False))

    if options.target is not None:
        checks.extend(check_target_repository(options.target))
    if options.repository is not None:
        checks.extend(check_repository_settings(options.repository))

    return DoctorResult(checks=checks)


def probe_command(
    name: str,
    executable: str,
    version_args: list[str],
    *,
    required: bool = True,
) -> DoctorCheck:
    command_path = shutil.which(executable)
    probe_executable = executable
    if executable == "python" and not command_path and Path(sys.executable).exists():
        command_path = sys.executable
        probe_executable = sys.executable
    if not command_path:
        status = "fail" if required else "warn"
        return DoctorCheck(name, status, f"`{executable}`가 PATH에 없습니다.")

    completed = run_command([probe_executable, *version_args])
    if completed.returncode != 0:
        status = "fail" if required else "warn"
        return DoctorCheck(name, status, summarize_command_failure(completed))

    version_line = first_nonempty_line(completed.stdout, completed.stderr) or command_path
    return DoctorCheck(name, "pass", version_line)


def check_docker_daemon() -> DoctorCheck:
    completed = run_command(["docker", "info", "--format", "{{.ServerVersion}}"])
    if completed.returncode != 0:
        return DoctorCheck("Docker daemon", "fail", summarize_command_failure(completed))
    version = first_nonempty_line(completed.stdout) or "daemon reachable"
    return DoctorCheck("Docker daemon", "pass", version)


def check_codex_auth_files() -> DoctorCheck:
    codex_home = resolve_codex_home()
    auth_json = codex_home / "auth.json"
    config_toml = codex_home / "config.toml"
    missing = [path.name for path in (auth_json, config_toml) if not path.exists()]
    if missing:
        return DoctorCheck(
            "Codex auth",
            "fail",
            f"`{codex_home}`에서 {', '.join(missing)} 파일을 찾지 못했습니다. 먼저 Codex 로그인부터 완료하세요.",
        )
    return DoctorCheck("Codex auth", "pass", str(codex_home))


def resolve_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME_HOST") or os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return Path(user_profile) / ".codex"
    return Path.home() / ".codex"


def check_runner_root(explicit_runner_root: Path | None) -> list[DoctorCheck]:
    runner_root = explicit_runner_root or detect_runner_root()
    if runner_root is None:
        return [
            DoctorCheck(
                "Self-hosted runner",
                "warn",
                "일반적인 runner 경로를 찾지 못했습니다. `--runner-root`로 경로를 지정하면 더 정확하게 점검할 수 있습니다.",
            )
        ]

    run_cmd = runner_root / "run.cmd"
    config_cmd = runner_root / "config.cmd"
    checks: list[DoctorCheck] = []
    if run_cmd.exists() or config_cmd.exists():
        checks.append(DoctorCheck("Self-hosted runner", "pass", str(runner_root)))
        configured = (runner_root / ".runner").exists()
        checks.append(
            DoctorCheck(
                "Runner registration",
                "pass" if configured else "warn",
                "이미 등록되어 있습니다." if configured else "runner 바이너리는 있지만 아직 GitHub에 등록되지 않았습니다.",
            )
        )
        return checks
    checks.append(
        DoctorCheck(
            "Self-hosted runner",
            "warn",
            f"`{runner_root}`는 찾았지만 `run.cmd` 또는 `config.cmd`가 없습니다.",
        )
    )
    return checks


def detect_runner_root() -> Path | None:
    for candidate in DEFAULT_RUNNER_ROOT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def check_target_repository(target: Path) -> list[DoctorCheck]:
    if not target.exists():
        return [DoctorCheck("Target repository", "fail", f"경로가 존재하지 않습니다: {target}")]
    if not target.is_dir():
        return [DoctorCheck("Target repository", "fail", f"디렉터리가 아닙니다: {target}")]

    checks = [DoctorCheck("Target repository", "pass", str(target))]
    issue_workflow = target / ".github/workflows/issue-comment.yml"
    review_workflow = target / ".github/workflows/pull-request-review.yml"
    review_comment_workflow = target / ".github/workflows/pull-request-review-comment.yml"
    config_file = target / ".issue-to-pr-bot.yml"

    checks.append(
        DoctorCheck(
            "Issue workflow",
            "pass" if issue_workflow.exists() else "fail",
            "설치됨" if issue_workflow.exists() else "`.github/workflows/issue-comment.yml`이 없습니다.",
        )
    )

    if review_workflow.exists() and review_comment_workflow.exists():
        checks.append(DoctorCheck("Review workflows", "pass", "두 review workflow가 모두 설치되어 있습니다."))
    elif review_workflow.exists() or review_comment_workflow.exists():
        checks.append(DoctorCheck("Review workflows", "warn", "review workflow가 일부만 설치되어 있습니다."))
    else:
        checks.append(DoctorCheck("Review workflows", "info", "선택 사항입니다. review 자동화가 필요하면 추가 설치하세요."))

    checks.append(
        DoctorCheck(
            "Repository config",
            "pass" if config_file.exists() else "info",
            "`.issue-to-pr-bot.yml` 존재" if config_file.exists() else "기본 동작에는 없어도 됩니다.",
        )
    )
    return checks


def check_repository_settings(repository: str) -> list[DoctorCheck]:
    gh_path = shutil.which("gh")
    if not gh_path:
        return [DoctorCheck("GitHub repository settings", "warn", "`gh`가 없어 저장소 변수/시크릿을 확인하지 못했습니다.")]

    variables_result = run_command(["gh", "variable", "list", "-R", repository, "--json", "name"])
    if variables_result.returncode != 0:
        return [DoctorCheck("GitHub repository settings", "warn", summarize_command_failure(variables_result))]

    secrets_result = run_command(["gh", "secret", "list", "-R", repository, "--json", "name"])
    if secrets_result.returncode != 0:
        return [DoctorCheck("GitHub repository settings", "warn", summarize_command_failure(secrets_result))]

    variable_names = extract_name_set(variables_result.stdout)
    secret_names = extract_name_set(secrets_result.stdout)
    missing_variables = sorted(set(EXPECTED_REPOSITORY_VARIABLES) - variable_names)
    missing_secrets = sorted(set(EXPECTED_REPOSITORY_SECRETS) - secret_names)
    checks: list[DoctorCheck] = []

    checks.append(
        DoctorCheck(
            "Repository variables",
            "pass" if not missing_variables else "fail",
            "모든 필수 변수 준비됨" if not missing_variables else f"누락: {', '.join(missing_variables)}",
        )
    )
    checks.append(
        DoctorCheck(
            "Repository secrets",
            "pass" if not missing_secrets else "fail",
            "모든 필수 시크릿 준비됨" if not missing_secrets else f"누락: {', '.join(missing_secrets)}",
        )
    )
    return checks


def configure_repository_settings(options: GithubConfigurationOptions) -> GithubConfigurationResult:
    if not options.bot_app_private_key_file.exists():
        raise FileNotFoundError(f"Private key file does not exist: {options.bot_app_private_key_file}")
    ensure_bootstrap_tooling(requires_github_cli=True, dry_run=options.dry_run)
    if not shutil.which("gh"):
        raise RuntimeError("`gh`가 PATH에 없습니다. GitHub CLI를 먼저 설치하고 로그인하세요.")

    support_paths = ensure_repository_support_paths(options.repository, dry_run=options.dry_run)

    operations: list[GithubConfigurationOperation] = []
    operations.append(
        run_gh_setting_command(
            ["gh", "variable", "set", "BOT_MENTION", "-R", options.repository, "--body", options.bot_mention],
            "BOT_MENTION",
            dry_run=options.dry_run,
        )
    )
    operations.append(
        run_gh_setting_command(
            ["gh", "variable", "set", "BOT_APP_ID", "-R", options.repository, "--body", options.bot_app_id],
            "BOT_APP_ID",
            dry_run=options.dry_run,
        )
    )
    operations.append(
        run_gh_setting_command(
            ["gh", "variable", "set", "BOT_CONTEXT_DIR_HOST", "-R", options.repository, "--body", str(support_paths.context_dir)],
            "BOT_CONTEXT_DIR_HOST",
            dry_run=options.dry_run,
        )
    )
    operations.append(
        run_gh_setting_command(
            ["gh", "variable", "set", "BOT_SECRETS_FILE_HOST", "-R", options.repository, "--body", str(support_paths.secrets_file)],
            "BOT_SECRETS_FILE_HOST",
            dry_run=options.dry_run,
        )
    )
    operations.append(
        run_gh_setting_command(
            ["gh", "secret", "set", "BOT_APP_PRIVATE_KEY", "-R", options.repository],
            "BOT_APP_PRIVATE_KEY",
            input_text=options.bot_app_private_key_file.read_text(encoding="utf-8"),
            dry_run=options.dry_run,
        )
    )

    return GithubConfigurationResult(
        repository=options.repository,
        operations=operations,
        next_steps=build_github_configuration_next_steps(options),
    )


def run_gh_setting_command(
    command: list[str],
    name: str,
    *,
    input_text: str | None = None,
    dry_run: bool,
) -> GithubConfigurationOperation:
    if dry_run:
        return GithubConfigurationOperation(name=name, action="would_set")

    completed = run_command(command, input_text=input_text)
    if completed.returncode != 0:
        raise RuntimeError(f"{name} 설정 실패: {summarize_command_failure(completed)}")
    return GithubConfigurationOperation(name=name, action="set")


def build_github_configuration_next_steps(options: GithubConfigurationOptions) -> list[str]:
    support_paths = build_repository_support_paths(options.repository)
    steps = [
        "GitHub App이 대상 저장소에 설치되어 있는지 확인하세요.",
        f"기본 외부 context 폴더: `{support_paths.context_dir}`",
        f"기본 secret env 파일: `{support_paths.secrets_file}`",
        "워크플로 파일이 이미 커밋되어 있으면 이슈 댓글에서 멘션 테스트를 해보세요.",
    ]
    if options.dry_run:
        steps.append("실제로 변수/시크릿을 쓰려면 같은 명령에서 `--dry-run`을 제거하세요.")
    return steps


def bootstrap_repository_environment(
    install_options: InstallManagerOptions,
    doctor_options: DoctorOptions,
    github_options: GithubConfigurationOptions | None,
) -> BootstrapResult:
    ensure_bootstrap_tooling(
        requires_github_cli=github_options is not None or doctor_options.repository is not None,
        dry_run=install_options.dry_run,
    )
    install_result = install_repository_environment(install_options)
    github_result = configure_repository_settings(github_options) if github_options is not None else None
    doctor_result = run_doctor(doctor_options)
    return BootstrapResult(
        install_result=install_result,
        doctor_result=doctor_result,
        github_result=github_result,
    )


def bootstrap_host_environment(options: HostBootstrapOptions) -> HostBootstrapResult:
    ensure_bootstrap_tooling(
        requires_github_cli=(
            options.runner_token is None
            and (options.repository is not None or options.organization is not None)
        ),
        dry_run=options.dry_run,
    )
    doctor_result = run_doctor(DoctorOptions(repository=options.repository, runner_root=options.runner_root))
    if not options.dry_run:
        ensure_host_prerequisites(doctor_result)

    operations: list[HostBootstrapOperation] = []
    runner_root = options.runner_root

    if options.dry_run:
        operations.append(HostBootstrapOperation("Runner root", "would_prepare", str(runner_root)))
    else:
        runner_root.mkdir(parents=True, exist_ok=True)
        operations.append(HostBootstrapOperation("Runner root", "prepared", str(runner_root)))

    runner_installed = (runner_root / "config.cmd").exists() and (runner_root / "run.cmd").exists()
    if runner_installed:
        operations.append(HostBootstrapOperation("Runner binaries", "skipped", "이미 runner 바이너리가 있습니다."))
    else:
        archive_source = resolve_runner_archive_source(options)
        archive_target = runner_root / archive_source.name
        if options.dry_run:
            operations.append(HostBootstrapOperation("Runner download", "would_download", archive_source.description))
            operations.append(HostBootstrapOperation("Runner extract", "would_extract", str(runner_root)))
        else:
            download_runner_archive(archive_source, archive_target)
            operations.append(HostBootstrapOperation("Runner download", "downloaded", str(archive_target)))
            extract_runner_archive(archive_target, runner_root)
            operations.append(HostBootstrapOperation("Runner extract", "extracted", str(runner_root)))

    if options.dry_run:
        operations.append(HostBootstrapOperation("Runner registration", "would_configure", build_runner_url(options)))
    else:
        config_cmd = runner_root / "config.cmd"
        if not config_cmd.exists():
            raise RuntimeError(f"`{config_cmd}`를 찾지 못했습니다. runner 압축 해제에 실패했는지 확인하세요.")
        if not is_runner_registered(runner_root):
            runner_token = options.runner_token or generate_runner_registration_token(options)
            configure_runner(runner_root, options, runner_token)
            operations.append(HostBootstrapOperation("Runner registration", "configured", build_runner_url(options)))
        else:
            operations.append(HostBootstrapOperation("Runner registration", "skipped", "이미 등록된 runner입니다."))

    if options.run_as_service:
        if options.dry_run:
            operations.append(HostBootstrapOperation("Runner service", "would_install_and_start", str(runner_root)))
        else:
            ensure_runner_service_started(runner_root)
            operations.append(HostBootstrapOperation("Runner service", "installed_and_started", str(runner_root)))
    else:
        operations.append(
            HostBootstrapOperation(
                "Runner service",
                "manual",
                "필요하면 `run.cmd`를 직접 실행하거나 `--run-as-service`로 서비스 등록을 사용하세요.",
            )
        )

    return HostBootstrapResult(
        runner_root=runner_root,
        doctor_result=doctor_result,
        operations=operations,
        next_steps=build_host_bootstrap_next_steps(options),
    )


def ensure_host_prerequisites(doctor_result: DoctorResult) -> None:
    failures = [check for check in doctor_result.checks if check.name in CRITICAL_HOST_CHECKS and check.status != "pass"]
    if not failures:
        return
    message = "; ".join(f"{check.name}: {check.detail}" for check in failures)
    raise RuntimeError(f"호스트 준비가 끝나지 않았습니다. {message}")


def resolve_runner_archive_source(options: HostBootstrapOptions) -> RunnerArchiveSource:
    if options.runner_archive is not None:
        if not options.runner_archive.exists():
            raise FileNotFoundError(f"Runner archive does not exist: {options.runner_archive}")
        return RunnerArchiveSource(
            url=options.runner_archive.as_uri(),
            name=options.runner_archive.name,
            description=f"로컬 아카이브 {options.runner_archive}",
        )

    download_url = options.runner_download_url or fetch_latest_runner_download_url()
    name = download_url.rstrip("/").split("/")[-1]
    return RunnerArchiveSource(
        url=download_url,
        name=name,
        description=download_url,
    )


def fetch_latest_runner_download_url() -> str:
    request = urllib.request.Request(
        LATEST_RUNNER_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": RUNNER_RELEASE_USER_AGENT,
        },
    )
    with urllib.request.urlopen(request) as response:
        payload = json.load(response)

    for asset in payload.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("actions-runner-win-x64-") and name.endswith(".zip"):
            browser_download_url = asset.get("browser_download_url")
            if browser_download_url:
                return browser_download_url
    raise RuntimeError("최신 Windows x64 runner 다운로드 URL을 찾지 못했습니다.")


def download_runner_archive(source: RunnerArchiveSource, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.url.startswith("file:"):
        source_path = Path(urllib.request.url2pathname(source.url.removeprefix("file:///")))
        shutil.copyfile(source_path, destination)
        return

    with urllib.request.urlopen(source.url) as response:
        destination.write_bytes(response.read())


def extract_runner_archive(archive_path: Path, runner_root: Path) -> None:
    runner_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(runner_root)


def is_runner_registered(runner_root: Path) -> bool:
    return (runner_root / ".runner").exists()


def generate_runner_registration_token(options: HostBootstrapOptions) -> str:
    if not shutil.which("gh"):
        raise RuntimeError("runner 토큰을 자동 발급하려면 `gh`가 필요합니다. 없으면 `--runner-token`을 직접 전달하세요.")

    if options.repository is not None:
        endpoint = f"repos/{options.repository}/actions/runners/registration-token"
    elif options.organization is not None:
        endpoint = f"orgs/{options.organization}/actions/runners/registration-token"
    else:
        raise RuntimeError("runner 토큰 자동 발급에는 `--repo` 또는 `--org`가 필요합니다.")

    completed = run_command(["gh", "api", "-X", "POST", endpoint, "--jq", ".token"])
    if completed.returncode != 0:
        raise RuntimeError(f"runner 토큰 발급 실패: {summarize_command_failure(completed)}")
    token = first_nonempty_line(completed.stdout)
    if not token:
        raise RuntimeError("runner 토큰 발급 결과가 비어 있습니다.")
    return token


def build_runner_url(options: HostBootstrapOptions) -> str:
    if options.runner_url:
        return options.runner_url
    if options.repository:
        return f"https://github.com/{options.repository}"
    if options.organization:
        return f"https://github.com/{options.organization}"
    raise RuntimeError("runner 등록 URL을 결정할 수 없습니다.")


def configure_runner(runner_root: Path, options: HostBootstrapOptions, runner_token: str) -> None:
    config_cmd = runner_root / "config.cmd"
    if not config_cmd.exists():
        raise RuntimeError(f"`{config_cmd}`가 없어 runner를 등록할 수 없습니다.")

    command = [
        "cmd.exe",
        "/c",
        str(config_cmd),
        "--unattended",
        "--url",
        build_runner_url(options),
        "--token",
        runner_token,
        "--name",
        options.runner_name or socket.gethostname(),
        "--work",
        options.runner_work_directory,
    ]
    if options.replace_existing:
        command.append("--replace")
    if options.runner_labels:
        command.extend(["--labels", ",".join(options.runner_labels)])

    completed = run_command(command)
    if completed.returncode != 0:
        raise RuntimeError(f"runner 등록 실패: {summarize_command_failure(completed)}")


def ensure_runner_service_started(runner_root: Path) -> None:
    service_script = runner_root / "svc.cmd"
    if not service_script.exists():
        raise RuntimeError(f"`{service_script}`가 없어 runner 서비스를 설치할 수 없습니다.")

    install_result = run_command(["cmd.exe", "/c", str(service_script), "install"])
    if install_result.returncode != 0:
        raise RuntimeError(f"runner 서비스 설치 실패: {summarize_command_failure(install_result)}")

    start_result = run_command(["cmd.exe", "/c", str(service_script), "start"])
    if start_result.returncode != 0:
        raise RuntimeError(f"runner 서비스 시작 실패: {summarize_command_failure(start_result)}")


def build_host_bootstrap_next_steps(options: HostBootstrapOptions) -> list[str]:
    steps = [
        "Docker와 Codex 로그인은 계속 호스트에 유지해야 합니다.",
    ]
    if options.run_as_service:
        steps.append("Actions 페이지에서 runner가 online으로 표시되는지 확인하세요.")
    else:
        steps.append("필요하면 runner 폴더에서 `run.cmd`를 직접 실행하세요.")
    if options.dry_run:
        steps.append("실제 구성하려면 같은 명령에서 `--dry-run`을 제거하세요.")
    return steps


def run_command(command: Sequence[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def extract_name_set(raw_json: str) -> set[str]:
    items = json.loads(raw_json or "[]")
    return {item["name"] for item in items if isinstance(item, dict) and "name" in item}


def summarize_command_failure(completed: subprocess.CompletedProcess[str]) -> str:
    message = first_nonempty_line(completed.stderr, completed.stdout)
    if message:
        return message
    return f"명령이 종료 코드 {completed.returncode}로 실패했습니다."


def first_nonempty_line(*values: str | None) -> str:
    for value in values:
        if not value:
            continue
        for line in value.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return ""


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
            lines.append(f"- `{relative_to_target(operation.path, result.target)}` -> `{operation.action}`")
    lines.extend(["", "### 다음 단계"])
    for step in result.next_steps:
        lines.append(f"- {step}")
    return "\n".join(lines)


def format_doctor_result(result: DoctorResult) -> str:
    lines = ["## 진단 결과", ""]
    for check in result.checks:
        lines.append(f"- [{check.status}] {check.name}: {check.detail}")
    return "\n".join(lines)


def format_github_configuration_result(result: GithubConfigurationResult) -> str:
    lines = [
        "## GitHub 설정 결과",
        "",
        f"- 대상 저장소: `{result.repository}`",
        "",
        "### 반영 항목",
    ]
    for operation in result.operations:
        lines.append(f"- `{operation.name}` -> `{operation.action}`")
    lines.extend(["", "### 다음 단계"])
    for step in result.next_steps:
        lines.append(f"- {step}")
    return "\n".join(lines)


def format_bootstrap_result(result: BootstrapResult) -> str:
    sections = [format_install_result(result.install_result)]
    if result.github_result is not None:
        sections.append(format_github_configuration_result(result.github_result))
    sections.append(format_doctor_result(result.doctor_result))
    return "\n\n".join(sections)


def format_host_bootstrap_result(result: HostBootstrapResult) -> str:
    lines = [
        "## 호스트 준비 결과",
        "",
        f"- runner 경로: `{result.runner_root}`",
        "",
        "### 수행 작업",
    ]
    for operation in result.operations:
        lines.append(f"- `{operation.name}` -> `{operation.action}` ({operation.detail})")
    lines.extend(["", "### 호스트 진단"])
    for check in result.doctor_result.checks:
        lines.append(f"- [{check.status}] {check.name}: {check.detail}")
    lines.extend(["", "### 다음 단계"])
    for step in result.next_steps:
        lines.append(f"- {step}")
    return "\n".join(lines)


def format_agent_bootstrap_result(result: AgentBootstrapResult) -> str:
    lines = [
        "## 로컬 agent 준비 결과",
        "",
        f"- 설정 파일: `{result.config_path}`",
        "",
        "### 파일 작업",
    ]
    if not result.operations:
        lines.append("- 없음")
    else:
        target_root = result.config_path.parent
        for operation in result.operations:
            lines.append(f"- `{relative_to_target(operation.path, target_root)}` -> `{operation.action}`")
    lines.extend(["", "### 다음 단계"])
    for step in result.next_steps:
        lines.append(f"- {step}")
    return "\n".join(lines)


def relative_to_target(path: Path, target: Path) -> str:
    if path.parent == target:
        return path.name
    return path.relative_to(target).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())


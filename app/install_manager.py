from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
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
EXPECTED_REPOSITORY_VARIABLES = ("BOT_MENTION", "BOT_APP_ID")
EXPECTED_REPOSITORY_SECRETS = ("BOT_APP_PRIVATE_KEY",)
DEFAULT_RUNNER_ROOT_CANDIDATES = (
    Path("C:/actions-runner"),
    Path("C:/actions-runner-live"),
    Path.home() / "actions-runner",
    Path.home() / "actions-runner-live",
)


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


def load_template_text(template_name: str) -> str:
    return resources.files("app.manager_templates").joinpath(template_name).read_text(encoding="utf-8")


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
    checks.append(check_runner_root(options.runner_root))
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
    if not command_path:
        status = "fail" if required else "warn"
        return DoctorCheck(name, status, f"`{executable}`가 PATH에 없습니다.")

    completed = run_command([executable, *version_args])
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
            f"`{codex_home}`에서 {', '.join(missing)} 파일을 찾지 못했습니다.",
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


def check_runner_root(explicit_runner_root: Path | None) -> DoctorCheck:
    runner_root = explicit_runner_root or detect_runner_root()
    if runner_root is None:
        return DoctorCheck(
            "Self-hosted runner",
            "warn",
            "일반적인 runner 경로를 찾지 못했습니다. `--runner-root`로 경로를 지정하면 더 정확하게 점검할 수 있습니다.",
        )

    run_cmd = runner_root / "run.cmd"
    config_cmd = runner_root / "config.cmd"
    if run_cmd.exists() or config_cmd.exists():
        return DoctorCheck("Self-hosted runner", "pass", str(runner_root))
    return DoctorCheck(
        "Self-hosted runner",
        "warn",
        f"`{runner_root}`는 찾았지만 `run.cmd` 또는 `config.cmd`가 없습니다.",
    )


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
    if not shutil.which("gh"):
        raise RuntimeError("`gh`가 PATH에 없습니다. GitHub CLI를 먼저 설치하고 로그인하세요.")

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
    steps = [
        "GitHub App이 대상 저장소에 설치되어 있는지 확인하세요.",
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
    install_result = install_repository_environment(install_options)
    github_result = configure_repository_settings(github_options) if github_options is not None else None
    doctor_result = run_doctor(doctor_options)
    return BootstrapResult(
        install_result=install_result,
        doctor_result=doctor_result,
        github_result=github_result,
    )


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


def relative_to_target(path: Path, target: Path) -> str:
    if path.parent == target:
        return path.name
    return path.relative_to(target).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())

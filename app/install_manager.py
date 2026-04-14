from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Sequence


CONFIG_TEMPLATE_NAME = ".issue-to-pr-bot.yml.example"
AGENTS_TEMPLATE_NAME = "AGENTS.md.example"
DEFAULT_AGENT_CONFIG_PATH = Path.home() / ".issue-to-pr-bot-agent" / "agent-config.json"
DEFAULT_SUPPORT_ROOT = Path.home() / "issue-to-pr-bot-data"
AUTO_INSTALL_PACKAGES = {
    "gh": {"label": "GitHub CLI", "winget": "GitHub.cli", "choco": "gh"},
    "git": {"label": "Git", "winget": "Git.Git", "choco": "git"},
    "npm": {"label": "Node.js", "winget": "OpenJS.NodeJS.LTS", "choco": "nodejs-lts"},
}
CRITICAL_CHECKS = {"Python", "Git", "Codex CLI", "Codex auth"}


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
    workspace_root: Path | None = None
    control_plane_url: str | None = None
    config_path: Path | None = None


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DoctorResult:
    checks: list[DoctorCheck]


@dataclass(frozen=True)
class TargetRepositoryOptions:
    target: Path
    write_config: bool = True
    write_agents: bool = True
    force: bool = False
    dry_run: bool = False


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
    log_path: Path = Path.home() / ".issue-to-pr-bot-agent" / "logs" / "agent.log"
    poll_interval_seconds: int = 10
    install_task: bool = True
    task_name: str = "issue-to-pr-bot-agent"
    force: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class AgentBootstrapResult:
    config_path: Path
    operations: list[FileOperation]
    next_steps: list[str]
    task_name: str | None = None


@dataclass(frozen=True)
class ControlPlaneBootstrapOptions:
    target: Path
    worker_name: str
    bot_mention: str
    github_app_id: str
    github_app_private_key_file: Path
    agent_token: str | None = None
    webhook_secret: str | None = None
    force: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class ControlPlaneBootstrapResult:
    install_result: InstallManagerResult
    operations: list[str]
    worker_url: str | None
    agent_token: str
    webhook_secret: str


@dataclass(frozen=True)
class BootstrapAllOptions:
    control_plane: ControlPlaneBootstrapOptions
    agent: AgentBootstrapOptions
    target_repository: TargetRepositoryOptions


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "doctor":
            result = run_doctor(build_doctor_options(args))
            print(format_doctor_result(result))
            return 0
        if args.command == "init-target-repo":
            result = init_target_repository(build_target_repository_options(args))
            print(format_install_result(result))
            return 0
        if args.command == "init-control-plane":
            result = init_control_plane_environment(build_control_plane_options(args))
            print(format_install_result(result))
            return 0
        if args.command == "bootstrap-control-plane":
            result = bootstrap_control_plane_environment(build_control_plane_bootstrap_options(args))
            print(format_control_plane_bootstrap_result(result))
            return 0
        if args.command == "bootstrap-agent":
            result = bootstrap_agent_environment(build_agent_bootstrap_options(args))
            print(format_agent_bootstrap_result(result))
            return 0
        if args.command == "bootstrap-all":
            result = bootstrap_all_environment(build_bootstrap_all_options(args))
            print(format_bootstrap_all_result(result))
            return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue-to-pr-bot-manager",
        description="Cloudflare Worker + 로컬 agent 구조를 설치하고 운영하는 중앙 매니저입니다.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="현재 PC와 설정이 실행 가능한 상태인지 점검합니다.",
    )
    add_doctor_arguments(doctor_parser)

    init_target_parser = subparsers.add_parser(
        "init-target-repo",
        help="대상 저장소에 최소 봇 설정 파일을 생성합니다.",
    )
    add_target_repository_arguments(init_target_parser)

    control_plane_parser = subparsers.add_parser(
        "init-control-plane",
        help="Cloudflare Worker 제어면 프로젝트 뼈대를 생성합니다.",
    )
    control_plane_parser.add_argument("--target", required=True, help="Worker 프로젝트를 생성할 경로입니다.")
    control_plane_parser.add_argument("--worker-name", required=True, help="Cloudflare Worker 이름입니다.")
    control_plane_parser.add_argument("--bot-mention", default="@incle-issue-to-pr-bot", help="댓글에서 사용할 봇 멘션입니다.")
    control_plane_parser.add_argument("--force", action="store_true")
    control_plane_parser.add_argument("--dry-run", action="store_true")

    bootstrap_control_plane_parser = subparsers.add_parser(
        "bootstrap-control-plane",
        help="제어면 프로젝트 생성부터 secret 등록, 배포까지 한 번에 진행합니다.",
    )
    add_control_plane_bootstrap_arguments(bootstrap_control_plane_parser)

    agent_parser = subparsers.add_parser(
        "bootstrap-agent",
        help="로컬 polling agent 설정 파일을 생성합니다.",
    )
    agent_parser.add_argument("--control-plane-url", required=True, help="배포된 제어면 Worker URL입니다.")
    agent_parser.add_argument("--agent-token", required=True, help="제어면과 통신할 때 사용할 agent 토큰입니다.")
    agent_parser.add_argument("--repository", action="append", dest="repositories", default=[], help="agent가 처리할 GitHub 저장소입니다. 여러 번 지정할 수 있습니다.")
    agent_parser.add_argument("--workspace-root", default=str(DEFAULT_SUPPORT_ROOT / "agent-workspaces"), help="agent가 작업용 저장소를 내려받을 루트 경로입니다.")
    agent_parser.add_argument("--config-path", default=str(DEFAULT_AGENT_CONFIG_PATH), help="agent 설정 파일을 저장할 경로입니다.")
    agent_parser.add_argument("--log-path", default=str(Path.home() / ".issue-to-pr-bot-agent" / "logs" / "agent.log"), help="agent 로그 파일 경로입니다.")
    agent_parser.add_argument("--poll-interval-seconds", type=int, default=10, help="제어면을 다시 조회할 주기(초)입니다.")
    agent_parser.add_argument("--skip-task", action="store_true", help="Windows 작업 스케줄러 등록을 건너뜁니다.")
    agent_parser.add_argument("--task-name", default="issue-to-pr-bot-agent", help="작업 스케줄러에 등록할 작업 이름입니다.")
    agent_parser.add_argument("--force", action="store_true")
    agent_parser.add_argument("--dry-run", action="store_true")

    bootstrap_all_parser = subparsers.add_parser(
        "bootstrap-all",
        help="제어면, 로컬 agent, 대상 저장소 초기화를 한 번에 진행합니다.",
    )
    add_control_plane_bootstrap_arguments(bootstrap_all_parser)
    bootstrap_all_parser.add_argument("--repository", action="append", dest="repositories", default=[], help="agent가 처리할 GitHub 저장소입니다. 여러 번 지정할 수 있습니다.")
    bootstrap_all_parser.add_argument("--workspace-root", default=str(DEFAULT_SUPPORT_ROOT / "agent-workspaces"), help="agent가 작업용 저장소를 내려받을 루트 경로입니다.")
    bootstrap_all_parser.add_argument("--config-path", default=str(DEFAULT_AGENT_CONFIG_PATH), help="agent 설정 파일을 저장할 경로입니다.")
    bootstrap_all_parser.add_argument("--log-path", default=str(Path.home() / ".issue-to-pr-bot-agent" / "logs" / "agent.log"), help="agent 로그 파일 경로입니다.")
    bootstrap_all_parser.add_argument("--poll-interval-seconds", type=int, default=10, help="제어면을 다시 조회할 주기(초)입니다.")
    bootstrap_all_parser.add_argument("--skip-task", action="store_true", help="Windows 작업 스케줄러 등록을 건너뜁니다.")
    bootstrap_all_parser.add_argument("--task-name", default="issue-to-pr-bot-agent", help="작업 스케줄러에 등록할 작업 이름입니다.")
    bootstrap_all_parser.add_argument("--target-repo", required=True, help="초기화할 대상 저장소 루트 경로입니다.")
    bootstrap_all_parser.add_argument("--skip-config", action="store_true")
    bootstrap_all_parser.add_argument("--skip-agents", action="store_true")

    return parser


def add_doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", help="추가로 점검할 대상 저장소 루트 경로입니다.")
    parser.add_argument("--workspace-root", help="추가로 점검할 로컬 agent 작업 루트 경로입니다.")
    parser.add_argument("--control-plane-url", help="추가로 점검할 제어면 Worker URL입니다.")
    parser.add_argument("--config-path", help="추가로 점검할 agent 설정 파일 경로입니다.")


def add_target_repository_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", required=True, help="대상 저장소 루트 경로입니다.")
    parser.add_argument("--skip-config", action="store_true", help="`.issue-to-pr-bot.yml` 파일 생성을 건너뜁니다.")
    parser.add_argument("--skip-agents", action="store_true", help="`AGENTS.md` 파일 생성을 건너뜁니다.")
    parser.add_argument("--force", action="store_true", help="기존 관리 파일이 있어도 덮어씁니다.")
    parser.add_argument("--dry-run", action="store_true", help="실제 파일을 쓰지 않고 변경 예정 내용만 출력합니다.")


def add_control_plane_bootstrap_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", required=True, help="제어면 프로젝트 루트 경로입니다.")
    parser.add_argument("--worker-name", required=True, help="Cloudflare Worker 이름입니다.")
    parser.add_argument("--bot-mention", default="@incle-issue-to-pr-bot", help="댓글에서 사용할 봇 멘션입니다.")
    parser.add_argument("--github-app-id", required=True, help="GitHub App ID입니다.")
    parser.add_argument("--github-app-private-key-file", required=True, help="GitHub App PEM 파일 경로입니다.")
    parser.add_argument("--agent-token", help="고정 agent 토큰이 필요하면 직접 지정합니다. 비우면 자동 생성합니다.")
    parser.add_argument("--webhook-secret", help="고정 webhook secret이 필요하면 직접 지정합니다. 비우면 자동 생성합니다.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def build_doctor_options(args: argparse.Namespace) -> DoctorOptions:
    return DoctorOptions(
        target=Path(args.target).resolve() if getattr(args, "target", None) else None,
        workspace_root=Path(args.workspace_root).resolve() if getattr(args, "workspace_root", None) else None,
        control_plane_url=getattr(args, "control_plane_url", None),
        config_path=Path(args.config_path).resolve() if getattr(args, "config_path", None) else None,
    )


def build_target_repository_options(args: argparse.Namespace) -> TargetRepositoryOptions:
    return TargetRepositoryOptions(
        target=Path(args.target).resolve(),
        write_config=not args.skip_config,
        write_agents=not args.skip_agents,
        force=args.force,
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


def build_control_plane_bootstrap_options(args: argparse.Namespace) -> ControlPlaneBootstrapOptions:
    return ControlPlaneBootstrapOptions(
        target=Path(args.target).resolve(),
        worker_name=args.worker_name,
        bot_mention=args.bot_mention,
        github_app_id=args.github_app_id,
        github_app_private_key_file=Path(args.github_app_private_key_file).resolve(),
        agent_token=getattr(args, "agent_token", None),
        webhook_secret=getattr(args, "webhook_secret", None),
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
        log_path=Path(getattr(args, "log_path", Path.home() / ".issue-to-pr-bot-agent" / "logs" / "agent.log")).resolve(),
        poll_interval_seconds=args.poll_interval_seconds,
        install_task=not getattr(args, "skip_task", False),
        task_name=getattr(args, "task_name", "issue-to-pr-bot-agent"),
        force=args.force,
        dry_run=args.dry_run,
    )


def build_bootstrap_all_options(args: argparse.Namespace) -> BootstrapAllOptions:
    control_plane = build_control_plane_bootstrap_options(args)
    agent = AgentBootstrapOptions(
        control_plane_url=f"https://{control_plane.worker_name}.workers.dev",
        agent_token=args.agent_token or "",
        repositories=list(args.repositories),
        workspace_root=Path(args.workspace_root).resolve(),
        config_path=Path(args.config_path).resolve(),
        log_path=Path(getattr(args, "log_path", Path.home() / ".issue-to-pr-bot-agent" / "logs" / "agent.log")).resolve(),
        poll_interval_seconds=args.poll_interval_seconds,
        install_task=not getattr(args, "skip_task", False),
        task_name=getattr(args, "task_name", "issue-to-pr-bot-agent"),
        force=args.force,
        dry_run=args.dry_run,
    )
    target_repository = TargetRepositoryOptions(
        target=Path(args.target_repo).resolve(),
        write_config=not args.skip_config,
        write_agents=not args.skip_agents,
        force=args.force,
        dry_run=args.dry_run,
    )
    return BootstrapAllOptions(
        control_plane=control_plane,
        agent=agent,
        target_repository=target_repository,
    )


def init_target_repository(options: TargetRepositoryOptions) -> InstallManagerResult:
    ensure_target_exists(options.target)
    operations: list[FileOperation] = []

    if options.write_config:
        config_target_path = options.target / ".issue-to-pr-bot.yml"
        action = write_managed_file(
            config_target_path,
            load_manager_template_text(CONFIG_TEMPLATE_NAME),
            force=options.force,
            dry_run=options.dry_run,
        )
        operations.append(FileOperation(path=config_target_path, action=action))

    if options.write_agents:
        agents_target_path = options.target / "AGENTS.md"
        action = write_managed_file(
            agents_target_path,
            load_manager_template_text(AGENTS_TEMPLATE_NAME),
            force=options.force,
            dry_run=options.dry_run,
        )
        operations.append(FileOperation(path=agents_target_path, action=action))

    return InstallManagerResult(
        target=options.target,
        operations=operations,
        next_steps=[
            "GitHub App이 대상 저장소에 설치되어 있는지 확인하세요.",
            "Cloudflare Worker webhook URL이 GitHub App에 연결되어 있는지 확인하세요.",
            "로컬 agent가 이 저장소를 처리하도록 `bootstrap-agent`에 같은 저장소를 넣으세요.",
            "규칙은 `AGENTS.md`와 `.issue-to-pr-bot.yml`에만 최소한으로 적으세요.",
        ],
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
        rendered = render_text_template(load_worker_template_text(template_name), replacements)
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
            "한 번에 진행하려면 `bootstrap-control-plane`을 사용하세요.",
            "수동으로 할 때는 Worker 디렉터리에서 `npm install`을 실행하세요.",
            "Cloudflare KV namespace를 만들고 `wrangler.jsonc`의 TASK_QUEUE id를 채우세요.",
            "`npx wrangler secret put CONTROL_PLANE_AGENT_TOKEN` 등 필수 secret을 넣으세요.",
            "`npx wrangler deploy`로 배포하세요.",
        ],
    )


def bootstrap_control_plane_environment(options: ControlPlaneBootstrapOptions) -> ControlPlaneBootstrapResult:
    if not options.github_app_private_key_file.exists():
        raise FileNotFoundError(f"GitHub App private key file not found: {options.github_app_private_key_file}")

    install_result = init_control_plane_environment(
        ControlPlaneOptions(
            target=options.target,
            worker_name=options.worker_name,
            bot_mention=options.bot_mention,
            force=options.force,
            dry_run=options.dry_run,
        )
    )
    generated_agent_token = options.agent_token or secrets.token_urlsafe(24)
    generated_webhook_secret = options.webhook_secret or secrets.token_urlsafe(32)

    operations: list[str] = []
    if options.dry_run:
        operations.extend(
            [
                "would_run: npm install",
            f"would_run: {' '.join(wrangler_command_prefix())} kv namespace create TASK_QUEUE --config wrangler.jsonc --update-config",
                "would_set: CONTROL_PLANE_AGENT_TOKEN",
                "would_set: GITHUB_WEBHOOK_SECRET",
                "would_set: GITHUB_APP_ID",
                "would_set: GITHUB_APP_PRIVATE_KEY",
            f"would_run: {' '.join(wrangler_command_prefix())} deploy --config wrangler.jsonc",
            ]
        )
        return ControlPlaneBootstrapResult(
            install_result=install_result,
            operations=operations,
            worker_url=f"https://{options.worker_name}.workers.dev",
            agent_token=generated_agent_token,
            webhook_secret=generated_webhook_secret,
        )

    ensure_command_available("npm")
    ensure_command_available("npx")

    run_checked_command(["npm", "install"], cwd=options.target)
    operations.append("ran: npm install")

    ensure_task_queue_namespace(options.target)
    operations.append("ran: wrangler kv namespace create TASK_QUEUE")

    run_wrangler_secret_put(options.target, "CONTROL_PLANE_AGENT_TOKEN", generated_agent_token)
    run_wrangler_secret_put(options.target, "GITHUB_WEBHOOK_SECRET", generated_webhook_secret)
    run_wrangler_secret_put(options.target, "GITHUB_APP_ID", options.github_app_id)
    run_wrangler_secret_put(
        options.target,
        "GITHUB_APP_PRIVATE_KEY",
        options.github_app_private_key_file.read_text(encoding="utf-8"),
    )
    operations.extend(
        [
            "set: CONTROL_PLANE_AGENT_TOKEN",
            "set: GITHUB_WEBHOOK_SECRET",
            "set: GITHUB_APP_ID",
            "set: GITHUB_APP_PRIVATE_KEY",
        ]
    )

    deploy_output = run_checked_command([*wrangler_command_prefix(), "deploy", "--config", "wrangler.jsonc"], cwd=options.target)
    operations.append("ran: wrangler deploy")
    worker_url = extract_worker_url(deploy_output) or f"https://{options.worker_name}.workers.dev"

    return ControlPlaneBootstrapResult(
        install_result=install_result,
        operations=operations,
        worker_url=worker_url,
        agent_token=generated_agent_token,
        webhook_secret=generated_webhook_secret,
    )


def bootstrap_agent_environment(options: AgentBootstrapOptions) -> AgentBootstrapResult:
    if not options.repositories:
        raise ValueError("--repository를 하나 이상 지정해야 합니다.")

    if not options.dry_run:
        options.workspace_root.mkdir(parents=True, exist_ok=True)
        options.config_path.parent.mkdir(parents=True, exist_ok=True)
        options.log_path.parent.mkdir(parents=True, exist_ok=True)

    config_body = json.dumps(
        {
            "control_plane_url": options.control_plane_url,
            "agent_token": options.agent_token,
            "repositories": options.repositories,
            "workspace_root": str(options.workspace_root),
            "log_path": str(options.log_path),
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
    task_status = register_agent_scheduled_task(options)
    next_steps = [
        "Codex 로그인 상태 확인",
        f"로그 확인: `{options.log_path}`",
    ]
    if options.install_task:
        next_steps.append(f"Windows 작업 스케줄러 작업: `{options.task_name}` ({task_status})")
        next_steps.append(f"즉시 실행: `schtasks /Run /TN \"{options.task_name}\"`")
    else:
        next_steps.append(f"`issue-to-pr-bot-agent start --config {options.config_path}` 로 agent 시작")
    next_steps.append(f"`issue-to-pr-bot-agent status --config {options.config_path}` 로 상태 확인")

    return AgentBootstrapResult(
        config_path=options.config_path,
        operations=[FileOperation(path=options.config_path, action=action)],
        next_steps=next_steps,
        task_name=options.task_name if options.install_task else None,
    )


def bootstrap_all_environment(options: BootstrapAllOptions) -> tuple[ControlPlaneBootstrapResult, AgentBootstrapResult, InstallManagerResult]:
    control_plane_result = bootstrap_control_plane_environment(options.control_plane)
    agent_options = AgentBootstrapOptions(
        control_plane_url=control_plane_result.worker_url or f"https://{options.control_plane.worker_name}.workers.dev",
        agent_token=control_plane_result.agent_token,
        repositories=options.agent.repositories,
        workspace_root=options.agent.workspace_root,
        config_path=options.agent.config_path,
        log_path=options.agent.log_path,
        poll_interval_seconds=options.agent.poll_interval_seconds,
        install_task=options.agent.install_task,
        task_name=options.agent.task_name,
        force=options.agent.force,
        dry_run=options.agent.dry_run,
    )
    agent_result = bootstrap_agent_environment(agent_options)
    target_result = init_target_repository(options.target_repository)
    return control_plane_result, agent_result, target_result


def run_doctor(options: DoctorOptions) -> DoctorResult:
    checks: list[DoctorCheck] = []
    checks.append(probe_python())
    checks.append(probe_command("Git", "git", ["--version"]))
    checks.append(probe_command("GitHub CLI", "gh", ["--version"], optional=True))
    checks.append(probe_command("Codex CLI", "codex", ["--version"]))
    checks.append(check_codex_auth())

    if options.control_plane_url:
        checks.append(check_control_plane_url(options.control_plane_url))
    if options.workspace_root:
        checks.append(check_workspace_root(options.workspace_root))
    if options.config_path:
        checks.append(check_agent_config(options.config_path))
    if options.target:
        checks.extend(check_target_repository(options.target))

    return DoctorResult(checks=checks)


def probe_python() -> DoctorCheck:
    command = probe_command("Python", "python", ["--version"], optional=True)
    if command.status == "pass":
        return command
    current_python = run_command([sys.executable, "--version"])
    if current_python.returncode == 0:
        return DoctorCheck("Python", "pass", first_nonempty_line(current_python.stdout, current_python.stderr))
    return command


def probe_command(name: str, executable: str, args: list[str], *, optional: bool = False) -> DoctorCheck:
    path = shutil.which(executable)
    if not path:
        status = "warn" if optional else "fail"
        return DoctorCheck(name, status, f"`{executable}`를 찾지 못했습니다.")

    completed = run_command([executable, *args])
    if completed.returncode != 0:
        status = "warn" if optional else "fail"
        return DoctorCheck(name, status, summarize_command_failure(completed))
    return DoctorCheck(name, "pass", first_nonempty_line(completed.stdout, completed.stderr))


def check_codex_auth() -> DoctorCheck:
    codex_home = resolve_codex_home()
    auth_path = codex_home / "auth.json"
    config_path = codex_home / "config.toml"
    if auth_path.exists() and config_path.exists():
        return DoctorCheck("Codex auth", "pass", str(codex_home))
    missing: list[str] = []
    if not auth_path.exists():
        missing.append(str(auth_path))
    if not config_path.exists():
        missing.append(str(config_path))
    return DoctorCheck("Codex auth", "fail", f"누락: {', '.join(missing)}")


def ensure_command_available(executable: str) -> None:
    if shutil.which(executable):
        return
    if auto_install_command_if_missing(executable):
        return
    raise RuntimeError(f"`{executable}`를 찾지 못했습니다. 먼저 설치하거나 PATH를 확인하세요.")


def check_control_plane_url(url: str) -> DoctorCheck:
    if url.startswith("https://") or url.startswith("http://"):
        return DoctorCheck("Control plane URL", "pass", url)
    return DoctorCheck("Control plane URL", "fail", "http:// 또는 https:// 로 시작해야 합니다.")


def check_workspace_root(path: Path) -> DoctorCheck:
    if path.exists() and path.is_dir():
        return DoctorCheck("Agent workspace root", "pass", str(path))
    return DoctorCheck("Agent workspace root", "info", f"아직 없으면 agent bootstrap 때 생성됩니다: {path}")


def check_agent_config(path: Path) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck("Agent config", "info", f"아직 없으면 bootstrap-agent 때 생성됩니다: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return DoctorCheck("Agent config", "fail", f"JSON 파싱 실패: {exc}")
    required = {"control_plane_url", "agent_token", "repositories", "workspace_root"}
    missing = sorted(required - set(payload))
    if missing:
        return DoctorCheck("Agent config", "fail", f"누락 키: {', '.join(missing)}")
    return DoctorCheck("Agent config", "pass", str(path))


def check_target_repository(target: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    config_file = target / ".issue-to-pr-bot.yml"
    agents_file = target / "AGENTS.md"
    checks.append(
        DoctorCheck(
            ".issue-to-pr-bot.yml",
            "pass" if config_file.exists() else "info",
            "존재" if config_file.exists() else "없어도 기본 동작은 가능합니다.",
        )
    )
    checks.append(
        DoctorCheck(
            "AGENTS.md",
            "pass" if agents_file.exists() else "info",
            "존재" if agents_file.exists() else "없으면 기본 규칙으로 동작합니다.",
        )
    )
    return checks


def load_manager_template_text(template_name: str) -> str:
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


def write_managed_file(path: Path, content: str, *, force: bool, dry_run: bool) -> str:
    exists = path.exists()
    if exists and not force:
        return "skipped"
    if dry_run:
        return "would_overwrite" if exists else "would_create"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "overwritten" if exists else "created"


def build_agent_launch_command(config_path: Path) -> str:
    pythonw_path = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw_path if pythonw_path.exists() else Path(sys.executable)
    return f'"{interpreter}" -m app.agent_runner serve --config "{config_path}"'


def register_agent_scheduled_task(options: AgentBootstrapOptions) -> str:
    if not options.install_task:
        return "skipped"
    if os.name != "nt":
        return "unsupported"
    if options.dry_run:
        return "would_create"

    command = build_agent_launch_command(options.config_path)
    try:
        run_checked_command(
            [
                "schtasks",
                "/Create",
                "/TN",
                options.task_name,
                "/SC",
                "ONLOGON",
                "/RL",
                "LIMITED",
                "/TR",
                command,
                "/F",
            ],
            cwd=options.config_path.parent,
        )
    except RuntimeError as exc:
        return f"failed: {exc}"
    return "created"


def resolve_codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))


def run_command(command: Sequence[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    resolved = resolve_subprocess_command(command)
    return subprocess.run(
        resolved,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def run_checked_command(command: Sequence[str], *, cwd: Path, input_text: str | None = None) -> str:
    resolved = resolve_subprocess_command(command)
    completed = subprocess.run(
        resolved,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} 실패: {summarize_command_failure(completed)}")
    return "\n".join(part for part in [completed.stdout, completed.stderr] if part)


def run_wrangler_secret_put(target: Path, name: str, value: str) -> None:
    run_checked_command(
        [*wrangler_command_prefix(), "secret", "put", name, "--config", "wrangler.jsonc"],
        cwd=target,
        input_text=value,
    )


def wrangler_command_prefix() -> list[str]:
    if shutil.which("npx"):
        return ["npx", "wrangler"]
    return ["npm", "exec", "--", "wrangler"]


def ensure_task_queue_namespace(target: Path) -> None:
    command = [*wrangler_command_prefix(), "kv", "namespace", "create", "TASK_QUEUE", "--config", "wrangler.jsonc", "--update-config"]
    try:
        run_checked_command(command, cwd=target)
        return
    except RuntimeError as exc:
        if "already exists" not in str(exc):
            raise

    listed = run_checked_command([*wrangler_command_prefix(), "kv", "namespace", "list", "--config", "wrangler.jsonc"], cwd=target)
    namespaces = json.loads(listed)
    existing = next((item for item in namespaces if item.get("title") == "TASK_QUEUE"), None)
    if not existing or not existing.get("id"):
        raise RuntimeError("기존 TASK_QUEUE KV namespace를 찾지 못했습니다.")
    update_kv_namespace_binding(target / "wrangler.jsonc", existing["id"])


def update_kv_namespace_binding(config_path: Path, namespace_id: str) -> None:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["kv_namespaces"] = [{"binding": "TASK_QUEUE", "id": namespace_id}]
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_subprocess_command(command: Sequence[str]) -> list[str]:
    resolved = list(command)
    executable_path = shutil.which(resolved[0])
    if executable_path:
        resolved[0] = executable_path
    return resolved


def extract_worker_url(output: str) -> str | None:
    match = re.search(r"https://[a-zA-Z0-9.-]+\.workers\.dev", output)
    if match:
        return match.group(0)
    return None


def detect_package_manager() -> str | None:
    if shutil.which("winget"):
        return "winget"
    if shutil.which("choco"):
        return "choco"
    return None


def refresh_process_path() -> None:
    return


def auto_install_command_if_missing(executable: str) -> bool:
    if shutil.which(executable):
        return True
    package = AUTO_INSTALL_PACKAGES.get(executable)
    if not package:
        return False
    manager = detect_package_manager()
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
    elif manager == "choco":
        command = ["choco", "install", package["choco"], "-y"]
    else:
        return False
    print(f"{package['label']} 자동 설치 시도: {' '.join(command)}")
    result = run_command(command)
    refresh_process_path()
    return result.returncode == 0 and shutil.which(executable) is not None


def first_nonempty_line(*values: str | None) -> str:
    for value in values:
        if not value:
            continue
        for line in value.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return ""


def summarize_command_failure(completed: subprocess.CompletedProcess[str]) -> str:
    message = first_nonempty_line(completed.stderr, completed.stdout)
    if message:
        return message
    return f"명령이 종료 코드 {completed.returncode}로 실패했습니다."


def relative_to_target(path: Path, target: Path) -> str:
    if path.parent == target:
        return path.name
    return path.relative_to(target).as_posix()


def try_extract_log_path(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    log_path = payload.get("log_path")
    return str(log_path) if log_path else None


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


def format_agent_bootstrap_result(result: AgentBootstrapResult) -> str:
    lines = [
        "## 로컬 agent 준비 결과",
        "",
        f"- 설정 파일: `{result.config_path}`",
        f"- 로그 파일: `{try_extract_log_path(result.config_path) or '설정 파일 생성 후 확인'}`",
    ]
    if result.task_name:
        lines.append(f"- 작업 스케줄러: `{result.task_name}`")
    lines.extend(
        [
        "",
        "### 파일 작업",
        ]
    )
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


def format_doctor_result(result: DoctorResult) -> str:
    lines = ["## 진단 결과", ""]
    for check in result.checks:
        lines.append(f"- [{check.status}] {check.name}: {check.detail}")
    return "\n".join(lines)


def format_control_plane_bootstrap_result(result: ControlPlaneBootstrapResult) -> str:
    lines = [
        format_install_result(result.install_result),
        "",
        "## 제어면 배포 결과",
        "",
        f"- Worker URL: `{result.worker_url or 'unknown'}`",
        "- 수행 작업:",
    ]
    for operation in result.operations:
        lines.append(f"  - {operation}")
    lines.extend(
        [
            "",
            "### 저장해둘 값",
            f"- Agent token: `{result.agent_token}`",
            f"- Webhook secret: `{result.webhook_secret}`",
            f"- GitHub App webhook URL: `{(result.worker_url or 'https://<worker>.workers.dev')}/github/webhook`",
        ]
    )
    return "\n".join(lines)


def format_bootstrap_all_result(result: tuple[ControlPlaneBootstrapResult, AgentBootstrapResult, InstallManagerResult]) -> str:
    control_plane_result, agent_result, target_result = result
    return "\n\n".join(
        [
            format_control_plane_bootstrap_result(control_plane_result),
            format_agent_bootstrap_result(agent_result),
            format_install_result(target_result),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())

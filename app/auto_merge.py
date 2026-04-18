import os
import subprocess
import urllib.parse
from pathlib import Path

from app.bot import BotRuntimeOptions, IssueRequest, build_codex_commit_message
from app.codex_runner import (
    build_post_sync_prompt,
    log_base_sync_result,
    run_codex,
    should_rerun_codex_after_sync,
)
from app.config import BOT_MENTION, BotConfig, GitSyncRule, get_git_sync_rule
from app.github_pr import (
    MergeRequestResult,
    create_issue_comment,
    ensure_no_protected_changes,
    get_pull_request,
    get_staged_files,
    get_workspace_changed_files,
    github_request,
    has_staged_changes,
    has_unmerged_paths,
    push_branch,
    request_pull_request_merge,
    run_git,
    try_requested_auto_merge_pull_request,
    checkout_pull_request_branch,
    apply_base_sync_strategy,
    unstage_output_artifacts,
)
from app.prompting import PreparedPrompt, prepare_prompt
from app.verification import resolve_verification_plan, run_verification


def handle_pull_request_review_event(
    payload: dict,
    workspace: Path | None = None,
    config: BotConfig | None = None,
) -> None:
    handle_auto_merge_event(payload, workspace=workspace, config=config)


def handle_auto_merge_event(
    payload: dict,
    workspace: Path | None = None,
    config: BotConfig | None = None,
) -> None:
    repository = (payload.get("repository") or {}).get("full_name") or os.getenv("GITHUB_REPOSITORY")

    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("auto-merge를 실행하려면 GitHub token이 필요합니다.")

    pull_request_numbers = extract_target_pull_request_numbers(payload, repository, token)

    if not repository or not pull_request_numbers:
        print("auto-merge 대상 PR 정보를 찾지 못해 건너뜁니다.")
        return

    if not should_attempt_auto_merge(payload):
        return

    for pull_request_number in pull_request_numbers:
        merge_sha = attempt_auto_merge(repository, pull_request_number, payload, token, workspace, config)
        if not merge_sha:
            continue
        body = "\n".join(
            [
                "## 자동 머지 결과",
                "",
                "- 상태: `merged`",
                f"- PR: #{pull_request_number}",
                f"- 머지 커밋: `{merge_sha}`",
            ]
        )
        create_issue_comment(repository, pull_request_number, body, token=token)


def attempt_auto_merge(
    repository: str,
    pull_request_number: int,
    payload: dict,
    token: str,
    workspace: Path | None,
    config: BotConfig | None,
) -> str | None:
    if workspace and config:
        maybe_prepare_pull_request_for_merge(workspace, config, repository, pull_request_number, token)

    if payload.get("review") and payload.get("pull_request"):
        result = request_pull_request_merge_with_conflict_recovery(
            repository,
            pull_request_number,
            token,
            workspace=workspace,
            config=config,
        )
        return result.merge_sha
    return try_requested_auto_merge_pull_request_with_conflict_recovery(
        repository,
        pull_request_number,
        token,
        workspace=workspace,
        config=config,
    )


def maybe_prepare_pull_request_for_merge(
    workspace: Path,
    config: BotConfig,
    repository: str,
    pull_request_number: int,
    token: str,
) -> None:
    rule = get_git_sync_rule(config, "before_merge")
    if not rule or rule.confidence not in {"high", "explicit"}:
        return
    _prepare_pull_request_branch_for_merge(
        workspace,
        config,
        repository,
        pull_request_number,
        token,
        rule=rule,
        rerun_reason="merge 전 base sync 이후 상태를 맞추기 위해 Codex를 한 번 더 실행합니다.",
    )


def request_pull_request_merge_with_conflict_recovery(
    repository: str,
    pull_request_number: int,
    token: str,
    *,
    workspace: Path | None,
    config: BotConfig | None,
) -> MergeRequestResult:
    result = request_pull_request_merge(repository, pull_request_number, token)
    if result.merge_sha or workspace is None or config is None:
        return result
    if not pull_request_has_merge_conflicts(repository, pull_request_number, token):
        return result

    print("GitHub merge가 conflict 상태로 막혀 Codex에게 정리 후 다시 merge를 시도합니다.")
    recover_pull_request_merge_conflicts_with_codex(workspace, config, repository, pull_request_number, token)
    retry_result = request_pull_request_merge(repository, pull_request_number, token)
    if not retry_result.merge_sha and pull_request_has_merge_conflicts(repository, pull_request_number, token):
        raise RuntimeError("Codex가 merge conflict를 정리한 뒤에도 PR이 여전히 conflicted 상태라 merge를 완료하지 못했습니다.")
    return retry_result


def try_requested_auto_merge_pull_request_with_conflict_recovery(
    repository: str,
    pull_request_number: int,
    token: str,
    *,
    workspace: Path | None,
    config: BotConfig | None,
) -> str | None:
    merge_sha = try_requested_auto_merge_pull_request(repository, pull_request_number, token)
    if merge_sha or workspace is None or config is None:
        return merge_sha
    if not pull_request_has_merge_conflicts(repository, pull_request_number, token):
        return None

    print("GitHub auto-merge가 conflict 상태로 막혀 Codex에게 정리 후 다시 merge를 시도합니다.")
    recover_pull_request_merge_conflicts_with_codex(workspace, config, repository, pull_request_number, token)
    retry_merge_sha = try_requested_auto_merge_pull_request(repository, pull_request_number, token)
    if retry_merge_sha is None and pull_request_has_merge_conflicts(repository, pull_request_number, token):
        raise RuntimeError("Codex가 merge conflict를 정리한 뒤에도 PR이 여전히 conflicted 상태라 auto-merge를 완료하지 못했습니다.")
    return retry_merge_sha


def pull_request_has_merge_conflicts(repository: str, pull_request_number: int, token: str) -> bool:
    pull_request = get_pull_request(repository, pull_request_number, token)
    mergeable_state = str(pull_request.get("mergeable_state") or "").strip().lower()
    if mergeable_state == "dirty":
        return True
    return False


def recover_pull_request_merge_conflicts_with_codex(
    workspace: Path,
    config: BotConfig,
    repository: str,
    pull_request_number: int,
    token: str,
) -> None:
    pull_request = get_pull_request(repository, pull_request_number, token)
    base_branch = ((pull_request.get("base") or {}).get("ref") or "").strip() or "main"
    configured_rule = get_git_sync_rule(config, "before_merge")
    if configured_rule and configured_rule.confidence in {"high", "explicit"}:
        rule = configured_rule
    else:
        rule = GitSyncRule(
            phase="before_merge",
            action="merge",
            base_branch=base_branch,
            require_conflict_free=True,
            confidence="wrapper",
            source="merge-conflict-recovery",
        )
    _prepare_pull_request_branch_for_merge(
        workspace,
        config,
        repository,
        pull_request_number,
        token,
        rule=rule,
        prompt_suffix_lines=[
            "- GitHub merge was blocked because the PR is currently in a conflicted merge state.",
            "- Resolve the merge conflicts against the current base branch in the worktree, keep the intended behavior, and leave the branch ready to merge.",
        ],
        rerun_reason="merge conflict를 정리하고 다시 merge할 수 있게 Codex를 한 번 더 실행합니다.",
    )


def _prepare_pull_request_branch_for_merge(
    workspace: Path,
    config: BotConfig,
    repository: str,
    pull_request_number: int,
    token: str,
    *,
    rule: GitSyncRule,
    prompt_suffix_lines: list[str] | None = None,
    rerun_reason: str,
) -> None:
    pull_request = get_pull_request(repository, pull_request_number, token)
    request = build_merge_request_from_pull_request(repository, pull_request)
    target = checkout_pull_request_branch(request, workspace)
    before_head = get_head_sha(workspace)

    print(
        "문서 기반 merge 전 Git workflow 규칙을 적용합니다. "
        f"phase={rule.phase}, action={rule.action}, base={target.base_branch}, confidence={rule.confidence}"
    )
    sync_result = apply_base_sync_strategy(
        workspace,
        target.base_branch,
        rule.action,
        allow_autostash=False,
    )
    log_base_sync_result(sync_result, before_codex=False)
    rerun_codex = should_rerun_codex_after_sync(sync_result)

    if rule.require_conflict_free and has_unmerged_paths(workspace) and not rerun_codex:
        raise RuntimeError(
            f"문서 규칙상 merge 전에 `{target.base_branch}` 반영 후 충돌이 없어야 하지만 unresolved conflict가 남아 있습니다."
        )

    if rerun_codex:
        prepared_prompt = prepare_prompt(request, workspace, config, action="run")
        followup_prompt = build_post_sync_prompt(prepared_prompt, sync_result, rule)
        if prompt_suffix_lines:
            followup_prompt = append_follow_up_prompt_lines(followup_prompt, prompt_suffix_lines)
        print(rerun_reason)
        run_codex(
            request,
            workspace,
            config,
            runtime_options=BotRuntimeOptions(mode="codex", provider=config.provider, verify=True),
            prepared_prompt=followup_prompt,
        )

    if rule.require_conflict_free and has_unmerged_paths(workspace):
        raise RuntimeError(
            f"문서 규칙상 merge 전에 `{target.base_branch}` 기준으로 충돌이 없어야 하지만 후속 정리 뒤에도 conflict가 남아 있습니다."
        )

    changed_head = get_head_sha(workspace) != before_head
    verification_commands: list[str] = []
    if changed_head or rerun_codex:
        verification_plan = resolve_verification_plan(config, workspace, request)
        verification_commands = verification_plan.commands
        if verification_commands:
            run_verification(config, workspace, commands=verification_commands)

    changed_files = get_workspace_changed_files(workspace)
    if changed_files:
        run_git(["add", "--all"], workspace)
        unstage_output_artifacts(workspace, config.output_dir)
        if has_staged_changes(workspace):
            staged_files = get_staged_files(workspace)
            ensure_no_protected_changes(staged_files, config)
            run_git(
                ["commit", "-m", build_codex_commit_message(request, config, changed_files=staged_files)],
                workspace,
            )
            changed_head = True

    if changed_head:
        push_branch(repository, target.branch_name, token, workspace)


def append_follow_up_prompt_lines(prepared_prompt: PreparedPrompt, lines: list[str]) -> PreparedPrompt:
    follow_up = "\n".join([prepared_prompt.prompt, *lines])
    return PreparedPrompt(
        prompt=follow_up,
        attachment_info=prepared_prompt.attachment_info,
        available_secret_keys=prepared_prompt.available_secret_keys,
        repository_context=prepared_prompt.repository_context,
        project_summary=prepared_prompt.project_summary,
        code_context=prepared_prompt.code_context,
        attachment_context=prepared_prompt.attachment_context,
        metrics=prepared_prompt.metrics,
    )


def build_merge_request_from_pull_request(repository: str, pull_request: dict) -> IssueRequest:
    return IssueRequest(
        repository=repository,
        issue_number=int(pull_request["number"]),
        issue_title=(pull_request.get("title") or "").strip(),
        issue_body=(pull_request.get("body") or "").strip(),
        comment_body=f"{BOT_MENTION} merge readiness check",
        comment_author=((pull_request.get("user") or {}).get("login") or "auto-merge"),
        comment_id=0,
        is_pull_request=True,
        pull_request_number=int(pull_request["number"]),
        base_branch=((pull_request.get("base") or {}).get("ref") or "").strip() or None,
        head_branch=((pull_request.get("head") or {}).get("ref") or "").strip() or None,
        pull_request_url=(pull_request.get("html_url") or "").strip() or None,
    )


def get_head_sha(workspace: Path) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "rev-parse",
            "HEAD",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"HEAD SHA 조회에 실패했습니다:\n{(result.stdout or '').strip()}")
    return (result.stdout or "").strip()


def should_attempt_auto_merge(payload: dict) -> bool:
    if payload.get("review") and payload.get("pull_request"):
        review_state = ((payload.get("review") or {}).get("state") or "").lower()
        if review_state != "approved":
            print(f"review state가 approved가 아니라 auto-merge를 건너뜁니다. {review_state}")
            return False
        return True

    if payload.get("check_run"):
        action = str(payload.get("action") or "").lower()
        conclusion = ((payload.get("check_run") or {}).get("conclusion") or "").lower()
        if action != "completed" or conclusion != "success":
            print(f"check_run이 아직 성공 완료 상태가 아니라 auto-merge를 건너뜁니다. action={action}, conclusion={conclusion}")
            return False
        return True

    if payload.get("check_suite"):
        action = str(payload.get("action") or "").lower()
        conclusion = ((payload.get("check_suite") or {}).get("conclusion") or "").lower()
        if action != "completed" or conclusion != "success":
            print(f"check_suite가 아직 성공 완료 상태가 아니라 auto-merge를 건너뜁니다. action={action}, conclusion={conclusion}")
            return False
        return True

    if is_status_event(payload):
        state = str(payload.get("state") or "").lower()
        if state != "success":
            print(f"status가 success가 아니라 auto-merge를 건너뜁니다. {state}")
            return False
        return True

    print("지원하지 않는 auto-merge 이벤트라 건너뜁니다.")
    return False


def extract_target_pull_request_numbers(payload: dict, repository: str | None, token: str) -> list[int]:
    if payload.get("pull_request"):
        number = (payload.get("pull_request") or {}).get("number")
        return [int(number)] if number else []

    pull_requests = []
    if payload.get("check_run"):
        pull_requests = (payload.get("check_run") or {}).get("pull_requests") or []
    elif payload.get("check_suite"):
        pull_requests = (payload.get("check_suite") or {}).get("pull_requests") or []

    numbers: list[int] = []
    for item in pull_requests:
        raw_value = item.get("number")
        try:
            number = int(raw_value)
        except (TypeError, ValueError):
            continue
        if number not in numbers:
            numbers.append(number)
    if numbers:
        return numbers
    if is_status_event(payload) and repository:
        return extract_status_event_pull_request_numbers(repository, payload, token)
    return numbers


def is_status_event(payload: dict) -> bool:
    return bool(payload.get("repository")) and payload.get("state") is not None and isinstance(payload.get("branches"), list)


def extract_status_event_pull_request_numbers(repository: str, payload: dict, token: str) -> list[int]:
    owner = repository.split("/", 1)[0]
    numbers: list[int] = []
    for branch in payload.get("branches") or []:
        branch_name = (branch or {}).get("name")
        if not branch_name:
            continue
        query = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{branch_name}"})
        try:
            response = github_request("GET", f"/repos/{repository}/pulls?{query}", token)
        except Exception as error:
            print(f"status 이벤트에서 PR 조회를 건너뜁니다. branch={branch_name}, error={error}")
            continue
        for item in response or []:
            raw_value = item.get("number")
            try:
                number = int(raw_value)
            except (TypeError, ValueError):
                continue
            if number not in numbers:
                numbers.append(number)
    return numbers

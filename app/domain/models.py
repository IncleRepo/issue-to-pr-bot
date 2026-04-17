"""명령 파싱과 런타임 전반에서 공유하는 핵심 데이터 구조."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IssueRequest:
    repository: str
    issue_number: int
    issue_title: str
    issue_body: str
    comment_body: str
    comment_author: str
    comment_id: int
    is_pull_request: bool = False
    pull_request_number: int | None = None
    base_branch: str | None = None
    head_branch: str | None = None
    pull_request_url: str | None = None
    review_path: str | None = None
    review_line: int | None = None
    review_start_line: int | None = None
    review_side: str | None = None
    review_diff_hunk: str | None = None
    review_comment_url: str | None = None


@dataclass(frozen=True)
class BotCommand:
    action: str
    trigger: str
    instruction: str
    options: dict[str, str]


@dataclass(frozen=True)
class BotRuntimeOptions:
    mode: str
    provider: str
    verify: bool
    effort: str | None = None
    sync_base: bool = False
    request_merge: bool = False
    fresh_workspace: bool = False


@dataclass(frozen=True)
class MetadataPlan:
    issue_labels: list[str]
    pr_labels: list[str]
    assignees: list[str]
    reviewers: list[str]
    team_reviewers: list[str]
    milestone_title: str | None = None

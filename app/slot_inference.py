"""래퍼 전역 추론 계층에서 공용으로 쓰는 슬롯 추출 도우미.

액션 추론, 옵션 해석, 메타데이터 판단, 문서 규칙 추론
같은 여러 추론 레이어가 같은 방식으로 점수화하도록 돕는다.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


NORMALIZED_TEXT_PATTERN = re.compile(r"[^a-z0-9가-힣@._/-]+")
FENCED_CODE_BLOCK_PATTERN = re.compile(r"```[^\n]*\n.*?\n```", re.DOTALL)
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")


ACTION_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "help": ("help", "usage", "how to use", "사용법", "도움말", "도움"),
    "status": ("status", "state", "health", "health check", "상태", "점검", "체크", "확인"),
    "plan": ("plan only", "just plan", "implementation plan", "plan", "planning", "계획만", "계획", "설계만", "설계", "정리만"),
    "merge": ("merge", "auto merge", "merge when ready", "merge if approved", "머지", "합쳐줘", "병합", "승인되면 머지", "승인되면 합쳐줘", "승인되면 병합"),
}

MODE_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "test-pr": ("test-pr", "test pr", "marker pr", "branch only", "pr only", "draft only", "브랜치만", "pr만", "초안만", "draft만", "코드 수정 없이", "코드 수정 없이 pr"),
    "codex": ("codex", "코덱스", "코드까지", "실제 구현", "수정해서"),
}

PROVIDER_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "codex": ("codex", "코덱스"),
    "claude": ("claude", "클로드"),
}

EFFORT_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "xhigh": ("xhigh", "extra high", "very high", "최대로 깊게", "아주 깊게", "최대한 깊게"),
    "high": ("high", "깊게", "강하게", "전역적으로", "전체적으로", "여러 파일", "큰 범위", "복잡", "대대적", "대대적으로"),
    "medium": ("medium", "보통", "중간", "적당히"),
    "low": ("low", "가볍게", "빠르게", "간단히", "사소", "한 줄", "조금만", "살짝"),
}

VERIFY_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "off": ("skip verification", "without verification", "no verification", "검증 없이", "검증 생략", "테스트 없이", "테스트는 생략", "검증은 나중에", "테스트는 나중에"),
    "on": ("verify too", "검증까지", "검증도", "테스트까지", "테스트도", "빌드까지", "컴파일까지", "검증해줘", "테스트도 돌려줘"),
}

SYNC_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "sync_base": (
        "merge conflict",
        "conflict",
        "rebase",
        "sync with main",
        "sync with base",
        "merge main",
        "충돌",
        "충돌 해결",
        "메인 반영",
        "main 반영",
        "base 반영",
        "최신 main",
        "최신 반영",
        "리베이스",
    ),
}

IMPLEMENTATION_INTENT_TERMS: tuple[str, ...] = (
    "fix",
    "implement",
    "update",
    "change",
    "add",
    "resolve",
    "reflect",
    "edit",
    "modify",
    "write code",
    "수정",
    "구현",
    "추가",
    "반영",
    "해결",
    "작성",
    "고쳐",
    "만들",
    "적용",
    "개선",
)

DEFAULT_EFFORT_HINTS: dict[str, tuple[str, ...]] = {
    "low": ("readme", "docs", "documentation", "comment", "typo", "template", "문서", "오타", "주석", "템플릿", "문구", "간단", "사소", "한 줄"),
    "high": ("conflict", "merge conflict", "rebase", "sync with main", "sync with base", "migration", "schema", "refactor", "across files", "전역", "전체", "리팩토링", "충돌", "메인 반영", "스키마", "마이그레이션", "대대적", "전면", "여러 파일"),
}

GIT_PHASE_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "before_pr": ("before pr", "before pull request", "before opening pr", "before creating pr", "pr 전에", "pr 올리기 전에", "pr 생성 전에", "pull request 전에", "pull request 올리기 전에"),
    "before_commit": ("before commit", "커밋 전에"),
}

GIT_ACTION_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "rebase": ("rebase", "리베이스"),
    "merge": ("merge", "merged", "merging", "병합", "머지", "반영", "합치"),
}

CONFLICT_SLOT_TERMS: tuple[str, ...] = ("conflict-free", "conflict free", "충돌 없는지", "충돌 없게", "충돌 확인", "conflict check")

METADATA_LABEL_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "bug": ("bug", "error", "regression", "broken", "버그", "오류", "깨짐", "고장"),
    "enhancement": ("enhancement", "feature", "implement", "add", "new feature", "기능", "구현", "추가"),
    "documentation": ("documentation", "docs", "readme", "guide", "문서", "가이드"),
    "refactor": ("refactor", "cleanup", "restructure", "리팩터링", "정리"),
    "tests": ("tests", "test", "qa", "테스트"),
    "automation": ("automation", "bot", "workflow", "github actions", "ci", "자동화"),
    "dependencies": ("dependencies", "dependency", "requirements", "package", "lockfile", "의존성", "패키지"),
    "frontend": ("frontend", "ui", "html", "css", "web", "프론트", "화면"),
    "backend": ("backend", "api", "server", "백엔드", "서버"),
    "infra": ("infra", "config", "deployment", "ops", "deploy", "인프라", "배포", "설정"),
}

METADATA_HEADING_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "issue_labels": ("issue labels", "issue label", "이슈 라벨"),
    "pr_labels": ("pr labels", "pull request labels", "pr 라벨"),
    "labels": ("labels", "라벨"),
    "assignees": ("assignees", "owners", "담당자", "오너"),
    "reviewers": ("reviewers", "리뷰어", "검토자"),
    "team_reviewers": ("team reviewers", "review teams", "팀 리뷰어", "리뷰 팀"),
    "milestone": ("milestone", "마일스톤"),
}

VERIFICATION_SCOPE_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "frontend": ("html", "css", "javascript", "typescript", "frontend", "front-end", "landing", "page", "ui", "프론트", "페이지", "화면", "스타일"),
    "docs": ("readme", "docs", "documentation", "typo", "comment", "문서", "오타", "주석"),
    "python": ("python", "pytest", "unittest", ".py", "venv", "pip", "app/", "tests/", "백엔드", "backend"),
    "config": ("config", "configuration", "yaml", "yml", "json", "toml", "ini", "docker", "compose", "설정", "인프라"),
}

COMMIT_TYPE_SLOT_LEXICON: dict[str, tuple[str, ...]] = {
    "refactor": ("refactor", "cleanup", "restructure", "rename", "리팩터링", "구조 정리"),
    "docs": ("docs", "documentation", "readme", "문서", "설명", "가이드"),
    "test": ("test", "tests", "qa", "테스트"),
    "fix": ("fix", "bug", "error", "correct", "수정", "고쳐", "오타", "반영", "리뷰 반영", "버그"),
    "perf": ("perf", "performance", "optimize", "optimization", "성능", "최적화"),
}


@dataclass(frozen=True)
class SlotDecision:
    """슬롯 후보 선택 결과와 점수 정보를 담는 값 객체."""

    value: str | None
    score: int = 0
    confidence: str = "none"


def normalize_text(text: str) -> str:
    """비교 가능한 형태로 텍스트를 정규화한다."""

    normalized = NORMALIZED_TEXT_PATTERN.sub(" ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def strip_code_blocks(text: str) -> str:
    """코드 블록을 제거한 본문 텍스트를 반환한다."""

    return FENCED_CODE_BLOCK_PATTERN.sub("\n", text)


def split_text_segments(text: str, *, strip_fences: bool = False) -> list[str]:
    """문장을 점수 계산에 맞는 세그먼트 단위로 나눈다."""

    target = strip_code_blocks(text) if strip_fences else text
    segments = [segment.strip() for segment in SENTENCE_SPLIT_PATTERN.split(target) if segment.strip()]
    return segments or [target.strip()] if target.strip() else []


def contains_term(text: str, term: str) -> bool:
    """정규화 규칙을 적용해 용어 포함 여부를 확인한다."""

    lowered = text.lower()
    normalized_text = normalize_text(text)
    if term.lower() in lowered:
        return True
    normalized_term = normalize_text(term)
    if not normalized_term:
        return False
    return f" {normalized_term} " in f" {normalized_text} "


def contains_any_term(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    """여러 용어 중 하나라도 포함되면 참을 반환한다."""

    return any(contains_term(text, term) for term in terms)


def score_slot_values(
    text: str,
    lexicon: dict[str, tuple[str, ...]],
    *,
    context_terms: tuple[str, ...] | list[str] = (),
    heading: str | None = None,
    strip_fences: bool = False,
) -> dict[str, int]:
    """주어진 슬롯 사전에 대해 후보별 점수를 계산한다."""

    target = strip_code_blocks(text) if strip_fences else text
    segments = split_text_segments(target)
    heading_text = heading or ""
    scores: dict[str, int] = {}

    for value, aliases in lexicon.items():
        score = 0
        for alias in aliases:
            if contains_term(target, alias):
                score += 2 if " " in alias else 1
            for segment in segments:
                if contains_term(segment, alias):
                    score += 2
                    if context_terms and contains_any_term(segment, tuple(context_terms)):
                        score += 2
            if heading_text and contains_term(heading_text, alias):
                score += 2
        if score:
            scores[value] = score
    return scores


def pick_best_slot(
    text: str,
    lexicon: dict[str, tuple[str, ...]],
    *,
    context_terms: tuple[str, ...] | list[str] = (),
    heading: str | None = None,
    strip_fences: bool = False,
    min_score: int = 1,
) -> SlotDecision:
    """가장 점수가 높은 슬롯 후보를 선택한다."""

    scores = score_slot_values(
        text,
        lexicon,
        context_terms=context_terms,
        heading=heading,
        strip_fences=strip_fences,
    )
    if not scores:
        return SlotDecision(None, 0, "none")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    value, score = ranked[0]
    if score < min_score:
        return SlotDecision(None, score, "none")

    second_score = ranked[1][1] if len(ranked) > 1 else 0
    confidence = "high" if score >= 5 and score >= second_score + 2 else "medium" if score >= 3 else "low"
    return SlotDecision(value, score, confidence)



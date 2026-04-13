# 🤖 issue-to-pr-bot

GitHub 이슈를 기반으로 코드 작성 및 Pull Request를 자동 생성하는 LLM 기반 개발 자동화 봇

## 🚀 프로젝트 개요

이 프로젝트는 GitHub 이슈 또는 댓글을 입력으로 받아
LLM(Codex)을 활용해 코드를 생성하고, 자동으로 PR까지 생성하는 개인용 개발 자동화 도구입니다.

주요 목적은 반복적인 개발 작업을 줄이고,
AI 기반 개발 워크플로우를 실험하는 데 있습니다.

## 🧠 동작 흐름

이슈 / 댓글
      ↓
LLM (Codex CLI)
      ↓
코드 생성 및 수정
      ↓
Git 커밋 & 푸시
      ↓
Pull Request 생성

## ⚙️ 기술 스택

- Python
- GitHub Actions
- Self-hosted Runner
- Docker sandbox
- Codex CLI (ChatGPT Plus)
- GitHub REST API

## 🎯 주요 기능 (MVP)

- [ ] 이슈 댓글로 봇 실행 (`/bot run`)
- [ ] 이슈 내용 및 요구사항 파싱
- [ ] Codex CLI를 통한 코드 생성
- [ ] 브랜치 생성 및 커밋
- [ ] Pull Request 자동 생성

## 🔒 안전 장치

- 모든 작업은 별도의 브랜치에서 수행
- main 브랜치 직접 수정 금지
- PR 생성 후 수동 리뷰 필수
- 최소 권한 기반 실행
- 봇 실행은 Docker 컨테이너 내부에서 격리

## 🛠️ 저장소별 설정

`.issue-to-pr-bot.yml`에서 저장소별 실행 옵션을 조정합니다.

봇은 먼저 `AGENTS.md`, `CONTRIBUTING.md`, `README.md`, PR 템플릿을 읽고 팀 규칙을 자동 적용합니다.
문서 안에 브랜치 규칙, 커밋 메시지 규칙, PR 제목 규칙, 검증 명령이 적혀 있으면 그 값을 우선 사용합니다.
아래 템플릿 필드는 그런 문서 규칙이 없을 때만 fallback으로 사용합니다.

```yaml
bot:
  command: "/bot run"
  plan_command: "/bot plan"
  help_command: "/bot help"
  status_command: "/bot status"
  mention: "@incle-issue-to-pr-bot"
  branch_prefix: "bot"
  branch_name_template: "{branch_prefix}/issue-{issue_number}{comment_suffix}-{slug}"
  pr_title_template: "[bot] Issue #{issue_number}: {issue_title}"
  codex_commit_message_template: "feat: issue #{issue_number} Codex 작업 반영"
  test_commit_message_template: "chore: issue #{issue_number} 작업 기록"
  output_dir: "bot-output"
  test_command: "python -m unittest discover -s tests"
  check_commands:
    - "python -m compileall -q app tests"
    - "python -m unittest discover -s tests"
  external_context_paths:
    - "product"
  required_context_paths:
    - "README.md"
    - "external:product/domain.md"
  secret_env_keys:
    - "DB_URL"
  required_secret_env:
    - "DB_URL"
  mode: "codex"
```

`external:` 접두사는 runner가 마운트한 외부 context 디렉터리 기준 경로를 뜻합니다.

기본 명령:

- `/bot run`
- `/bot plan`
- `/bot help`
- `/bot status`
- `@incle-issue-to-pr-bot run ...`
- `@incle-issue-to-pr-bot plan ...`
- `@incle-issue-to-pr-bot status`

네이밍 템플릿에서 사용할 수 있는 주요 placeholder:

- `{issue_number}`
- `{issue_title}`
- `{slug}`
- `{comment_id}`
- `{comment_suffix}`
- `{branch_prefix}`
- `{repository}`
- `{comment_author}`

## 🧾 PR 템플릿

봇은 `.github/pull_request_template.md`를 읽어서 PR 본문을 생성합니다.

사용 가능한 placeholder:

- `{{ISSUE_NUMBER}}`
- `{{ISSUE_TITLE}}`
- `{{CHANGED_FILES}}`
- `{{VERIFICATION_COMMANDS}}`
- `{{TRIGGER_COMMAND}}`
- `{{BOT_MODE}}`

## 🔐 외부 문서와 secret 전달

외부 문서와 도메인 자료는 self-hosted runner 호스트의 디렉터리를 read-only로 마운트해서 전달합니다.

- GitHub Actions variable `BOT_CONTEXT_DIR_HOST`
  - 예: `C:\bot-context\issue-to-pr-bot`
- 컨테이너 내부 경로
  - `/run/external-context`
- `.issue-to-pr-bot.yml`의 `external_context_paths`와 `required_context_paths`로 읽을 문서를 제어

비밀 정보는 env 파일을 read-only로 마운트해서 전달합니다.

- GitHub Actions variable `BOT_SECRETS_FILE_HOST`
  - 예: `C:\bot-secrets\issue-to-pr-bot.env`
- 컨테이너 내부 경로
  - `/run/bot-secrets/secrets.env`
- `.issue-to-pr-bot.yml`의 `secret_env_keys`로 Codex에 “사용 가능한 키 이름만” 알림
- 실제 값은 프롬프트에 넣지 않음
- `required_secret_env`에 지정한 키가 없으면 작업을 중단하고 이슈 댓글로 실패를 남김

## 📎 첨부 링크 처리

이슈 본문과 댓글에 포함된 링크를 최대 일부까지 수집합니다.

- 텍스트 첨부(`.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.csv` 등)는 내용 일부를 프롬프트에 포함
- 이미지와 PDF는 파일 경로와 종류만 프롬프트에 포함
- 지원하지 않는 형식이나 너무 큰 파일은 건너뜀

## 📌 향후 확장 계획

- PR 리뷰 코멘트 자동 반영
- 멀티 LLM 구조 (Claude + Codex)
- 테스트 자동 실행 및 검증
- 변경 코드(diff) 분석 기능
- 조건부 자동 merge 기능

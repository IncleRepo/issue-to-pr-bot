# issue-to-pr-bot

GitHub 이슈, PR 댓글, 리뷰 코멘트에서 App 멘션을 받아 self-hosted runner 환경의 Codex로 코드 수정, 검증, PR 생성, PR 수정, merge 요청까지 연결하는 자동화 봇 엔진입니다.

이 저장소는 "봇 엔진"입니다. 실제 사용자는 자기 저장소에 workflow를 넣고, 자기 GitHub App과 자기 self-hosted runner를 연결해서 사용합니다.

## 1. 프로젝트 개요

### 이 프로젝트가 하는 일

사람이 GitHub에서 자연어로 요청하면 봇이 아래 순서로 작업합니다.

1. 댓글의 App 멘션과 자연어 요청을 읽습니다.
2. 저장소 문서와 구조를 읽고 규칙을 추론합니다.
3. Codex가 코드를 수정하거나 계획을 만듭니다.
4. 검증을 돌립니다.
5. 브랜치를 push하고 PR을 만들거나 기존 PR을 다시 갱신합니다.
6. 결과를 GitHub 댓글로 남깁니다.

### 지원 입력

- 이슈 댓글
- PR 일반 댓글
- PR 리뷰 코멘트
- PR 리뷰 본문

### 지원 결과

- 새 브랜치 생성
- 코드 수정
- 검증 실행
- PR 생성
- 기존 PR 재수정
- 승인 후 merge 요청 처리
- 실패 사유 댓글 보고

### 프로젝트 구조

```text
app/
  domain/
    models.py              # 공용 데이터 모델
  automation/
    parsing.py             # 자연어 멘션 해석
    templates.py           # 브랜치명, 커밋, 프롬프트 템플릿
  runtime/
    orchestrator.py        # 전체 실행 흐름
    comments.py            # GitHub 댓글 보고
  attachments.py           # 첨부 링크 수집
  auto_merge.py            # 승인 후 merge 처리
  codex_provider.py        # Codex CLI 실행
  codex_runner.py          # Codex 실행과 PR 생성 연결
  config.py                # 최소 설정과 기본값
  github_pr.py             # git / GitHub API 처리
  llm_provider.py          # LLM provider 추상화
  prompting.py             # prompt budget, context 선별, 계측
  repo_context.py          # 저장소 문서/구조 수집
  repo_rules.py            # 문서 기반 규칙 추론
  runtime_secrets.py       # secret env 로딩
  verification.py          # 검증 명령 실행
  bot.py                   # 호환용 facade
  main.py                  # 실행 진입점 facade
templates/
tests/
```

## 2. 사용하는 법: 빈 저장소에서 처음부터 적용하기

이 섹션은 "완전 빈 저장소" 기준입니다. 아래 순서대로만 하면 됩니다.

### 2-1. 새 저장소 만들기

GitHub에서 새 저장소를 만듭니다.

예시:

- 저장소 이름: `my-test-bot-repo`

처음에는 정말 비어 있어도 됩니다.

### 2-2. 이 저장소를 로컬에 clone 하기

```powershell
git clone https://github.com/<내계정>/my-test-bot-repo.git
cd my-test-bot-repo
```

### 2-3. 내 GitHub App 만들기

GitHub에서 새 GitHub App을 하나 만듭니다.

App 이름은 자유입니다.

예시:

- `my-issue-to-pr-bot`

중요:

- App 이름이 GitHub 멘션 이름이 됩니다.
- 위 이름이면 댓글에서 `@my-issue-to-pr-bot` 로 부릅니다.

### 2-4. GitHub App 권한 설정

App 생성 화면에서 아래 권한을 켭니다.

Repository permissions:

- `Contents` -> Read and write
- `Issues` -> Read and write
- `Pull requests` -> Read and write
- `Metadata` -> Read-only
- `Workflows` -> Read and write

Subscribe to events:

- `Issue comment`
- `Pull request review`
- `Pull request review comment`

설치 대상은 보통:

- `Only on this account`

으로 시작하면 충분합니다.

### 2-5. App 정보 준비

App 생성 후 아래 3개를 확인합니다.

1. App 멘션 이름
2. App ID
3. Private key PEM

예시:

- 멘션 이름: `@my-issue-to-pr-bot`
- App ID: `123456`
- Private key: GitHub에서 발급한 `.pem`

Private key는 App 화면에서 발급받아야 합니다.

### 2-6. App을 내 저장소에 설치

방금 만든 App을 실제 사용할 저장소에 설치합니다.

즉:

1. App 생성
2. 저장소 선택
3. 설치

까지 끝내면 저장소 연결이 완료됩니다.

### 2-7. 내 PC에 self-hosted runner 준비

봇은 GitHub 기본 서버가 아니라 **내 PC 또는 내 서버**에서 돌아갑니다.

필수 준비물:

- Windows self-hosted runner
- Docker
- Git
- Python
- Codex CLI 로그인 완료

### 2-8. runner 실행

예시:

```powershell
cd C:\actions-runner
.\run.cmd
```

정상 상태면 아래 문구가 보입니다.

- `Connected to GitHub`
- `Listening for Jobs`

### 2-9. workflow 파일 넣기

대상 저장소에는 GitHub 이벤트를 받기 위한 workflow 파일이 필요합니다.

필수:

- `.github/workflows/issue-comment.yml`

권장:

- `.github/workflows/pull-request-review.yml`
- `.github/workflows/pull-request-review-comment.yml`

가장 쉬운 방법:

1. 이 저장소의 `templates/` 폴더를 엽니다.
2. 아래 파일들을 대상 저장소에 복사합니다.

- `templates/issue-comment.yml.example`
- `templates/pull-request-review.yml.example`
- `templates/pull-request-review-comment.yml.example`

3. 대상 저장소에서 파일명을 아래처럼 바꿉니다.

```text
.github/workflows/issue-comment.yml
.github/workflows/pull-request-review.yml
.github/workflows/pull-request-review-comment.yml
```

### 2-10. self-hosted runner가 yml에서 어떻게 쓰이는지

많이 헷갈리는 부분이라 아주 간단히 적습니다.

- runner 프로그램은 내 PC에서 직접 실행합니다.
- workflow yml은 그 runner를 쓰라고 지정합니다.

템플릿 workflow에는 이런 값이 들어 있습니다.

```yml
with:
  runner_labels_json: '["self-hosted","Windows"]'
```

뜻:

- `self-hosted`: GitHub 기본 서버 말고 내가 직접 띄운 runner를 써라
- `Windows`: Windows runner를 써라

즉:

1. 내 PC에서 runner를 켠다
2. yml이 그 runner를 잡아서 실행한다

둘 다 있어야 돌아갑니다.

### 2-11. 저장소 변수와 secret 넣기

대상 저장소 `Settings > Secrets and variables > Actions` 로 갑니다.

Repository variables:

- `BOT_MENTION`
- `BOT_APP_ID`

Repository secrets:

- `BOT_APP_PRIVATE_KEY`

넣는 값:

- `BOT_MENTION` = 내 App 멘션 이름  
  예: `@my-issue-to-pr-bot`
- `BOT_APP_ID` = 내 App ID  
  예: `123456`
- `BOT_APP_PRIVATE_KEY` = 내 App의 `.pem` 파일 내용 전체

즉 이 단계는 "공용 App"이 아니라 **각자 자기 App 정보**를 넣는 단계입니다.

### 2-12. Codex 로그인 확인

내 PC에서 Codex CLI가 로그인되어 있어야 합니다.

보통 아래 파일이 있어야 합니다.

- `%USERPROFILE%\\.codex\\auth.json`
- `%USERPROFILE%\\.codex\\config.toml`

### 2-13. 선택 파일 넣기

이 단계는 필수는 아니지만 정확도를 높여줍니다.

권장 파일:

- `README.md`
- `AGENTS.md`
- `CONTRIBUTING.md`
- `.github/pull_request_template.md`

선택 설정 파일:

- `.issue-to-pr-bot.yml`

`.issue-to-pr-bot.yml`이 없어도 됩니다.

최소 예시:

```yaml
bot:
  output_dir: "bot-output"
```

중요:

- `required_context_paths`는 문서에서 자동 추론하지 않습니다.
- `required_secret_env`는 문서에서 자동 추론하지 않습니다.
- 필수 context나 필수 시크릿이 정말 필요할 때만 `.issue-to-pr-bot.yml`에 직접 적습니다.

예시:

```yaml
bot:
  output_dir: "bot-output"
  external_context_paths:
    - "product"
  required_context_paths:
    - "docs/domain.md"
    - "external:product/api.md"
  secret_env_keys:
    - "DB_URL"
    - "OPENAI_API_KEY"
  required_secret_env:
    - "DB_URL"
```

위 예시 의미:

- `docs/domain.md`는 저장소 안에 반드시 있어야 합니다.
- `external:product/api.md`는 runner가 마운트한 외부 context 폴더 안에 반드시 있어야 합니다.
- `DB_URL`은 없으면 작업을 중단합니다.
- `OPENAI_API_KEY`는 있으면 사용하고, 없어도 중단하지 않습니다.

### 2-14. 완전 최소 예시 파일 만들기

정말 빈 저장소라면 아래 정도만 먼저 만들어도 됩니다.

`README.md`

```md
# my-test-bot-repo

테스트 저장소
```

`AGENTS.md`

```md
# Repository Agent Guide

## Working Rules

- Keep changes focused on the request.
- Do not commit secrets or private keys.

## Verification

```powershell
python -m compileall -q app tests
python -m unittest discover -s tests
```
```

### 2-15. 대상 저장소에 첫 commit / push

```powershell
git add .
git commit -m "chore: bot workflow 초기 설정"
git push
```

### 2-16. 첫 테스트 이슈 만들기

GitHub에서 이슈를 하나 만듭니다.

예시:

- 제목: `README에 로컬 실행 방법 추가`
- 본문: `README.md에 로컬 실행 방법을 추가해줘.`

### 2-17. 첫 실행 댓글 달기

이슈 댓글에 내 App 멘션으로 요청합니다.

예시:

```text
@my-issue-to-pr-bot README에 로컬 실행 방법 추가해줘
```

### 2-18. 정상 동작하면 일어나는 일

정상이라면 봇이:

1. 댓글을 읽습니다.
2. 저장소 문서를 읽습니다.
3. 브랜치를 만듭니다.
4. 코드를 수정합니다.
5. 검증합니다.
6. PR을 생성합니다.
7. 결과를 댓글로 남깁니다.

### 2-19. PR 수정 요청

이미 올라온 PR에서 다시 수정시키고 싶으면 PR 댓글에 이렇게 적습니다.

```text
@my-issue-to-pr-bot 이 부분 다시 수정해줘
```

그러면 봇이 새 PR을 만드는 게 아니라 기존 PR 브랜치에 다시 push합니다.

### 2-20. 리뷰 코멘트 반영

코드 라인에 달린 리뷰 코멘트에서도 요청할 수 있습니다.

```text
@my-issue-to-pr-bot 이 리뷰 반영해줘
```

### 2-21. 충돌 해결 요청

PR 브랜치가 main과 충돌 나면 이렇게 적습니다.

```text
@my-issue-to-pr-bot main 반영하고 충돌 해결해줘
```

### 2-22. merge 요청

PR에서 이렇게 적습니다.

```text
@my-issue-to-pr-bot 승인되면 머지해줘
```

그러면 봇은 merge 의도를 기록하고, GitHub 보호 규칙이 만족되는 시점에 merge를 시도합니다.

## 3. 기능 소개

### 자연어 요청 처리

- `/bot` 같은 명령어 없이 App 멘션 + 자연어만 받습니다.
- 이슈, PR, 리뷰 코멘트, 리뷰 본문까지 처리합니다.

### 저장소 규칙 자동 추론

봇은 다음 문서를 읽고 규칙을 자동 반영합니다.

- `AGENTS.md`
- `CONTRIBUTING.md`
- `README.md`
- `.github/pull_request_template.md`
- `.github/ISSUE_TEMPLATE`
- `.editorconfig`
- `pyproject.toml`
- `package.json`

자동 추론 대상:

- 브랜치명 규칙
- 커밋 메시지 규칙
- PR 제목 규칙
- 검증 명령
- protected paths

### 이슈 기반 구현

- 이슈 댓글에서 요청을 받으면 새 브랜치를 만들고 PR을 생성합니다.

### PR 기반 수정

- PR 일반 댓글에서 요청을 받으면 기존 PR 브랜치를 수정하고 다시 push합니다.

### 리뷰 코멘트 반영

- inline review comment와 review 본문 요청을 모두 처리합니다.

### 충돌 해결 지원

- `main 반영`, `충돌 해결`, `rebase` 같은 자연어를 해석해 base sync를 시도합니다.

### merge 요청 처리

- `머지해줘`, `승인되면 머지해줘` 같은 요청을 받아 merge intent를 기록합니다.
- 실제 merge 허용 여부는 GitHub branch protection 규칙을 따릅니다.

### 첨부 링크 처리

- 이슈/댓글에 들어간 링크를 수집합니다.
- 텍스트 첨부는 일부 내용을 prompt에 포함합니다.
- 이미지/PDF는 파일 경로와 메타 정보만 전달합니다.

### 외부 context / secret 지원

- 외부 문서 폴더를 read-only로 마운트할 수 있습니다.
- secret env 파일을 주입할 수 있습니다.
- 필수 context는 `.issue-to-pr-bot.yml`의 `required_context_paths`에 직접 적었을 때만 검사합니다.
- 필수 secret은 `.issue-to-pr-bot.yml`의 `required_secret_env`에 직접 적었을 때만 검사합니다.

### LLM 사용 최적화

- prompt budget 적용
- 문서 우선순위 선별
- 첨부/프로젝트 구조 축약
- 기본 effort 자동 라우팅
- LLM 실행 시간과 prompt 크기 계측

### 검증 후 PR 생성

- 검증 실패 시 PR을 만들지 않습니다.
- 실패 원인과 다음 행동을 댓글로 남깁니다.

## 4. 빠른 체크리스트

완전 최소 체크리스트만 다시 적으면:

1. 저장소 생성
2. 내 GitHub App 생성
3. App 권한 설정
4. App 설치
5. App ID / PEM 준비
6. runner 설치
7. runner 실행
8. workflow 파일 복사
9. `BOT_MENTION`, `BOT_APP_ID`, `BOT_APP_PRIVATE_KEY` 등록
10. Codex 로그인 확인
11. 이슈 생성
12. 댓글로 `@내-앱이름 ...` 실행

## 5. 로컬 검증

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

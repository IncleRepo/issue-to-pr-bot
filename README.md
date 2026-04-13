# issue-to-pr-bot

GitHub 이슈나 PR에서 App을 멘션하면, self-hosted runner 위에서 Codex가 작업을 수행하고 PR까지 만들어주는 중앙 엔진 저장소입니다.

이 저장소의 핵심 목표는 두 가지입니다.

- 여러 대상 저장소에서 같은 봇 엔진을 재사용하기
- GitHub Actions를 유지한 채 설정과 운영을 최대한 중앙화하기

즉, 대상 저장소마다 봇 코드를 복사하는 방식이 아니라 중앙 엔진 저장소 하나를 두고 얇은 workflow만 연결해서 사용하는 구조를 지향합니다.

## 이 저장소를 한 줄로 이해하기

흐름은 아래처럼 보면 가장 쉽습니다.

1. 대상 저장소에서 GitHub 이벤트가 발생합니다.
2. 대상 저장소의 얇은 workflow가 중앙 `engine repo`의 reusable workflow를 호출합니다.
3. self-hosted runner가 작업을 받습니다.
4. Docker 컨테이너 안에서 봇 엔진이 실행됩니다.
5. Codex가 변경을 만들고 검증한 뒤 PR을 생성합니다.

역할은 이렇게 나뉩니다.

- `self-hosted runner`
  - 실제 GitHub Actions 작업이 돌아가는 머신입니다.
- `Docker`
  - 봇이 항상 같은 환경에서 실행되도록 고정하는 작업실입니다.
- `Codex`
  - 코드 수정과 계획 생성을 담당하는 CLI 도구입니다.
- `engine repo`
  - 지금 보고 있는 이 저장소입니다. 봇 코드와 reusable workflow가 여기에 있습니다.
- `target repo`
  - 실제로 봇이 작업할 대상 저장소입니다.

## 중앙화는 어디까지 가능한가

GitHub Actions를 유지하는 한도에서 현실적으로 가능한 최대치는 아래에 가깝습니다.

- 중앙 `engine repo` 1개
- 중앙 `reusable workflow` 1개
- 중앙 `self-hosted runner` 또는 `runner group`
- 중앙 `GitHub App`
- 중앙 `organization-level variables / secrets` 또는 저장소별 자동 설정
- 대상 저장소에는 얇은 caller workflow만 유지

중요한 한계도 있습니다.

- GitHub Actions를 쓰는 한, 대상 저장소에 workflow 파일이 완전히 0개가 되지는 않습니다.
- GitHub App 생성, runner 등록, Codex 로그인 같은 인증 단계는 완전 무인 자동화가 어렵습니다.

그래도 대부분의 설치와 설정은 중앙화 매니저로 많이 줄일 수 있습니다.

## 이 저장소가 다른 저장소에서 바로 동작하는 이유

대상 저장소에 Python 앱 코드를 복사하지 않아도 되는 이유는, workflow 실행 시점에 중앙 엔진 저장소를 별도로 checkout해서 쓰기 때문입니다.

즉 대상 저장소에는 보통 아래 정도만 있으면 됩니다.

- `.github/workflows/issue-comment.yml`
- 선택: `.github/workflows/pull-request-review.yml`
- 선택: `.github/workflows/pull-request-review-comment.yml`
- 선택: `.issue-to-pr-bot.yml`

## 빠른 시작

처음 붙이는 순서는 아래가 가장 단순합니다.

1. GitHub App을 만든다.
2. self-hosted runner, Docker, Codex 로그인을 준비한다.
3. 중앙화 매니저로 대상 저장소에 workflow를 설치한다.
4. 필요하면 GitHub 변수/시크릿도 매니저로 설정한다.
5. 대상 저장소 이슈나 PR에서 봇을 멘션한다.

## 중앙화 매니저 CLI

이제 이 저장소는 패키지형 CLI로도 사용할 수 있습니다.

설치 방법 예시:

```powershell
python -m pip install .
```

원격 저장소에서 바로 설치하고 싶다면:

```powershell
python -m pip install git+https://github.com/IncleRepo/issue-to-pr-bot.git
```

설치 후 사용할 명령은 아래입니다.

```powershell
issue-to-pr-bot-manager --help
```

로컬 소스에서 바로 실행할 수도 있습니다.

```powershell
python -m app.install_manager --help
```

## 주요 명령

### 1. `init`

대상 저장소에 얇은 workflow를 처음 설치합니다.

```powershell
issue-to-pr-bot-manager init `
  --target C:\path\to\target-repo `
  --write-config
```

이 명령이 해주는 일:

- issue comment workflow 생성
- 선택 시 review workflow 생성
- 최소 `.issue-to-pr-bot.yml` 생성
- 중앙 engine repo / ref / runner labels 값 반영

### 2. `update`

이미 설치된 관리 파일을 최신 템플릿 기준으로 다시 반영합니다.

```powershell
issue-to-pr-bot-manager update `
  --target C:\path\to\target-repo `
  --write-config
```

이 명령은 사실상 `force` 재생성에 가깝습니다. 여러 저장소를 한 번씩 돌려서 caller workflow를 갱신할 때 유용합니다.

### 3. `doctor`

현재 머신과 대상 저장소가 실행 준비가 되었는지 점검합니다.

```powershell
issue-to-pr-bot-manager doctor `
  --target C:\path\to\target-repo `
  --repo IncleRepo/my-target-repo `
  --runner-root C:\actions-runner
```

점검 항목:

- Python / Git / Docker / Codex / GitHub CLI 존재 여부
- Docker daemon 응답 여부
- Codex 로그인 파일 존재 여부
- self-hosted runner 경로 확인
- 대상 저장소 workflow 설치 여부
- `gh`를 통한 저장소 변수/시크릿 존재 여부

### 4. `configure-github`

GitHub CLI(`gh`)를 사용해 대상 저장소 변수와 시크릿을 설정합니다.

```powershell
issue-to-pr-bot-manager configure-github `
  --repo IncleRepo/my-target-repo `
  --bot-mention @my-issue-to-pr-bot `
  --bot-app-id 123456 `
  --bot-app-private-key-file C:\keys\my-app.pem
```

설정되는 값:

- repository variable `BOT_MENTION`
- repository variable `BOT_APP_ID`
- repository secret `BOT_APP_PRIVATE_KEY`

### 5. `bootstrap`

가장 실전적인 명령입니다. 설치, 선택적 GitHub 설정, 상태 점검을 한 번에 묶습니다.

```powershell
issue-to-pr-bot-manager bootstrap `
  --target C:\path\to\target-repo `
  --repo IncleRepo/my-target-repo `
  --bot-mention @my-issue-to-pr-bot `
  --bot-app-id 123456 `
  --bot-app-private-key-file C:\keys\my-app.pem `
  --write-config `
  --runner-root C:\actions-runner
```

추천 흐름은 아래처럼 보면 됩니다.

1. `bootstrap`으로 대상 저장소 파일 설치
2. 같은 명령에서 GitHub 변수/시크릿 설정
3. 마지막 doctor 결과로 부족한 항목 확인

## 대상 저장소에 남는 최소 파일

최소 설치 기준으로는 아래 정도만 남습니다.

```text
.github/workflows/issue-comment.yml
.issue-to-pr-bot.yml   # 선택
```

리뷰 자동화까지 쓰려면 아래 두 파일을 추가합니다.

```text
.github/workflows/pull-request-review.yml
.github/workflows/pull-request-review-comment.yml
```

## GitHub App 준비

App 이름은 멘션 이름이 됩니다.

예시:

- App 이름: `my-issue-to-pr-bot`
- 댓글 멘션: `@my-issue-to-pr-bot`

권장 권한:

- `Contents` -> Read and write
- `Issues` -> Read and write
- `Pull requests` -> Read and write
- `Metadata` -> Read-only
- `Workflows` -> Read and write

필요 이벤트:

- `Issue comment`
- `Pull request review`
- `Pull request review comment`

준비해야 할 값:

- `BOT_MENTION`
- `BOT_APP_ID`
- `BOT_APP_PRIVATE_KEY`

## self-hosted runner 준비

필수 준비물:

- Windows self-hosted runner
- Docker
- Git
- Python
- Codex CLI 로그인

예시:

```powershell
cd C:\actions-runner
.\run.cmd
```

정상 상태라면 다음과 비슷한 문구가 보입니다.

- `Connected to GitHub`
- `Listening for Jobs`

## Codex 로그인

호스트 머신에서 Codex CLI가 로그인되어 있어야 합니다.

보통 아래 파일이 필요합니다.

- `%USERPROFILE%\.codex\auth.json`
- `%USERPROFILE%\.codex\config.toml`

이 프로젝트는 호스트의 Codex 인증을 Docker 실행 시점에 마운트해서 사용합니다.

즉 이미지 안에 로그인 정보가 들어있는 구조가 아니라, 호스트 인증을 컨테이너가 읽는 구조입니다.

## `.issue-to-pr-bot.yml`

이 파일은 없어도 기본 동작이 가능합니다. 처음에는 되도록 없이 시작하는 편이 좋습니다.

최소 예시:

```yaml
bot:
  output_dir: "bot-output"
```

언제 추가하면 좋은가:

- 외부 문서를 반드시 읽어야 할 때
- 특정 시크릿이 없으면 작업을 중단해야 할 때
- 출력 디렉터리 정책을 바꾸고 싶을 때

확장 예시:

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

## 대상 저장소에서 어떻게 요청하나

이슈나 PR에서 자연어로 요청하면 됩니다.

예시:

- `@my-issue-to-pr-bot README 문구를 다듬어줘`
- `@my-issue-to-pr-bot 이 리뷰 반영해줘`
- `@my-issue-to-pr-bot main 반영하고 충돌 해결해줘`
- `@my-issue-to-pr-bot 승인되면 머지해줘`

## 봇이 자동으로 읽는 문서

봇은 저장소 안 문서를 읽고 규칙을 추론합니다.

- `AGENTS.md`
- `CONTRIBUTING.md`
- `README.md`
- `.github/pull_request_template.md`
- `.github/ISSUE_TEMPLATE`
- `.editorconfig`
- `pyproject.toml`
- `package.json`

이 문서들에서 주로 추론하는 내용:

- 브랜치명 규칙
- 커밋 메시지 규칙
- PR 제목 규칙
- 검증 명령
- protected paths

## 실패하면 어떻게 되나

이 프로젝트는 두 종류의 실패를 가능한 한 댓글로 보고합니다.

- 봇 엔진 내부 실패
  - 예: 검증 실패, GitHub API 실패, context 누락
- 워크플로 단계 실패
  - 예: Docker 이미지 빌드 실패, 컨테이너 시작 실패

즉 예전보다 실패 원인을 GitHub UI 안에서 바로 파악하기 쉬운 편입니다.

## 프로젝트 구조

주요 파일:

```text
app/
  install_manager.py       # 패키지형 중앙화 매니저 CLI
  verification_policy.py   # 변경 범위 기반 검증 선택
  runtime/orchestrator.py  # 전체 실행 흐름
  runtime/comments.py      # 결과 / 실패 댓글 작성
  codex_runner.py          # Codex 실행과 PR 생성 연결
  github_pr.py             # git / GitHub API 처리
  repo_rules.py            # 저장소 문서 기반 규칙 추론
  verification.py          # 검증 실행
app/manager_templates/     # 패키지 배포에도 포함되는 설치 템플릿
templates/                 # 사람이 직접 복사해서 쓸 수 있는 템플릿
tests/                     # 단위 테스트
```

## 로컬 개발

로컬 검증:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

패키지형 매니저를 로컬에서 설치:

```powershell
python -m pip install .
```

설치 매니저 dry-run 예시:

```powershell
issue-to-pr-bot-manager bootstrap `
  --target C:\path\to\target-repo `
  --write-config `
  --dry-run
```

## 운영 팁

- Actions를 유지하는 한, 대상 저장소에는 얇은 caller workflow만 남기는 방향이 가장 관리하기 쉽습니다.
- organization이 있다면 runner와 variables/secrets를 가능한 한 조직 레벨로 중앙화하는 편이 좋습니다.
- `.issue-to-pr-bot.yml`은 정말 필요할 때만 추가하세요.
- 설치와 설정은 `bootstrap`, 정기 점검은 `doctor`, 템플릿 갱신은 `update`로 가져가는 운영이 가장 단순합니다.

## 최소 체크리스트

1. GitHub App 생성
2. App 권한 / 이벤트 설정
3. App을 대상 저장소에 설치
4. self-hosted runner 실행
5. Docker / Codex 로그인 확인
6. `issue-to-pr-bot-manager bootstrap` 실행
7. 이슈나 PR에서 봇 멘션 테스트

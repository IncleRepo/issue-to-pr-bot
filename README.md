# issue-to-pr-bot

GitHub 이슈나 PR에서 봇을 멘션하면, Codex가 작업을 수행하고 PR까지 만들어주는 자동화 봇입니다.

이 저장소는 봇 엔진 저장소입니다.  
실제로 봇을 쓰는 대상 저장소에는 얇은 workflow만 넣고, 실행할 때 이 저장소를 불러와서 사용합니다.

## 이 프로젝트로 할 수 있는 일

- 이슈 댓글 요청 처리
- PR 댓글 / 리뷰 댓글 요청 처리
- 코드 수정
- 검증 실행
- 브랜치 push
- PR 생성
- 실패 사유 댓글 보고

## 가장 쉬운 이해 방식

처음에는 아래 3가지만 이해하면 충분합니다.

- `self-hosted runner`
  - GitHub Actions 작업이 실제로 돌아가는 내 PC 또는 서버
- `Docker`
  - 봇이 항상 같은 환경에서 실행되게 하는 작업실
- `Codex`
  - 실제 코드 작업을 하는 도구

즉 흐름은 간단히 이렇습니다.

1. GitHub에서 댓글 이벤트가 발생
2. runner가 작업을 받음
3. Docker 안에서 봇 실행
4. Codex가 코드 수정
5. 검증 후 PR 생성

## 빠른 시작

처음 붙일 때는 아래 순서만 따라가면 됩니다.

1. GitHub App 만들기
2. self-hosted runner 준비하기
3. Codex 로그인 확인하기
4. 대상 저장소에 workflow 설치하기
5. 대상 저장소 변수/시크릿 등록하기
6. 첫 멘션 테스트하기

## 1. GitHub App 만들기

App 이름은 멘션 이름이 됩니다.

예시:

- App 이름: `my-issue-to-pr-bot`
- 멘션: `@my-issue-to-pr-bot`

권한:

- `Contents` -> Read and write
- `Issues` -> Read and write
- `Pull requests` -> Read and write
- `Metadata` -> Read-only
- `Workflows` -> Read and write

이벤트:

- `Issue comment`
- `Pull request review`
- `Pull request review comment`

준비해둘 값:

- `BOT_MENTION`
- `BOT_APP_ID`
- `BOT_APP_PRIVATE_KEY`

## 2. self-hosted runner 준비하기

필수 준비물:

- Windows self-hosted runner
- Docker
- Git
- Python
- Codex CLI

runner 실행 예시:

```powershell
cd C:\actions-runner
.\run.cmd
```

정상 상태라면 보통 이런 문구가 보입니다.

- `Connected to GitHub`
- `Listening for Jobs`

## 3. Codex 로그인 확인하기

호스트 머신에서 Codex CLI가 로그인되어 있어야 합니다.

보통 아래 파일이 있어야 합니다.

- `%USERPROFILE%\.codex\auth.json`
- `%USERPROFILE%\.codex\config.toml`

이 프로젝트는 이 로그인 정보를 Docker 실행 시점에 컨테이너로 넘겨서 사용합니다.

## 4. 대상 저장소에 workflow 설치하기

가장 쉬운 방법은 중앙화 매니저 CLI를 쓰는 것입니다.

### 설치

현재 저장소에서 설치:

```powershell
python -m pip install .
```

원격 저장소에서 바로 설치:

```powershell
python -m pip install git+https://github.com/IncleRepo/issue-to-pr-bot.git
```

설치 후 사용할 명령:

```powershell
issue-to-pr-bot-manager --help
```

### 가장 추천하는 설치 명령

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

이 명령이 해주는 일:

- 대상 저장소 workflow 설치
- 최소 설정 파일 생성
- GitHub 변수/시크릿 등록
- 로컬 머신 준비 상태 진단

### 최소 설치만 하고 싶다면

```powershell
issue-to-pr-bot-manager init `
  --target C:\path\to\target-repo `
  --write-config
```

### 상태만 점검하고 싶다면

```powershell
issue-to-pr-bot-manager doctor `
  --target C:\path\to\target-repo `
  --repo IncleRepo/my-target-repo `
  --runner-root C:\actions-runner
```

### 변수/시크릿만 설정하고 싶다면

```powershell
issue-to-pr-bot-manager configure-github `
  --repo IncleRepo/my-target-repo `
  --bot-mention @my-issue-to-pr-bot `
  --bot-app-id 123456 `
  --bot-app-private-key-file C:\keys\my-app.pem
```

## 5. 대상 저장소에 실제로 생기는 파일

기본 설치 시 보통 아래 파일이 생깁니다.

```text
.github/workflows/issue-comment.yml
.github/workflows/pull-request-review.yml
.github/workflows/pull-request-review-comment.yml
.issue-to-pr-bot.yml
```

리뷰 자동화가 필요 없으면 `init --skip-review-workflows`로 더 최소 설치도 가능합니다.

## 6. 첫 테스트

대상 저장소에서 이슈를 하나 만든 뒤 댓글로 봇을 멘션하면 됩니다.

예시:

```text
@my-issue-to-pr-bot README에 로컬 실행 방법 추가해줘
```

정상 동작하면:

1. 댓글을 읽고
2. 저장소 문서를 읽고
3. 코드를 수정하고
4. 검증을 돌리고
5. 브랜치를 push하고
6. PR을 만들고
7. 결과를 댓글로 남깁니다

## 자주 쓰는 명령

workflow 설치:

```powershell
issue-to-pr-bot-manager init --target C:\repo --write-config
```

workflow 갱신:

```powershell
issue-to-pr-bot-manager update --target C:\repo --write-config
```

설정만 미리 보기:

```powershell
issue-to-pr-bot-manager bootstrap --target C:\repo --write-config --dry-run
```

## `.issue-to-pr-bot.yml`

이 파일은 없어도 기본 동작이 가능합니다.  
처음에는 최대한 단순하게 두는 걸 추천합니다.

최소 예시:

```yaml
bot:
  output_dir: "bot-output"
```

언제 수정하면 좋나:

- 외부 문서를 반드시 읽어야 할 때
- 특정 시크릿이 없으면 작업을 중단해야 할 때
- 출력 디렉터리를 바꾸고 싶을 때

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

## 어떤 요청을 보낼 수 있나

예시:

- `@my-issue-to-pr-bot README 문구를 다듬어줘`
- `@my-issue-to-pr-bot 이 리뷰 반영해줘`
- `@my-issue-to-pr-bot main 반영하고 충돌 해결해줘`
- `@my-issue-to-pr-bot 승인되면 머지해줘`

## 실패하면 어떻게 되나

실패하면 가능한 한 GitHub 댓글로 원인을 남기도록 되어 있습니다.

예:

- 검증 실패
- GitHub API 실패
- Docker 이미지 빌드 실패
- 컨테이너 시작 실패

즉 예전보다 “왜 실패했는지”를 Actions 로그를 열기 전에도 보기 쉬운 편입니다.

## 로컬 개발

로컬 검증:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## 한 번에 보는 체크리스트

1. GitHub App 생성
2. App 권한 / 이벤트 설정
3. App을 대상 저장소에 설치
4. self-hosted runner 실행
5. Docker / Codex 로그인 확인
6. `issue-to-pr-bot-manager bootstrap` 실행
7. 이슈나 PR에서 봇 멘션 테스트

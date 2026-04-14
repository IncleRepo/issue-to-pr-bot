# issue-to-pr-bot

GitHub 이슈, PR 댓글, 리뷰 댓글에서 봇을 멘션하면 Codex가 작업을 수행하고 결과를 PR로 올려주는 자동화 봇 엔진입니다.

이 저장소는 봇 엔진 저장소입니다. 실제로 봇을 붙일 대상 저장소에는 얇은 workflow만 들어가고, 실행할 때 이 저장소를 재사용합니다.

## 이 프로젝트가 해주는 일

- 이슈 댓글 요청 처리
- PR 댓글, 리뷰 댓글, 리뷰 본문 요청 처리
- 코드 수정
- 검증 실행
- 브랜치 push
- PR 생성 또는 기존 PR 갱신
- 실패 사유 댓글 보고
- 승인 후 merge 시도

## 먼저 알아두면 좋은 흐름

처음에는 아래 네 가지만 알고 시작하면 됩니다.

1. GitHub App  
   댓글에서 부르는 봇 이름입니다. 예를 들어 App 이름이 `my-issue-to-pr-bot`이면 댓글에서 `@my-issue-to-pr-bot`으로 부릅니다.
2. self-hosted runner  
   GitHub Actions 작업이 실제로 돌아가는 내 PC나 서버입니다.
3. Docker  
   봇을 항상 같은 환경에서 실행하게 해주는 작업 공간입니다.
4. Codex  
   실제 코드 작업을 하는 도구입니다.

실제 동작은 대략 이렇게 흘러갑니다.

1. GitHub에서 댓글 이벤트가 발생합니다.
2. runner가 작업을 받습니다.
3. Docker 안에서 봇이 실행됩니다.
4. Codex가 코드를 수정합니다.
5. 검증을 통과하면 PR을 만듭니다.

## 가장 쉬운 설치 순서

초보자라면 아래 순서대로만 따라가면 됩니다.

1. 설치 매니저 설치
2. GitHub App 만들기
3. self-hosted runner가 돌 호스트 준비
4. 대상 저장소에 봇 설정 적용
5. 첫 멘션 테스트

아래부터는 이 순서대로 자세히 설명합니다.

## 1. 설치 매니저 설치

가장 먼저 할 일은 `issue-to-pr-bot-manager`를 설치하는 것입니다.

현재 저장소에서 설치:

```powershell
python -m pip install .
```

원격 저장소에서 바로 설치:

```powershell
python -m pip install git+https://github.com/IncleRepo/issue-to-pr-bot.git
```

설치 확인:

```powershell
issue-to-pr-bot-manager --help
```

이 매니저가 해주는 일은 다음과 같습니다.

- 대상 저장소에 workflow 설치
- 최소 `.issue-to-pr-bot.yml` 생성
- GitHub 변수/시크릿 등록
- runner 준비 상태 점검
- runner 다운로드와 등록 자동화
- `gh`, `git`이 없으면 자동 설치 시도
- 저장소별 외부 context 폴더, secret env 파일 경로 자동 준비

반대로 매니저가 대신 못 하는 일도 있습니다.

- GitHub App 생성
- GitHub App 설치
- Docker 설치
- Codex 로그인

이 네 가지는 사용자가 직접 해야 합니다.

## 2. GitHub App 만들기

GitHub에서 새 GitHub App을 만듭니다.

예시:

- App 이름: `my-issue-to-pr-bot`

중요한 점:

- App 이름이 곧 멘션 이름입니다.
- 위 예시라면 댓글에서 `@my-issue-to-pr-bot`으로 부릅니다.

### 권한 설정

Repository permissions:

- `Contents` -> Read and write
- `Issues` -> Read and write
- `Pull requests` -> Read and write
- `Metadata` -> Read-only
- `Workflows` -> Read and write

### 이벤트 설정

- `Issue comment`
- `Pull request review`
- `Pull request review comment`

### 미리 확보할 값

App을 만든 뒤 아래 세 가지를 준비합니다.

- 멘션 이름  
  예: `@my-issue-to-pr-bot`
- App ID  
  예: `123456`
- Private key PEM 파일  
  예: `C:\keys\my-app.pem`

그리고 이 App을 실제로 쓸 대상 저장소에 설치합니다.

## 3. 호스트 준비

호스트는 self-hosted runner가 실제로 돌아갈 PC나 서버입니다.

필요한 것:

- Windows
- Python
- Git
- Docker
- Codex CLI

### Codex 로그인

Codex는 먼저 로그인되어 있어야 합니다.

보통 아래 파일이 있으면 됩니다.

- `%USERPROFILE%\.codex\auth.json`
- `%USERPROFILE%\.codex\config.toml`

### GitHub CLI 로그인

`gh`가 설치되어 있고 로그인되어 있으면 매니저가 저장소 변수와 시크릿을 자동 등록할 수 있습니다.

로그인:

```powershell
gh auth login
```

### runner 준비를 매니저로 처리하기

가장 쉬운 방법은 `bootstrap-host`를 쓰는 것입니다.

```powershell
issue-to-pr-bot-manager bootstrap-host `
  --runner-root C:\actions-runner `
  --repo IncleRepo/my-target-repo `
  --run-as-service
```

이 명령이 하는 일:

- Python, Git, Docker, Codex 상태 확인
- Git, `gh`가 없으면 자동 설치 시도
- runner 바이너리가 없으면 다운로드와 압축 해제
- runner 등록 토큰이 없으면 `gh`로 자동 발급 시도
- self-hosted runner 등록
- 선택 시 Windows 서비스 설치 및 시작

중요:

- Codex 로그인이 안 되어 있으면 먼저 로그인하라는 메시지를 내고 멈춥니다.
- Docker 설치 자체는 자동으로 하지 않습니다.
- `gh` 로그인 없이도 `--runner-token`을 직접 넘기면 runner 등록은 가능합니다.

### runner가 제대로 뜨는지 확인

GitHub 저장소에서 아래로 들어가 확인합니다.

- `Settings > Actions > Runners`

정상이라면 runner가 online 상태로 보입니다.

## 4. 대상 저장소에 봇 붙이기

이제 봇을 붙일 저장소를 준비합니다.

예시 저장소:

- `IncleRepo/my-target-repo`

로컬에 clone:

```powershell
git clone https://github.com/IncleRepo/my-target-repo.git
cd my-target-repo
```

그리고 아래 명령을 실행합니다.

```powershell
issue-to-pr-bot-manager bootstrap `
  --target C:\path\to\my-target-repo `
  --repo IncleRepo/my-target-repo `
  --bot-mention @my-issue-to-pr-bot `
  --bot-app-id 123456 `
  --bot-app-private-key-file C:\keys\my-app.pem `
  --write-config `
  --runner-root C:\actions-runner
```

이 명령이 해주는 일:

- workflow 설치
- 최소 `.issue-to-pr-bot.yml` 생성
- GitHub 변수/시크릿 등록
- `gh`가 없으면 자동 설치 시도
- 저장소별 기본 외부 context 폴더와 secret env 파일 경로 생성
- 현재 준비 상태 점검

### 기본으로 만들어지는 값

`bootstrap` 또는 `configure-github`를 실행하면 아래 저장소 변수도 같이 잡습니다.

- `BOT_CONTEXT_DIR_HOST`
- `BOT_SECRETS_FILE_HOST`

기본 경로 예시:

```text
C:\Users\<내계정>\issue-to-pr-bot-data\<owner>__<repo>\context
C:\Users\<내계정>\issue-to-pr-bot-data\<owner>__<repo>\secrets.env
```

처음에는 이 기본 경로를 그대로 써도 충분합니다.

### 실제로 대상 저장소에 생기는 파일

기본 설치 시 보통 아래 파일이 생깁니다.

```text
.github/workflows/issue-comment.yml
.github/workflows/pull-request-review.yml
.github/workflows/pull-request-review-comment.yml
.issue-to-pr-bot.yml
```

리뷰 자동화가 필요 없으면 더 최소로 설치할 수도 있습니다.

```powershell
issue-to-pr-bot-manager init `
  --target C:\path\to\my-target-repo `
  --write-config `
  --skip-review-workflows
```

## 5. 변경 내용 commit, push

대상 저장소에서:

```powershell
git add .
git commit -m "chore: bot 초기 설정"
git push
```

중요:

- workflow는 기본 브랜치에 올라가 있어야 실제로 동작합니다.

## 6. 첫 테스트

대상 저장소에서 이슈를 하나 만듭니다.

예시:

- 제목: `README에 로컬 실행 방법 추가`
- 본문: `README.md에 로컬 실행 방법을 추가해줘.`

그 다음 댓글에 이렇게 적습니다.

```text
@my-issue-to-pr-bot README에 로컬 실행 방법 추가해줘
```

정상 동작하면 봇이:

1. 댓글을 읽고
2. 저장소 문서를 읽고
3. 코드를 수정하고
4. 검증을 돌리고
5. 브랜치를 push하고
6. PR을 만들고
7. 결과를 댓글로 남깁니다

## 자주 쓰는 명령

처음 설치:

```powershell
issue-to-pr-bot-manager bootstrap `
  --target C:\repo `
  --repo IncleRepo/my-target-repo `
  --bot-mention @my-issue-to-pr-bot `
  --bot-app-id 123456 `
  --bot-app-private-key-file C:\keys\my-app.pem `
  --write-config `
  --runner-root C:\actions-runner
```

workflow만 설치:

```powershell
issue-to-pr-bot-manager init --target C:\repo --write-config
```

workflow 갱신:

```powershell
issue-to-pr-bot-manager update --target C:\repo --write-config
```

상태 점검:

```powershell
issue-to-pr-bot-manager doctor `
  --target C:\repo `
  --repo IncleRepo/my-target-repo `
  --runner-root C:\actions-runner
```

GitHub 변수/시크릿만 등록:

```powershell
issue-to-pr-bot-manager configure-github `
  --repo IncleRepo/my-target-repo `
  --bot-mention @my-issue-to-pr-bot `
  --bot-app-id 123456 `
  --bot-app-private-key-file C:\keys\my-app.pem
```

호스트 준비:

```powershell
issue-to-pr-bot-manager bootstrap-host `
  --runner-root C:\actions-runner `
  --repo IncleRepo/my-target-repo `
  --run-as-service
```

미리 보기:

```powershell
issue-to-pr-bot-manager bootstrap --target C:\repo --write-config --dry-run
```

## `.issue-to-pr-bot.yml`

이 파일은 없어도 기본 동작은 됩니다. 처음에는 최대한 단순하게 두는 편이 좋습니다.

최소 예시:

```yaml
bot:
  output_dir: "bot-output"
```

이 파일을 수정하는 경우는 보통 이런 때입니다.

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

중요:

- `required_context_paths`
- `required_secret_env`

이 두 값은 문서에서 자동 추론하지 않습니다. `.issue-to-pr-bot.yml`에 적었을 때만 강제됩니다.

## 어떤 요청을 보낼 수 있나

예시:

- `@my-issue-to-pr-bot README 문구를 다듬어줘`
- `@my-issue-to-pr-bot 이 리뷰 반영해줘`
- `@my-issue-to-pr-bot main 반영하고 충돌 해결해줘`
- `@my-issue-to-pr-bot 승인되면 머지해줘`

## 실패하면 어떻게 되나

실패하면 가능한 한 GitHub 댓글에 원인을 남기도록 되어 있습니다.

예를 들면:

- 검증 실패
- GitHub API 실패
- Docker 이미지 빌드 실패
- 컨테이너 시작 실패
- 필수 context 누락
- 필수 secret 누락

즉 예전보다 “왜 실패했는지”를 Actions 로그를 열기 전에도 파악하기 쉬운 편입니다.

## 로컬 개발

로컬 검증:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## 한 번에 보는 체크리스트

1. `issue-to-pr-bot-manager` 설치
2. GitHub App 생성
3. App 권한과 이벤트 설정
4. App을 대상 저장소에 설치
5. Codex 로그인
6. `bootstrap-host` 실행
7. `bootstrap` 실행
8. 대상 저장소 commit / push
9. 이슈나 PR에서 봇 멘션 테스트

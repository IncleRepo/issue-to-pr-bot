# issue-to-pr-bot

GitHub 이슈, PR 댓글, 리뷰 댓글에서 봇을 멘션하면 Codex가 작업을 수행하고 결과를 PR로 올려주는 자동화 봇 엔진입니다.

이 저장소는 봇 엔진 저장소입니다. 실제 설치는 GitHub App, Cloudflare Worker, 로컬 agent를 함께 써서 진행합니다. 코드를 실제로 수정하는 작업은 각 사용자 PC의 Codex가 담당하고, Cloudflare Worker는 이벤트를 받아 작업을 분배하는 역할만 맡습니다.

## 이 프로젝트를 쓰면 어떻게 되나

설치가 끝나면 사용자는 보통 아래처럼 씁니다.

1. GitHub 이슈를 만든다.
2. 이슈 댓글이나 PR 댓글에 봇을 멘션한다.
3. 봇이 브랜치를 만들고 코드를 수정한다.
4. 검증까지 끝나면 PR을 만들거나 기존 PR을 갱신한다.

예시:

```text
@my-issue-to-pr-bot README에 로컬 실행 방법 추가해줘
```

## 이 프로젝트가 해주는 일

- 이슈 댓글 요청 처리
- PR 댓글, 리뷰 댓글, 리뷰 본문 요청 처리
- 코드 수정
- 검증 실행
- 브랜치 push
- PR 생성 또는 기존 PR 갱신
- 라벨, 담당자, 리뷰어, 마일스톤 자동 반영
- 실패 사유 댓글 보고
- 승인 후 merge 시도

## 준비물

처음에는 아래 네 가지만 준비하면 됩니다.

1. GitHub App  
   댓글에서 부르는 봇 이름입니다. 예를 들어 App 이름이 `my-issue-to-pr-bot`이면 댓글에서 `@my-issue-to-pr-bot`으로 부릅니다.
2. Cloudflare Worker  
   GitHub webhook을 받아 작업 큐를 관리합니다.
3. 로컬 agent  
   내 PC에서 계속 돌면서 작업을 가져와 실행합니다.
4. Codex  
   실제 코드 수정과 리뷰 반영을 수행합니다.

## 동작 흐름

실제 동작은 대략 이렇게 흘러갑니다.

1. GitHub에서 댓글 이벤트가 발생합니다.
2. Cloudflare Worker가 webhook을 받고 작업을 큐에 넣습니다.
3. 로컬 agent가 작업을 가져옵니다.
4. Codex가 코드를 수정합니다.
5. 검증을 통과하면 PR을 만듭니다.

## 가장 쉬운 설치 순서

초보자라면 아래 순서대로만 따라가면 됩니다.

1. 설치 매니저 설치
2. GitHub App 만들기
3. Cloudflare Worker 제어면 만들기
4. 내 PC에 로컬 agent 설정
5. 대상 저장소 최소 파일 준비
6. 대상 저장소에 App 설치
7. 첫 멘션 테스트

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

- Cloudflare Worker 제어면 스캐폴드 생성
- 로컬 agent 설정 파일 생성
- 최소 `.issue-to-pr-bot.yml` 생성
- `gh`, `git`이 없으면 자동 설치 시도

반대로 매니저가 대신 못 하는 일도 있습니다.

- GitHub App 생성
- GitHub App 설치
- Codex 로그인
- Cloudflare 계정 생성과 Worker 배포 승인

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

## 3. Cloudflare Worker 제어면 만들기

제어면은 GitHub webhook을 받아서 작업을 큐에 쌓는 중앙 창구입니다.

가장 빠른 방법은 매니저로 Worker 프로젝트 뼈대를 만든 다음 Cloudflare에 배포하는 것입니다.

```powershell
issue-to-pr-bot-manager init-control-plane `
  --target C:\issue-to-pr-bot-control `
  --worker-name issue-to-pr-bot-control `
  --bot-mention @my-issue-to-pr-bot
```

이 명령을 실행하면 아래 파일이 만들어집니다.

```text
package.json
wrangler.jsonc
README.md
src/index.js
```

그 다음 Worker 디렉터리에서 아래 순서로 진행합니다.

```powershell
cd C:\issue-to-pr-bot-control
npm install
```

Cloudflare에서 KV namespace를 하나 만들고, `wrangler.jsonc`의 `TASK_QUEUE` id를 채웁니다.

그 다음 secret을 넣습니다.

```powershell
npx wrangler secret put CONTROL_PLANE_AGENT_TOKEN
npx wrangler secret put GITHUB_WEBHOOK_SECRET
npx wrangler secret put GITHUB_APP_ID
npx wrangler secret put GITHUB_APP_PRIVATE_KEY
```

마지막으로 배포합니다.

```powershell
npx wrangler deploy
```

배포가 끝나면 `https://...workers.dev` 주소가 생깁니다. 이 주소가 제어면 URL입니다.

한 번에 묶고 싶으면 아래 명령을 써도 됩니다.

```powershell
issue-to-pr-bot-manager bootstrap-control-plane `
  --target C:\issue-to-pr-bot-control `
  --worker-name issue-to-pr-bot-control `
  --bot-mention @my-issue-to-pr-bot `
  --github-app-id 123456 `
  --github-app-private-key-file C:\keys\my-app.pem
```

이 명령은 아래를 한 번에 처리합니다.

- Worker 프로젝트 파일 생성
- `npm install`
- KV namespace 생성과 `wrangler.jsonc` 반영
- Worker secret 등록
- `wrangler deploy`

실행이 끝나면 매니저가 아래 값을 같이 출력합니다.

- Worker URL
- agent token
- webhook secret

GitHub App 설정 화면에서는 Webhook URL을 아래처럼 넣습니다.

```text
https://<workers 주소>/github/webhook
```

## 4. 로컬 agent 준비

이제 실제 Codex를 돌릴 PC를 준비합니다. 이 PC가 작업을 받아서 코드 수정, 검증, push까지 수행합니다.

필요한 것:

- Windows
- Python
- Git
- Codex CLI
- `gh`

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

### agent 설정 파일 만들기

매니저로 agent 설정을 만듭니다.

```powershell
issue-to-pr-bot-manager bootstrap-agent `
  --control-plane-url https://issue-to-pr-bot-control.example.workers.dev `
  --agent-token <CONTROL_PLANE_AGENT_TOKEN 값> `
  --repository IncleRepo/my-target-repo `
  --workspace-root C:\issue-to-pr-bot-workspaces
```

이 명령이 하는 일:

- agent 설정 파일 생성
- 작업용 workspace 루트 준비
- 어떤 저장소를 이 PC가 처리할지 기록
- 기본 로그 파일 경로 생성
- Windows 작업 스케줄러에 agent 자동 시작 등록

기본 로그 파일 경로:

```text
%USERPROFILE%\.issue-to-pr-bot-agent\logs\agent.log
```

기본 작업 스케줄러 이름:

```text
issue-to-pr-bot-agent
```

즉시 한번 실행해보고 싶으면:

```powershell
schtasks /Run /TN "issue-to-pr-bot-agent"
```

작업 스케줄러 등록을 원하지 않으면 `--skip-task`를 붙이면 됩니다. 그 경우에는 직접 아래 명령으로 agent를 실행하면 됩니다.

```powershell
issue-to-pr-bot-agent start
```

상태 확인:

```powershell
issue-to-pr-bot-agent status
```

중지:

```powershell
issue-to-pr-bot-agent stop
```

## 5. 대상 저장소 최소 파일 준비

Cloudflare Worker + 로컬 agent 경로에서는 대상 저장소에 별도 workflow를 넣지 않아도 됩니다. 대신 최소 문서 파일만 두는 편이 좋습니다.

가장 간단한 초기화는 이 명령입니다.

```powershell
issue-to-pr-bot-manager init-target-repo `
  --target C:\path\to\my-target-repo
```

이 명령은 기본적으로 아래 파일을 만듭니다.

```text
.issue-to-pr-bot.yml
AGENTS.md
```

이 두 파일만 있어도 봇은 기본 규칙과 검증 명령을 읽고 작업할 수 있습니다.

그 다음 GitHub App을 이 저장소에 설치하면 됩니다.

### 메타데이터도 자동으로 붙일 수 있나

됩니다. 방식은 두 가지입니다.

1. `AGENTS.md`, `README.md`, `CONTRIBUTING.md` 같은 문서에 규칙을 적어둔다.
2. 그런 규칙이 없으면 봇이 이슈 내용, 변경 파일, `CODEOWNERS`를 보고 추론한다.

예를 들어 `AGENTS.md`에 이렇게 적어둘 수 있습니다.

```md
## Metadata

Issue labels: `bug`
PR labels: `automation`, `bot`
Assignees: `@alice`
Reviewers: `@bob`
Team reviewers: `@my-org/backend`
Milestone: `Sprint 1`
```

그러면 봇이 이슈와 PR을 만들거나 갱신할 때 위 메타데이터를 같이 반영합니다.

문서에 규칙이 없으면 아래 정도는 자동 추론합니다.

- 문서 변경 위주 -> `documentation`
- 버그 수정 성격 -> `bug`
- 리팩토링 성격 -> `refactor`
- 테스트 위주 변경 -> `tests`
- 자동화나 workflow 변경 -> `automation`
- `CODEOWNERS`가 있으면 reviewer 또는 team reviewer 요청

## 6. 변경 내용 commit, push

대상 저장소에서:

```powershell
git add .
git commit -m "chore: bot 초기 설정"
git push
```

## 7. 첫 테스트

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

전체 초기 설정 한 번에:

```powershell
issue-to-pr-bot-manager bootstrap-all `
  --target C:\issue-to-pr-bot-control `
  --worker-name issue-to-pr-bot-control `
  --bot-mention @my-issue-to-pr-bot `
  --github-app-id 123456 `
  --github-app-private-key-file C:\keys\my-app.pem `
  --repository IncleRepo/my-target-repo `
  --workspace-root C:\issue-to-pr-bot-workspaces `
  --target-repo C:\path\to\my-target-repo
```

대상 저장소 최소 파일 준비:

```powershell
issue-to-pr-bot-manager init-target-repo `
  --target C:\repo
```

제어면 스캐폴드 생성:

```powershell
issue-to-pr-bot-manager init-control-plane `
  --target C:\issue-to-pr-bot-control `
  --worker-name issue-to-pr-bot-control `
  --bot-mention @my-issue-to-pr-bot
```

제어면 자동 배포:

```powershell
issue-to-pr-bot-manager bootstrap-control-plane `
  --target C:\issue-to-pr-bot-control `
  --worker-name issue-to-pr-bot-control `
  --bot-mention @my-issue-to-pr-bot `
  --github-app-id 123456 `
  --github-app-private-key-file C:\keys\my-app.pem
```

로컬 agent 설정:

```powershell
issue-to-pr-bot-manager bootstrap-agent `
  --control-plane-url https://issue-to-pr-bot-control.example.workers.dev `
  --agent-token <CONTROL_PLANE_AGENT_TOKEN 값> `
  --repository IncleRepo/my-target-repo `
  --workspace-root C:\issue-to-pr-bot-workspaces
```

기본 로그 파일:

```text
%USERPROFILE%\.issue-to-pr-bot-agent\logs\agent.log
```

기본 작업 스케줄러 이름:

```text
issue-to-pr-bot-agent
```

즉시 실행:

```powershell
schtasks /Run /TN "issue-to-pr-bot-agent"
```

작업 스케줄러를 쓰지 않으려면 직접 실행:

```powershell
issue-to-pr-bot-agent start
```

상태 확인:

```powershell
issue-to-pr-bot-agent status
```

상태 점검:

```powershell
issue-to-pr-bot-manager doctor `
  --target C:\repo `
  --workspace-root C:\issue-to-pr-bot-workspaces `
  --control-plane-url https://issue-to-pr-bot-control.example.workers.dev `
  --config-path C:\Users\<내계정>\.issue-to-pr-bot-agent\agent-config.json
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

즉 예전보다 “왜 실패했는지”를 GitHub 댓글만 보고도 파악하기 쉬운 편입니다.

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
4. `init-control-plane` 실행
5. Worker secret 등록 후 `wrangler deploy`
6. `bootstrap-agent` 실행
7. `issue-to-pr-bot-agent serve` 실행
8. `init-target-repo` 실행
9. App을 대상 저장소에 설치
10. 이슈나 PR에서 봇 멘션 테스트

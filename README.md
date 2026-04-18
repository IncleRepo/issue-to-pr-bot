# issue-to-pr-bot

GitHub 이슈, PR 댓글, 리뷰 댓글에서 봇을 멘션하면 작업을 받아 브랜치를 만들고, 코드를 수정하고, PR까지 이어주는 자동화 봇입니다.

이 저장소는 봇 엔진을 관리하는 저장소입니다. 대상 저장소에는 전체 엔진 코드를 넣지 않고, 필요한 설정 파일만 둡니다. 실제 작업은 각 사용자 PC에 설치된 agent가 맡습니다.

## 구조

한 번의 요청은 네 레이어를 거칩니다.

1. **GitHub App**
2. **Cloudflare Worker control plane**
3. **local agent**
4. **Codex**

겉으로는 하나의 봇처럼 보이지만, 실제로는 이벤트를 받는 쪽과 코드를 만지는 쪽이 분리되어 있습니다. 이 구조 덕분에 중앙 제어와 로컬 실행을 나눠서 운영할 수 있습니다.

### GitHub App

GitHub App은 저장소에서 일어나는 이벤트를 바깥으로 전달하는 입구입니다.

- 이슈 댓글
- PR 댓글
- 리뷰 본문
- 리뷰 코멘트

이벤트가 생기면 GitHub가 App에 연결된 webhook URL로 payload를 보냅니다. App 자체가 코드를 수정하지는 않습니다. 역할은 어디까지나 이벤트와 저장소 문맥을 바깥으로 넘기는 것입니다.

### Cloudflare Worker control plane

Cloudflare Worker는 중앙 제어면입니다. 실제 코드를 수정하지 않고, 다음 역할만 맡습니다.

- GitHub webhook 수신
- 멘션 여부와 기본 권한 확인
- 작업 큐 적재
- agent 인증
- agent 중앙 설정 배포
- GitHub App installation token 발급

즉 Worker는 실행기가 아니라 접수와 분배를 담당하는 조정기입니다.

### local agent

local agent는 사용자 PC에서 실제 작업을 수행하는 프로세스입니다.

agent는 다음 일을 맡습니다.

- control plane 연결
- task polling / claim
- 작업용 workspace 준비
- 저장소 clone / fetch / checkout
- Codex 실행
- 검증 실행
- PR 생성 또는 기존 PR 업데이트
- 로그 기록

실제로 Git 저장소를 만지고, 파일을 수정하고, 커밋하고, push하는 주체는 agent입니다.

### Codex

Codex는 agent가 준비한 workspace 안에서 호출되는 코드 생성·수정 엔진입니다.

흐름은 보통 이렇습니다.

1. agent가 workspace와 작업 브랜치를 준비합니다.
2. 저장소 문서와 요청 문맥을 모아 prompt를 만듭니다.
3. Codex가 관련 코드를 읽고 변경을 만듭니다.
4. agent가 검증, push, PR 반영을 마무리합니다.

지금 구조에서는 로컬 workspace 안의 작업 판단은 Codex가 주도하고, 원격 publish와 merge는 wrapper가 감독합니다.

### Workspace / Runtime 경계

경로 책임은 다음처럼 나눕니다.

- workspace 안: Codex가 직접 읽고 쓰는 작업용 입력/출력
- workspace 밖 runtime: wrapper 내부 메타데이터와 Codex 세션 홈 같은 내부 상태

기본 경로는 이렇습니다.

```text
<workspace>/.issue-to-pr-bot/input/attachments/comment-<comment_id>/...
<workspace>/.issue-to-pr-bot/output/pr-title.txt
<workspace>/.issue-to-pr-bot/output/pr-body.md
<workspace>/.issue-to-pr-bot/output/pr-summary.md
<workspace-root>/.issue-to-pr-bot-runtime/<repo>/<scope>/workspace-meta.json
<workspace-root>/.issue-to-pr-bot-runtime/<repo>/<scope>/codex-home-root/...
```

중요:

- `.issue-to-pr-bot/input/`와 `.issue-to-pr-bot/output/` 아래 파일은 모두 작업용 파일이며 커밋 대상이 아닙니다.
- agent는 각 workspace의 `.git/info/exclude`로 이 경로들을 로컬에서 제외합니다.
- fresh 요청이 오면 workspace 저장소 전체를 지우지 않고 `input`, `output`, runtime의 `codex-home`만 초기화합니다.

### 전체 흐름

1. 사용자가 GitHub 이슈나 PR에서 봇을 멘션합니다.
2. GitHub App이 이벤트를 Worker로 전달합니다.
3. Worker가 이벤트를 검사하고 task queue에 넣습니다.
4. local agent가 queue에서 task를 가져옵니다.
5. agent가 작업용 workspace를 만들고 저장소를 준비합니다.
6. agent가 Codex를 호출해 변경을 만듭니다.
7. agent가 검증, push, PR 생성을 처리합니다.
8. 결과 댓글이 GitHub에 남습니다.

한 문장으로 줄이면 이렇습니다.

- **GitHub App**: 이벤트를 전달합니다.
- **Worker**: 중앙에서 접수하고 분배합니다.
- **agent**: 사용자 PC에서 실제 작업을 수행합니다.
- **Codex**: 코드 변경과 작업 보조를 맡습니다.

## 사용 흐름

보통은 이렇게 씁니다.

1. GitHub 이슈나 PR에 댓글을 답니다.
2. Worker가 요청을 큐에 넣습니다.
3. local agent가 작업을 가져옵니다.
4. Codex가 저장소를 수정하고 검증합니다.
5. 봇이 PR을 만들거나 기존 PR을 업데이트합니다.
6. 결과를 댓글로 남깁니다.

예시:

```text
@my-issue-to-pr-bot README에 로컬 실행 방법 추가해줘
```

## 대상 저장소에 들어가는 것

대상 저장소에는 보통 이것만 있으면 됩니다.

- `.issue-to-pr-bot.yml`
- `AGENTS.md`
- 필요하면 PR/이슈 템플릿

예제는 [examples/target-repo](examples/target-repo)에 있습니다.

## 설치 개요

보통은 GitHub Releases에서 installer를 받아 시작합니다.

1. OS에 맞는 installer를 받습니다.
2. installer가 필요한 의존성과 수동 단계가 끝났는지 확인합니다.
3. installer가 agent 실행 파일과 설정을 설치합니다.
4. 대상 저장소에 최소 설정 파일을 넣습니다.
5. GitHub App을 저장소에 연결하고 첫 요청을 보냅니다.

## 1. installer 받기

릴리즈 페이지:

- https://github.com/IncleRepo/issue-to-pr-bot/releases/tag/v0.3.0

GitHub Releases에서 OS에 맞는 installer를 받습니다.

- Windows: `issue-to-pr-bot-installer-windows-x64.zip`
- Ubuntu/Linux: `issue-to-pr-bot-installer-linux-x64.tar.gz`

### Windows

압축을 풀고 실행 파일을 바로 실행하면 됩니다.

### Ubuntu / Linux

브라우저에서 받아도 되고, 터미널에서 바로 받아도 됩니다.

standalone installer/agent는 Python 없이 바로 실행할 수 있습니다.
소스 코드로 직접 실행하거나 테스트할 때는 Python `3.11+`가 필요합니다.
특히 Ubuntu 20.04 기본 Python `3.8`만으로는 실행되지 않습니다.

`curl` 예시:

```bash
curl -L -o issue-to-pr-bot-installer-linux-x64.tar.gz \
  https://github.com/IncleRepo/issue-to-pr-bot/releases/download/v0.3.0/issue-to-pr-bot-installer-linux-x64.tar.gz

tar -xzf issue-to-pr-bot-installer-linux-x64.tar.gz
chmod +x issue-to-pr-bot-installer
./issue-to-pr-bot-installer
```

`wget` 예시:

```bash
wget -O issue-to-pr-bot-installer-linux-x64.tar.gz \
  https://github.com/IncleRepo/issue-to-pr-bot/releases/download/v0.3.0/issue-to-pr-bot-installer-linux-x64.tar.gz

tar -xzf issue-to-pr-bot-installer-linux-x64.tar.gz
chmod +x issue-to-pr-bot-installer
./issue-to-pr-bot-installer
```

installer는 다음을 확인하고 필요한 안내를 이어갑니다.

- OS 확인
- 필수 의존성 확인
- Codex 로그인 상태 확인
- 최신 agent 다운로드
- agent 설정 파일 생성
- 자동 시작 등록

설치가 끝나면 `issue-to-pr-bot-agent`를 바로 실행할 수 있어야 합니다.

## 2. GitHub App 만들기

GitHub에서 새 GitHub App을 만듭니다.

예:

- App 이름: `my-issue-to-pr-bot`
- App ID
- private key PEM 파일

중요한 점:

- App 이름이 곧 멘션 이름입니다.
- App 이름이 `my-issue-to-pr-bot`이면 댓글에서는 `@my-issue-to-pr-bot`으로 부릅니다.

권한:

- `Contents` -> Read and write
- `Issues` -> Read and write
- `Pull requests` -> Read and write
- `Metadata` -> Read-only
- `Workflows` -> Read and write

이벤트 구독:

- `Issue comment`
- `Pull request review`
- `Pull request review comment`

## 3. Cloudflare Worker 제어면 배포

가장 쉬운 방법은 중앙 매니저로 control plane을 만들고 바로 배포하는 것입니다.

```powershell
issue-to-pr-bot-manager bootstrap-control-plane `
  --target C:\issue-to-pr-bot-control `
  --worker-name issue-to-pr-bot-control `
  --bot-mention @my-issue-to-pr-bot `
  --github-app-id 123456 `
  --github-app-private-key-file C:\keys\my-app.pem `
  --agent-repository IncleRepo/my-target-repo `
  --agent-poll-interval-seconds 10 `
  --agent-max-concurrency 2
```

이 명령은 다음을 처리합니다.

- Worker 프로젝트 생성
- `npm install`
- KV namespace 준비
- Worker secret 등록
- `wrangler deploy`

출력에서 확인할 값:

- Worker URL
- webhook URL
- webhook secret
- agent token

GitHub App 설정에는 아래를 넣습니다.

- Webhook URL: `https://<worker-url>/github/webhook`
- Secret: bootstrap 결과로 나온 `webhook secret`

## 4. local agent 설정

이제 실제 코드 작업을 수행할 PC를 연결합니다.

```powershell
issue-to-pr-bot-manager bootstrap-agent `
  --control-plane-url https://issue-to-pr-bot-control.example.workers.dev `
  --agent-token <CONTROL_PLANE_AGENT_TOKEN> `
  --repository IncleRepo/my-target-repo `
  --workspace-root C:\issue-to-pr-bot-workspaces `
  --poll-interval-seconds 10 `
  --max-concurrency 2
```

이 명령은 installer가 설치한 agent를 기준으로 다음을 준비합니다.

- agent 설정 파일 생성
- workspace 루트 준비
- 로그 경로 준비
- 자동 시작 등록

기본 로그 파일:

```text
%USERPROFILE%\.issue-to-pr-bot-agent\logs\agent.log
```

Ubuntu에서는 기본 로그 파일이 아래 경로입니다.

```text
~/.issue-to-pr-bot-agent/logs/agent.log
```

### agent 실행

포그라운드 실행:

```powershell
issue-to-pr-bot-agent
```

실행하면 콘솔 안에서 아래 명령을 바로 쓸 수 있습니다.

- `ps`
- `status`
- `logs latest`
- `logs latest -f`
- `logs <task-id>`
- `logs <task-id> -f`
- `cancel <task-id>`
- `stop all`
- `help`
- `quit`

### 자동 시작

- Windows에서는 작업 스케줄러로 등록합니다.
- Ubuntu에서는 `systemd --user` 서비스로 등록합니다.

Ubuntu에서 바로 확인:

```bash
systemctl --user start issue-to-pr-bot-agent
systemctl --user status issue-to-pr-bot-agent
```

## 5. 대상 저장소 초기화

대상 저장소에는 최소 파일만 넣습니다.

```powershell
issue-to-pr-bot-manager init-target-repo `
  --target C:\path\to\my-target-repo
```

기본 생성 파일:

- `.issue-to-pr-bot.yml`
- `AGENTS.md`

GitHub Actions workflow를 대상 저장소에 넣지 않습니다.

## 6. GitHub App을 대상 저장소에 설치

위에서 만든 GitHub App을 실제 대상 저장소에 설치합니다.

여기까지 끝나면 최소 동작 준비는 끝입니다.

## 7. 첫 테스트

이슈를 하나 만들고 댓글이나 PR 리뷰에서 이렇게 요청합니다.

```text
@my-issue-to-pr-bot README에 로컬 실행 방법 추가해줘
```

정상이라면:

1. Worker가 webhook을 받습니다.
2. agent가 task를 가져옵니다.
3. Codex가 workspace 안에서 작업합니다.
4. wrapper가 push와 PR 생성을 처리합니다.
5. 결과 댓글이 GitHub에 남습니다.

## 병렬 처리와 task 운영

agent는 한 번에 여러 task를 처리할 수 있습니다. 다만 같은 이슈나 같은 PR처럼 같은 작업 범위를 건드리는 task는 충돌을 막기 위해 순차 처리합니다.

- `--agent-max-concurrency`: control plane에서 내려주는 중앙 동시 실행 수
- `--max-concurrency`: local fallback 동시 실행 수

중앙 설정이 있으면 중앙 값이 우선입니다.

같은 저장소라도 서로 다른 이슈나 서로 다른 PR이면 별도 workspace에서 병렬로 처리할 수 있습니다.

### 실행 중인 task 보기

```text
ps
```

### 특정 task 로그 보기

```text
logs <task-id>
```

실시간 tail:

```text
logs <task-id> -f
```

가장 최근 task 로그 보기:

```text
logs latest -f
```

### task 취소

```text
cancel <task-id>
```

중요:

- `logs -f`에서 `Ctrl+C`를 누르면 로그 보기만 종료됩니다.
- 실제 작업을 중단하려면 `cancel <task-id>`를 써야 합니다.

## 자연어 키워드

자연어 그대로 적어도 되지만, 아래 표현을 쓰면 더 안정적으로 인식합니다.

### 기본 실행

아래 표현은 기본적으로 `run`으로 처리합니다.

- `구현해줘`
- `수정해줘`
- `반영해줘`
- `고쳐줘`
- `추가해줘`

예:

```text
@my-issue-to-pr-bot 저기 버그 수정해줘
```

### 계획만 세우기

아래 표현은 `plan`으로 처리합니다.

- `계획만`
- `계획 세워줘`
- `단계만`
- `정리만`

예:

```text
@my-issue-to-pr-bot 계획만 세워줘
```

### 상태 확인

아래 표현은 `status`로 처리합니다.

- `status`
- `상태`
- `상태 확인`
- `어디까지`

### 머지 요청

아래 표현은 merge 의도로 처리합니다.

- `머지해줘`
- `승인되면 머지해줘`
- `merge해줘`

### effort 힌트

- `간단히`, `살짝`, `한 줄`, `사소` -> `low`
- 기본값 -> `medium`
- `깊게`, `크게`, `복잡하게`, `전역적으로`, `대대적으로` -> `high`
- `최대로`, `아주 깊게`, `xhigh` -> `xhigh`

### 검증 관련 표현

- `검증 없이`
- `테스트는 나중에`
- `빌드까지 확인`
- `컴파일까지 봐줘`

### merge / sync 관련 표현

- `충돌 해결`
- `main 반영`
- `최신 main 맞춰서`
- `rebase해서`
- `merge해서`

## `.issue-to-pr-bot.yml`

대상 저장소 루트에 두는 설정 파일입니다.

형식:

```yaml
bot:
  output_dir: "bot-output"
```

지원 키:

- `output_dir`
  - Codex 작업 산출물을 둘 디렉터리 이름
- `context_paths`
  - 항상 함께 읽을 저장소 내부 문서/설정 경로 목록
- `external_context_paths`
  - 외부 컨텍스트 디렉터리 경로 목록
- `required_context_paths`
  - 없으면 작업을 중단할 필수 문서 경로 목록
- `secret_env_keys`
  - Codex에 알려줄 사용 가능한 환경변수 이름 목록
- `required_secret_env`
  - 없으면 작업을 중단할 필수 환경변수 이름 목록
- `check_commands`
  - wrapper가 최종 검증에 사용할 명령 목록
- `protected_paths`
  - publish 시 보호할 경로 목록
- `base_branch`
  - 기본 기준 브랜치
- `git_sync_phase`
  - 문서 추론 없이 고정할 sync 시점
- `git_sync_action`
  - 문서 추론 없이 고정할 sync 방식
- `git_sync_base_branch`
  - sync 대상 브랜치
- `git_sync_require_conflict_free`
  - 충돌 없이 끝나야 하는지 여부

권장 최소 설정:

```yaml
bot:
  output_dir: "bot-output"
  context_paths:
    - ".issue-to-pr-bot.yml"
    - "AGENTS.md"
    - "README.md"
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
  check_commands:
    - "python -m compileall -q app tests"
    - "python -m unittest discover -s tests"
  protected_paths:
    - "secrets/**"
  base_branch: "main"
  git_sync_phase: "before_merge"
  git_sync_action: "merge"
  git_sync_base_branch: "main"
  git_sync_require_conflict_free: true
```

이 값들은 문서 자동 추론과 별개로 `.issue-to-pr-bot.yml`의 명시값으로 바로 적용합니다.

## 예제 파일

대상 저장소 예제는 [examples/target-repo](examples/target-repo)에 모아두었습니다.

포함 예시:

- `.issue-to-pr-bot.yml.example`
- `AGENTS.md.example`
- `.github/pull_request_template.md.example`
- `.github/ISSUE_TEMPLATE/bot-task.yml.example`
- `.github/ISSUE_TEMPLATE/config.yml.example`

## 문제가 생기면 먼저 볼 것

1. local agent 로그
2. Worker URL과 webhook 설정
3. GitHub App 권한과 이벤트 구독
4. agent 설정 파일
5. 대상 저장소의 `.issue-to-pr-bot.yml`과 `AGENTS.md`

agent 로그:

- Windows: `%USERPROFILE%\.issue-to-pr-bot-agent\logs\agent.log`
- Ubuntu: `~/.issue-to-pr-bot-agent/logs/agent.log`

## 가장 빠른 전체 설치

control plane, local agent, 대상 저장소 초기화를 한 번에 하고 싶다면 아래처럼 진행할 수 있습니다.

```powershell
issue-to-pr-bot-manager bootstrap-all `
  --target C:\issue-to-pr-bot-control `
  --worker-name issue-to-pr-bot-control `
  --bot-mention @my-issue-to-pr-bot `
  --github-app-id 123456 `
  --github-app-private-key-file C:\keys\my-app.pem `
  --repository IncleRepo/my-target-repo
```

## 배포 방식

배포 채널은 GitHub Releases입니다.

- installer: `issue-to-pr-bot-installer`
- agent: `issue-to-pr-bot-agent`

### installer 역할

- OS 감지
- 필수 의존성 검사
- Codex 로그인 등 사용자 수동 단계 점검
- 최신 agent 다운로드
- agent 설정 파일 생성
- 자동 시작 등록

### agent 업데이트

- agent 설정에는 `managed_runtime_path`, `managed_runtime_version`, `release_repository`가 저장됩니다.
- `serve` 콘솔에서 `update`를 입력하면 GitHub Releases 기준 최신 agent를 확인합니다.
- Linux는 즉시 교체합니다.
- Windows는 현재 프로세스가 종료되면 staged 바이너리로 교체한 뒤 다시 시작합니다.

### 실행 파일 빌드

PyInstaller가 준비되어 있다면 아래 명령으로 installer와 agent를 onefile 바이너리로 빌드할 수 있습니다.

```powershell
python scripts/build_standalone.py --role all --clean
```

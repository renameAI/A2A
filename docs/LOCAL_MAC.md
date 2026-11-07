# 맥에서 A2A 브라우저 쓰기 (백엔드까지 내 맥에)

브라우저(에이전트·메리·업무 지도·EMA)와 백엔드(팀 채팅 엔진·Postgres·Redis)를 **내 맥 안에서** 돌립니다.
데이터는 밖으로 나가지 않습니다. 사람끼리 공유되지도 않습니다(공유 서버는 다음 단계).

## 처음 한 번

1. **준비**
   - Xcode 명령줄 도구: `xcode-select --install`
     - git이 "license"로 막히면 `sudo xcodebuild -license accept`
   - Homebrew: https://brew.sh 의 설치 한 줄
2. **받기**
   ```bash
   git clone https://github.com/renameAI/A2A.git && cd A2A
   ```
3. **설치**
   ```bash
   ./setup-mac.sh
   ```
   - 묻는 것 두 가지
     - **내 이름**: 영문 소문자. 팀 채팅과 업무 기록에 쓰입니다.
     - **ANTHROPIC_API_KEY**: 입력이 화면에 보이지 않습니다. 엔터를 누르면 나중에 넣습니다.
   - 하는 일
     - Homebrew로 postgresql@17·redis·node@22·uv를 설치하고, Postgres·Redis를 상시 실행으로 켭니다.
     - 로컬 DB `a2a_control`·`a2a_cell_a`·`a2a_cell_b`를 만듭니다.
     - Python 가상환경(`.venv`)과 패키지를 설치합니다.
     - `.env`를 채우고, 팀 채팅 시드(워크스페이스 TEST, 내가 관리자)를 넣습니다.
     - 브라우저 npm 설치와 빌드를 합니다.
   - 다시 돌려도 안전합니다. 이미 있는 것은 건너뛰고, `.env`에 **있는 값은 절대 덮어쓰지 않습니다.**
   - 무엇을 할지 미리 보려면 `./setup-mac.sh --check` (아무것도 바꾸지 않음)

## 매일

```bash
./run-mac.sh            # 엔진(8423)·워커를 띄우고 브라우저를 엽니다. 브라우저를 닫으면 엔진도 꺼집니다
./run-mac.sh --backend  # 백엔드만
```

로그는 `data/logs/engine.log`, `data/logs/worker.log`에 남습니다.

## 브라우저 안에서
- **EMA(FRN):** 마이페이지 → 연결된 도구 → EMA 로그인(사람마다 한 번)
- **메리:** 즐겨찾기 "메리". 지시 붙여넣기, 자료 첨부, 함께 쓰기를 여기서 합니다.
- **업무 지도:** ⇧⌘G. SSoT 임시 발행도 여기서 봅니다.

## 환경 변수 (`.env` 하나 — 엔진과 브라우저가 같이 읽음)

| 키 | 값(설치가 채움) | 뜻 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 직접 입력 | 없으면 AI 기능(에이전트·메리)이 꺼집니다 |
| `A2A_USER` · `A2A_TENANT` | 내 이름 · `test` | 브라우저가 쓰는 팀 채팅 사용자·워크스페이스 |
| `SAAS_AUTH=dev` · `SAAS_ALLOWED_USERS` | 내 이름 + 시드 사용자 | **로컬 전용 개발 인증.** 목록 밖 사용자는 403 |
| `CHAT_CONTROL_DSN` · `DEV_CELL_A` · `DEV_CELL_B` | 로컬 Postgres | 팀 채팅 컨트롤 플레인과 셀 |
| `CHAT_FANOUT=redis` · `CHAT_REDIS_URL` | 로컬 Redis | 실시간 전파 |
| `SAAS_STORE=local` · `SAAS_DB_PATH` · `CHAT_ARCHIVE_DIR` | `data/…` | 로컬 파일 저장소 |

`.env`는 커밋되지 않습니다(`.gitignore`). 키를 채팅이나 코드에 붙여넣지 마세요.

## 알아 둘 것
- **기록:** 웹 본문, 채팅, 에이전트 작업을 `~/Library/Application Support/a2a-browser/events`에 기록합니다. 은행·메일·정부 사이트 같은 제외 도메인은 `browser/collect.config.json`에 있습니다.
- **개발용 인증:** `SAAS_AUTH=dev`는 내 맥 안(127.0.0.1)에서만 쓰는 개발용 인증입니다. 엔진을 바깥에 열지 마세요.
- **설치가 기존 도구를 바꾸지 않음:** 다른 Node·Python이 이미 깔려 있어도 괜찮습니다. 스크립트는 Homebrew의 node@22를 경로 앞에 두고 실행합니다.

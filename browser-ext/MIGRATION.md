# Electron → Chromium 확장 이전 계획

목표: Electron 앱(`A2A/browser`)에서 만든 **A2A만의 기능**을 Chromium 위에서 도는 확장(MV3)으로 옮긴다.
나중에 우리 Chromium 빌드에 그대로 **내장 확장(component extension)**으로 싣는다 — 그래서 "확장 목록에 보인다/권한 창이 뜬다"에 기대지 않는다.

## 무엇을 옮기고 무엇을 Chromium에 맡기나

| Electron에서 만든 것 | 처리 | 이유 |
|---|---|---|
| 세로 탭·주소창·배경화면 셸 | **Chromium에 맡김**(세로 탭은 Chromium 내장 기능) / 배경화면은 새 탭에만 | 브라우저 UI는 확장으로 못 바꾼다 — 나중에 C++ |
| 비밀번호 금고·채우기 | **Chromium에 맡김** | 크롬 비밀번호 관리자(키체인)가 더 강함, 패스키도 됨 |
| 다운로드·방문 기록 화면 | **옮김 → 사이드 패널(채팅방 자리)**, 데이터는 Chromium 것 | `chrome.downloads`·`chrome.history` — 화면은 Electron 때와 같게 |
| 찾기·확대·세션 복구·닫은 탭·탭 단축키·인쇄·전체 화면·팝업·권한·크롬 즐겨찾기 | **Chromium에 맡김** | 이미 있음 |
| AI 채팅방·팀 채널(Room) | **옮김 → 사이드 패널** | `chrome.sidePanel` |
| 새 탭 "무엇을 맡길까요?"·최근 작업·배경화면 | **옮김 → 새 탭 대체** | `chrome_url_overrides.newtab` |
| 에이전트(도구 루프·승인 게이트·데이터 경계·RISKY·URL 가드) | **옮김 → 서비스 워커 + 탭 조작** | `chrome.scripting`(격리 월드) + `chrome.debugger`(사람과 같은 입력·스크린샷) |
| 결과 페이지(shadcn/ui) | **옮김 → 확장 페이지** | 명세는 해시 대신 `chrome.storage.session` |
| @태그·보내는 규칙·채널에서 부르기 | **옮김** | 화면 코드 그대로 |
| 마이페이지·프로필(계정×워크스페이스)·아바타 색 | **옮김** | 화면 코드 그대로 |
| 오른쪽 클릭 "AI에게 물어보기" | **옮김** | `chrome.contextMenus` |
| 승인 요청·멘션 알림 | **옮김** | `chrome.notifications` |
| 팀 채팅 실시간(SSE) | **옮김 → 사이드 패널·새 탭 페이지에서** | MV3 서비스 워커는 오래 못 살아 긴 연결은 페이지가 맡고, 닫혀 있을 땐 1분마다 안 읽음 확인(`chrome.alarms`) |
| 이벤트 수집(events.js)·업무 지도(ontology.js)·하네스(harness.js)·업무 유형·SSoT | **옮김 → 서비스 워커(sw/knowledge.js) + 업무 지도 페이지(map.html)** | browser/ 모듈을 그대로 번들, Node API는 src/shim/(IndexedDB) |
| Claude API 키 | 개발용: `chrome.storage.local` · 운영: 엔진 프록시 | Electron 때와 같은 한계(앱에 키) |

## 구조
```
browser-ext/
  manifest.json              MV3 — side_panel, newtab, background SW, permissions
  src/bridge.js              window.a2a(send/on/invoke)를 chrome.runtime 포트로 — React 코드를 그대로 쓰게
  src/sw/                    서비스 워커: 상태·탭·작업·프로필·에이전트(main.js의 확장판)
  src/sidepanel/             Room·MyPage·프로필 메뉴(화면)
  src/newtab/                NewTab
  src/page/                  결과 페이지(shadcn/ui)
  src/shared/                agent·page-tools·format·Markdown 등 공용
  test/harness.mjs           Chromium을 확장과 함께 띄워 CDP로 시험
```

> **2026-10-28 — M6까지 하고 멈춤.** 브라우저 틀(탭 줄·주소창·카드 화면·왼쪽 사이드바)은 확장으로 못 바꿔서,
> 당분간 Electron(`A2A/browser`)에서 기능·화면을 다듬는다. 다시 시작할 때: Electron에서 바뀐 화면(`renderer/src`)·
> 에이전트 규칙(`agent.js`)을 여기(`src/ui`·`src/sw/agent.js`)로 맞추고 `npm run build && npm test`(26개)부터.
> 개발 창: `node scripts/dev.mjs`(띄우기) · `reload` · `shot`.

## 단계(하나씩 — 매 단계 빌드·시험 통과 뒤 다음)
- [x] M1. 뼈대: manifest·Vite 빌드·서비스 워커·다리(bridge)·빈 사이드 패널/새 탭, Chromium에 실어 열리는지 시험
- [x] M2. 상태: 탭(chrome.tabs)·작업·설정·프로필을 서비스 워커가 들고 화면에 보냄 · 사이드 패널에 Electron 채팅방 화면 · 방문 기록/다운로드(Chromium 데이터)
- [x] M3. 팀 채팅: 채널 목록·메시지·실시간·보내기·반응·안 읽음, 보내는 규칙 · 워크스페이스 바꾸기 · 툴바 배지(@n/안 읽음) · 알림
- [x] M4. 에이전트: 도구 루프·페이지 읽기/클릭/입력/이동·승인 게이트·데이터 경계, 사이드 패널에 작업 과정 · 알림의 [승인]/[거절] · 패널을 닫았다 열어도 대화 유지 · 채널에서 @에이전트
- [x] M5. 결과 페이지(create_page)·결과 카드 · 닫힌 결과 페이지를 카드에서 다시 열기
- [x] M4b. 사이드 패널을 넓히면 왼쪽에 채팅 목록(2단) — 진짜 3단은 우리 빌드(C++)에서
- [x] M6. @태그(탭·결과·채널·사람)·채널에서 @에이전트·오른쪽 클릭 "AI에게 물어보기/이 링크·이 페이지 맡기기" · AI 방에서 사람 태그 → 팀 채널로(AI는 답하지 않음)
- [ ] M7. 마이페이지·프로필 고르기·아바타 색·알림
- [ ] M8. 새 탭(무엇을 맡길까요·최근 작업·배경화면)
- [x] K. 업무 기록·업무 지도·하네스 — 웹 방문·에이전트 실행·승인·팀 채팅이 지도에 쌓이고, 채택한 교훈은 안전 규칙 앞에 붙는다(시험 k1~k5)
- [ ] M9. 전체 시험(함정 페이지 인젝션 무시·승인·채널 게시 등 Electron 때와 같은 항목) + 문서

원칙: 화면(UI/UX)은 Electron 때와 최대한 같게. Chromium은 바탕일 뿐 — 그 위에 우리 기능을 계속 더한다.

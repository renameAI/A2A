# DocEditor — 메리 투자심사보고서 본문 편집기

`renderer/src/merry/DocEditor.jsx` (+ `doc-editor.css`). 새 의존성 없음 — `package.json`은 그대로입니다.

```jsx
import DocEditor from './merry/DocEditor.jsx';

<DocEditor
  html={sec.html}                 // 토큰이 살아 있는 원문. sec.rendered(숫자를 채운 글)를 넘기면 안 됩니다
  vars={data.vars}                // { 이름: { display } } — invest/index.js가 이미 이 모양으로 줍니다
  citeNo={citeNo}                 // (선택) 보고서 전체 근거 번호. 없으면 섹션 안 순서로 1, 2, 3…
  onCite={onCite}                 // (factId) => void
  onSave={(h) => inv('saveSectionHtml', deal.id, key, h)}
  readOnly={busy || !!live}       // 메리가 쓰는 중에는 잠근다
/>
```

`.mr-page.mr-doc` 종이 **안에** 두면 됩니다. 장 제목(`h1.mr-chapter`)과 쪽 번호는 밖에 둡니다. 편집기 쪽 CSS는 캐럿과 선택색, 칩, 떠 있는 도구 막대만 다루고, 종이 모양은 `merry.css` 그대로 씁니다.

## 계약

| prop | 동작 |
|---|---|
| `html` | 제한 HTML. `[f:id]`는 위첨자 근거 번호 칩, `{{var:이름}}`은 계산값 칩(안을 고칠 수 없음), `[확인 필요: …]`는 빨간 칩(콜론 뒤 글만 고칠 수 있음)으로 보입니다. 칩은 ⌫/⌦ 한 번에 통째로 지워집니다 |
| `onSave(html)` | 1.5초 쉬면, 포커스가 빠지면, ⌘S를 누르면 부릅니다. 토큰은 원래 글자로 되돌리고, 허용한 태그·클래스(`div.box` `div.caption` `td.num`)·`colspan`/`rowspan`만 남깁니다. **고친 게 없으면 받은 문자열을 한 글자도 바꾸지 않고 돌려줍니다.** 쉼이나 포커스 빠짐 때는 바뀐 게 없으면 아예 부르지 않고, ⌘S는 늘 부릅니다. 한글 조합 중에는 부르지 않습니다 |
| `readOnly` | 편집과 도구 막대를 끕니다. 근거 번호 누르기(`onCite`)는 그대로 됩니다 |
| `vars` | 바뀌면 계산값 칩 글자만 고칩니다. 값이 없으면 변수 이름을 빨간 기울임으로 보여 줍니다 |
| `html`이 밖에서 바뀔 때 | 우리가 저장한 글이 되돌아온 것이면 무시합니다. 사람이 고치는 중(포커스 + 저장 안 됨)이면 손 글이 이기고, 포커스가 빠질 때 새 글이 그대로 남아 있을 때만 다시 그립니다. 그 밖에는 바로 다시 그립니다 |

`cleanPaste(html)`, `normalizeHtml(html)`도 함께 내보냅니다. `normalizeHtml`은 편집기가 저장하는 정규형으로 맞춰 주므로, 사람이 고쳤는지 비교할 때 씁니다.

## 키

| 키 | 동작 |
|---|---|
| ⌘Z / ⇧⌘Z (⌘Y) | 되돌리기 / 다시하기. 자체 기록이고, 0.7초 또는 문단 단위로 묶습니다 |
| ⌘B ⌘I ⌘U | 굵게 / 기울임 / 밑줄 |
| ⌘⌥0 ⌘⌥1 ⌘⌥2 ⌘⌥3 | 본문 / 절(h2) / 항(h3) / ■ 소제목(h4). 같은 단계를 한 번 더 누르면 본문으로 |
| ⌘⇧8 ⌘⇧7 | 글머리 목록 / 번호 목록(켜고 끄기) |
| 문단 맨 앞 `- ` `* ` `1. ` | 자동 목록. ⌘Z를 한 번 누르면 `- `로 돌아갑니다 |
| Tab / ⇧Tab | 목록에서는 들여쓰기 / 내어쓰기, 표에서는 다음 / 이전 칸. 마지막 칸에서 Tab을 누르면 새 행 |
| 항목 맨 앞 ⌫, 빈 항목에서 ↵ | 글머리만 빼서 목록을 끝냅니다 |
| ⇧⌘V | 서식 없이 붙여넣기 |
| ⌘S | 바로 저장 |
| Esc | 편집 끝내기(포커스 빼기 → 저장) |

글을 고르거나 표 안에 있으면 그 위에 작은 도구 막대가 뜹니다: 본문 · 절 · 항 · ■ 소제목 · B I U · 목록 · 번호, 표 안에서는 ＋ 위 행 · ＋ 아래 행 · 행 삭제.
단축키는 `e.key`가 아니라 `e.code`로 봅니다. 한글 자판에서는 ⌘B의 `e.key`가 'ㅠ'로 들어오기 때문입니다.

## Google Docs에서 본 것과 가져온 것

**조사 방법.** 버려도 되는 headless Chrome(포트 9444, 임시 프로필, 로그인 안 함)으로 공개 문서를 열었습니다. 문서는 Chromium 설계 문서 “Design Doc: WebSocket Protocol Stack in chrome/net”입니다. 여기에 REA를 붙여 다음을 했습니다.
- `inspect_web_page`: DOM 2,558개를 확인했습니다. iframe 5개 중 2개는 `tabindex`와 `aria-hidden`이 있고 src가 없는 입력용 iframe이었고, 본문은 canvas 3개였습니다. 접근성 트리의 편집 영역 역할은 `application`이었습니다.
- `inspect_web_event_listeners .kix-appview-editor`: 편집 영역 하나에 `pointerdown`(capture), `keydown`/`keyup`, `synthetic-keydown`, `mousedown`/`move`/`up`, `click`, `contextmenu`, `scroll`이 모두 직접 걸려 있었습니다. 출처는 모두 `kix_core` 묶음 한 군데였습니다.
- 그 리스너들이 가리키는 `kix_core` 원문(3.6MB)에서 편집 관련 문자열을 찾아 앞뒤를 읽었습니다.

보기 전용으로 열었기 때문에 실제로 타자를 쳐서 관찰하지는 못했습니다. 키 동작 표는 Docs의 공개 단축키(⌘⌥1–6, ⌘⇧7/8, ⌘⇧V, 표 Tab)를 따랐습니다.

| Docs에서 본 것(근거) | 여기서 한 것 | 이유 |
|---|---|---|
| 입력과 그리기를 나눕니다. 키와 IME는 숨은 contenteditable iframe(`docs-texteventtarget-iframe`)이 받고, 글은 canvas 타일에 그립니다 | **따라 하지 않았습니다.** 실제 DOM 위의 contenteditable 하나를 씁니다 | 보고서는 제한 HTML이고, 인쇄·PDF와 `merry.css` 종이 모양을 그대로 써야 합니다. canvas 렌더러는 조판기를 통째로 다시 만드는 일입니다 |
| 리스너는 편집 영역 하나에 모읍니다(`.kix-appview-editor`) | 키·입력·조합·붙여넣기·복사·끌어 놓기·클릭을 본문 요소 하나에 native 리스너로 겁니다. React 합성 이벤트는 쓰지 않습니다 | React의 `onBeforeInput`은 진짜 `beforeinput`이 아닙니다. 본문 DOM을 React가 다시 그리지 않아야 IME가 깨지지 않습니다 |
| `beforeinput`을 `inputType`별 처리기 표로 나눕니다(`this.D[a.inputType]`: `insertText`, `insertReplacementText`, `deleteByComposition`) | `beforeinput`에서 `historyUndo`/`historyRedo`(편집 메뉴)를 가로채 자체 기록으로 보내고, 타자 묶음이 시작되는 자리를 기록합니다 | 크롬 기본 되돌리기 기록은 DOM을 직접 바꾸면(칩 삭제, 정리) 꼬입니다 |
| 조합 상태를 따로 둡니다(`composing_region`, `composing_decoration`, `isComposing()`) | 조합 중에는 DOM 정리, 저장, 되돌리기 기록, 단축키를 모두 멈추고(`e.isComposing \|\| keyCode 229`), `compositionend` 뒤에 한꺼번에 처리합니다. 밑줄은 브라우저 기본 표시를 씁니다 | 조합 중 DOM을 만지면 ‘ㅎ한’ 같은 자모 중복이 생깁니다 |
| 복사할 때 전용 MIME(`application/x-vnd.google-docs-document-slice-clip+wrapped`)과 `text/html`, `text/plain`을 함께 씁니다 | 복사·잘라내기 때 `application/x-merry-doc`(토큰 그대로)과 `text/html`, `text/plain`을 씁니다. 붙여넣을 때는 이 MIME을 먼저 봅니다 | 편집기끼리는 칩이 토큰째로 오가고, 다른 앱에는 보이는 글자가 갑니다 |
| 복사한 HTML을 `<b style="font-weight:normal" id="docs-internal-guid-…">`로 감쌉니다(정규식 `docs-internal-guid-([^>]+?)`) | 붙여넣을 때 `font-weight:normal`인 `<b>`는 굵게로 보지 않고 벗깁니다. Docs는 굵게를 `<span style="font-weight:700">`으로 주므로 style에서 굵게·기울임·밑줄만 읽어 `strong`·`em`·`u`로 바꾸고, 나머지 style·class·id·dir는 버립니다 | 이 처리가 없으면 Docs에서 붙인 글 전체가 굵어지거나 글꼴·색이 따라 들어옵니다 |
| 캐럿을 직접 그립니다(`kix-cursor-caret`, `docs-text-ui-cursor-blink`) | 브라우저 캐럿에 `caret-color`를 주고, 선택색은 Docs와 비슷한 옅은 파랑(rgb(66 133 244 / .26))으로 합니다. 테두리 있는 입력 상자는 쓰지 않습니다 | 종이 위에서 편집 티가 나지 않게 하려는 것입니다 |
| 스마트 칩은 한 덩어리로 다룹니다 | 근거와 계산값은 `contenteditable=false` 칩이고, 칩 바로 앞이나 뒤에서 ⌫/⌦를 누르면 직접 지웁니다. 확인 필요 칩은 바깥이 잠겨 있고, 안쪽 글만 `plaintext-only`로 고칠 수 있습니다 | 토큰이 반쯤 지워진 `[f:` 같은 찌꺼기가 남지 않게 합니다 |
| 명령은 문서 모델에 적용합니다 | 제목 단계, 목록, 들여쓰기, 표 행은 `execCommand` 대신 직접 구현했습니다 | 실험해 보니 크롬 `formatBlock`과 `insertUnorderedList`는 칩이 든 문단을 `<p><h3>…</h3><sup>`, `<p><ul>…</ul></p>`로 깨뜨렸습니다. 굵게·기울임·밑줄과 삭제만 `execCommand`를 쓰고, 그 뒤 `tidy()`가 `<b>`, `<span style>`, `<div>`를 정리합니다 |
| (공개 동작) 자동 목록, 표에서 Tab 이동과 새 행, ⇧⌘V | 위 키 표대로 | 사람들이 이미 손에 익은 동작입니다 |

## 의존성을 넣지 않은 이유

TipTap/ProseMirror를 쓰면 스키마, 되돌리기, IME가 단단해집니다. 그래도 넣지 않은 이유는 다음과 같습니다.
1. 문서는 태그 20개짜리 제한 HTML과 토큰 세 개뿐이라 지금 코드(약 1,000줄)로 충분합니다.
2. 저장 형식이 “받은 글자 그대로 돌려주기”라서 ProseMirror 스키마를 거치면 정규형 차이를 따로 메워야 합니다.
3. 의존성을 추가하려면 `package.json`을 바꾸고 승인을 받아야 합니다.

다음 같은 기능이 필요해지면 ProseMirror(`prosemirror-model`, `-state`, `-view`, `-history`, 약 100KB gz)로 옮기는 편이 낫습니다. 이 컴포넌트의 props 계약은 그대로 둘 수 있습니다.
- 표 칸 합치기와 나누기
- 동시 편집
- 사람 수정 이벤트를 문자 단위로 기록하기

## 알려진 한계

- 끌어서 옮기기(drag-move)는 막았습니다. 복사·잘라내기로 옮깁니다. 바깥에서 끌어 놓은 글은 붙여넣기처럼 정리해서 넣습니다.
- `ul` 안 `ul`처럼 원문에 있던 잘못된 중첩은 고치지 않고 그대로 저장합니다. 편집기가 직접 만드는 들여쓰기는 `li > ul`입니다.
- 표 머리 칸(`th`) 안에서는 도구 막대의 B가 켜진 것처럼 보입니다. CSS가 머리 칸을 굵게 그리기 때문입니다.
- 사람 수정을 업무 지도·온톨로지 이벤트로 남기는 일은 통합 쪽 몫입니다. `onSave`가 넘겨주는 전후 HTML(`saveSectionHtml`이 이미 `byAgent:false`로 기록)을 쓰면 됩니다.

## 자체 점검

```bash
npx electron selftest-doceditor.js
```

일회용 Electron 창(숨김, `userData`는 임시 폴더)에서 esbuild로 묶은 하네스를 띄우고 CDP로 입력을 넣습니다. 켜져 있는 앱(9555)과 `ui/` 빌드는 건드리지 않습니다. `A2A_SELFTEST_DIR=<폴더>`를 주면 결과(`result.json`), 붙여넣기 결과, 최종 HTML, 스크린샷을 그 폴더에 남깁니다.

점검 항목(17개):
- 칩 표시
- 손대지 않고 포커스가 빠지면 저장 0번, ⌘S 저장은 받은 HTML과 같음, 정규형 고정점
- 한글 `insertText`, IME 조합(`imeSetComposition` ㅎ→하→한, ㄱ→그→글)과 조합 중 저장 0번, 1.5초 자동 저장
- 되돌리기와 다시하기
- 근거·계산값 칩 통째 삭제, 삭제 되돌리기, 근거 누르기
- Google Docs·Word·스크립트가 섞인 붙여넣기 정리
- ⌘B와 ⌘⌥2
- 칩이 든 문단의 목록, 들여쓰기·내어쓰기, 자동 목록
- 표 Tab 새 행과 도구 막대
- 최종 저장의 토큰 보존

#!/bin/zsh
# A2A Browser = Chromium + 우리 패치(patches/) + 내장 확장(browser-ext → 앱 번들 Resources/a2a).
#   ./chromium/build.sh            확장 빌드 → Chromium 소스에 복사 → 패치 확인 → autoninja
#   CHROMIUM_SRC=~/dev/chromium/src A2A_JOBS=8 ./chromium/build.sh
# 결과: $CHROMIUM_SRC/out/A2A/Chromium.app (실행하면 사이드 패널·에이전트·팀 채팅·업무 지도가 이미 들어 있다)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${CHROMIUM_SRC:-$HOME/dev/chromium/src}"
OUT="${A2A_OUT:-out/A2A}"
export PATH="${DEPOT_TOOLS:-$HOME/dev/depot_tools}:$PATH"

# 1) 패치 — 이미 들어 있으면 건너뛴다(새 Chromium으로 올렸을 때 안 맞으면 여기서 멈춘다)
for p in "$ROOT"/chromium/patches/*.patch; do
  if git -C "$SRC" apply --reverse --check "$p" 2>/dev/null; then echo "✓ 패치 적용됨: ${p:t}"
  else git -C "$SRC" apply --3way "$p" && echo "✓ 패치 적용: ${p:t}"; fi
done

# 2) 내장 확장 — browser-ext를 빌드해 //a2a/extension 으로
( cd "$ROOT/browser-ext" && npm run build >/dev/null )
mkdir -p "$SRC/a2a"
rsync -a --delete "$ROOT/browser-ext/dist/" "$SRC/a2a/extension/"
# 폴더째 싣는 bundle_data는 안의 파일이 바뀐 걸 모른다 — 번들 안 사본을 지워 다시 복사되게
rm -rf "$SRC/$OUT/Chromium.app/Contents/Frameworks/Chromium Framework.framework/Versions/"*/Resources/a2a(N)
echo "✓ 내장 확장 → //a2a/extension"

# 3) 빌드
cd "$SRC"
[[ -f "$OUT/args.gn" ]] || { echo "먼저 $OUT/args.gn을 만드세요 (browser/CHROMIUM_PLAN.md)"; exit 1; }
gn gen "$OUT" >/dev/null
caffeinate -dims autoninja -j "${A2A_JOBS:-8}" -C "$OUT" chrome
echo "✓ $SRC/$OUT/Chromium.app"

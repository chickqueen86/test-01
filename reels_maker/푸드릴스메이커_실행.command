#!/bin/bash
cd "$(dirname "$0")"
echo "━━━━━━━━━━━━ 푸드 릴스 메이커 ━━━━━━━━━━━━"
HERE="$(pwd)"; WORK="$HERE/작업"; mkdir -p "$WORK"
# ---------- 준비 (처음 한 번) ----------
if ! command -v ffmpeg >/dev/null; then
  if ! command -v brew >/dev/null; then
    echo "Homebrew 설치 중 (맥 비밀번호를 물어보면 입력)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
  fi
  echo "ffmpeg 설치 중..."; brew install ffmpeg
fi
command -v python3 >/dev/null || { command -v brew >/dev/null && brew install python; }
if [ ! -d .venv ] || ! .venv/bin/python -c "import faster_whisper, yaml" 2>/dev/null; then
  echo "필요한 프로그램 설치 중 (몇 분 걸림)..."
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt || { echo "설치 실패. 이 창을 캡처해서 보내주세요."; read; exit 1; }
fi

clean(){ local p="$1"; p="${p%"${p##*[![:space:]]}"}"; p="${p//\\ / }"; p="${p#\'}"; p="${p%\'}"; echo "$p"; }

setup_brand(){  # 대행사 브랜드 (왼쪽 위 로고 + 엔딩 카드). 한 번 설정하면 계속 사용
  echo ""; echo "대행사 브랜드 설정 (영상 왼쪽 위 로고 + 마지막 엔딩 카드)"
  read -r -p "로고 이미지(PNG, 배경 투명 권장)를 끌어다 놓거나, 글자로 쓰려면 Enter: " LOGO; LOGO="$(clean "$LOGO")"
  if [ -n "$LOGO" ] && [ -f "$LOGO" ]; then
    cp "$LOGO" "$HERE/brand_logo.png"
    printf 'brand:\n  logo_image: brand_logo.png\n  logo_width: 200\n' > "$HERE/brand.yaml"
  else
    read -r -p "로고 대신 쓸 글자 (없으면 Enter → 로고 없이 제작): " BN
    if [ -n "$BN" ]; then printf 'brand:\n  parts:\n    - ["%s", "#FFFFFF"]\n' "$BN" > "$HERE/brand.yaml"
    else printf 'brand:\n  parts: []\n' > "$HERE/brand.yaml"; fi
  fi
  echo "저장했습니다. 바꾸려면 메뉴 4번."
}

pick_store(){   # 작업 폴더 목록에서 매장 선택
  local i=1; STORES=()
  for d in "$WORK"/*/; do [ -d "$d" ] && { STORES+=("${d%/}"); echo "  $i) $(basename "$d")"; i=$((i+1)); }; done
  [ ${#STORES[@]} -eq 0 ] && { echo "매장 폴더가 없습니다. 먼저 1번으로 만드세요."; read; exit 1; }
  read -r -p "매장 번호: " n; STORE_DIR="${STORES[$((n-1))]}"
}

[ -f "$HERE/brand.yaml" ] || setup_brand
echo ""
echo "1) 새 매장 폴더 만들기"
echo "2) 대본 만들기     (1_촬영 → 2_대본)"
echo "3) 영상 완성하기   (1_촬영 + 3_녹음 → 4_완성)"
echo "4) 대행사 로고/브랜드 바꾸기"
read -r -p "번호 선택: " MENU

if [ "$MENU" = "1" ]; then
  read -r -p "매장 이름: " NAME
  D="$WORK/${NAME// /}_$(date +%Y%m%d)"
  mkdir -p "$D/1_촬영" "$D/2_대본" "$D/3_녹음" "$D/4_완성"
  echo "$NAME" > "$D/.store_name"
  echo "만들었습니다: $D"
  echo "→ 1_촬영 폴더에 촬영 영상을 넣은 뒤 2번을 실행하세요."
  open "$D/1_촬영"
elif [ "$MENU" = "2" ]; then
  pick_store
  command -v claude >/dev/null || { echo "Claude Code(claude 명령)가 필요합니다. 설치·로그인 후 다시 실행하세요."; read; exit 1; }
  NAME="$(cat "$STORE_DIR/.store_name" 2>/dev/null || basename "$STORE_DIR")"
  read -r -p "알고 있는 정보 (메뉴·가격 등, 없으면 Enter): " NOTE
  .venv/bin/python write_script.py "$STORE_DIR/1_촬영" "$NAME" --note "$NOTE" --out "$STORE_DIR/2_대본" \
    && open "$STORE_DIR/2_대본/script.md" && echo "→ 대본을 녹음해서 3_녹음 폴더에 넣은 뒤 3번을 실행하세요."
elif [ "$MENU" = "3" ]; then
  pick_store
  NAR="$(ls "$STORE_DIR/3_녹음"/* 2>/dev/null | grep -iv bgm | grep -iE '\.(m4a|mp3|wav|aac|mp4|mov)$' | head -1)"
  MUSIC="$(ls "$STORE_DIR/3_녹음"/* 2>/dev/null | grep -i bgm | head -1)"
  [ -z "$NAR" ] && { echo "3_녹음 폴더에 나레이션 파일이 없습니다."; read; exit 1; }
  echo "나레이션: $(basename "$NAR")"; [ -n "$MUSIC" ] && echo "BGM: $(basename "$MUSIC")"
  ARGS=("$STORE_DIR/1_촬영" "$NAR" --out-dir "$STORE_DIR/4_완성" --script-dir "$STORE_DIR/2_대본")
  [ -n "$MUSIC" ] && ARGS+=(--music "$MUSIC")
  .venv/bin/python auto_edit.py "${ARGS[@]}" && open "$STORE_DIR/4_완성"
elif [ "$MENU" = "4" ]; then
  setup_brand
fi
read -r -p "끝났습니다. Enter를 누르면 창이 닫힙니다."

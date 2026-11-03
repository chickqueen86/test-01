#!/bin/bash
cd "$(dirname "$0")"
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
if [ ! -d .venv ] || ! .venv/bin/python -c "import anthropic, faster_whisper" 2>/dev/null; then
  echo "필요한 프로그램 설치 중 (몇 분 걸림)..."
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt || { echo "설치 실패. 이 창을 캡처해서 보내주세요."; read; exit 1; }
fi
KEYFILE="$HOME/.reels_maker_key"
if [ ! -s "$KEYFILE" ]; then
  echo "Anthropic API 키가 필요합니다 (대본 작성용, 한 번만 입력). https://console.anthropic.com 에서 발급"
  read -r -s -p "API 키 붙여넣기 후 Enter: " K; echo; echo "$K" > "$KEYFILE"; chmod 600 "$KEYFILE"
fi
export ANTHROPIC_API_KEY="$(cat "$KEYFILE")"
clean(){ local p="$1"; p="${p%"${p##*[![:space:]]}"}"; p="${p//\\ / }"; p="${p#\'}"; p="${p%\'}"; echo "$p"; }

# ---------- 메뉴 ----------
echo ""
echo "1) 대본 만들기   (촬영 영상 + 매장 이름 → 녹음용 대본, 표지 문구)"
echo "2) 영상 완성하기 (촬영 영상 + 녹음 파일 → 완성 릴스)"
read -r -p "번호 선택: " MENU
read -r -p "촬영 폴더를 끌어다 놓고 Enter: " MEDIA; MEDIA="$(clean "$MEDIA")"
if [ "$MENU" = "1" ]; then
  read -r -p "매장 이름: " STORE
  read -r -p "알고 있는 정보 (메뉴·가격 등, 없으면 Enter): " NOTE
  .venv/bin/python write_script.py "$MEDIA" "$STORE" --note "$NOTE" && open "$MEDIA/script.md"
else
  read -r -p "나레이션 녹음 파일을 끌어다 놓고 Enter: " NAR
  read -r -p "BGM 파일 (없으면 Enter): " MUSIC
  ARGS=("$MEDIA" "$(clean "$NAR")")
  [ -n "$MUSIC" ] && ARGS+=(--music "$(clean "$MUSIC")")
  .venv/bin/python auto_edit.py "${ARGS[@]}" && open "$MEDIA"
fi
read -r -p "끝났습니다. Enter를 누르면 창이 닫힙니다."

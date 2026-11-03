#!/bin/bash
cd "$(dirname "$0")"
# 1) Homebrew / ffmpeg / python 확인 및 자동 설치
if ! command -v ffmpeg >/dev/null; then
  if ! command -v brew >/dev/null; then
    echo "Homebrew 설치 중 (맥 비밀번호를 물어보면 입력)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
  fi
  echo "ffmpeg 설치 중..."; brew install ffmpeg
fi
command -v python3 >/dev/null || { command -v brew >/dev/null && brew install python; }
# 2) 프로그램 준비 (처음 한 번)
if [ ! -d .venv ]; then
  echo "처음 실행: 필요한 프로그램 설치 중 (몇 분 걸림)..."
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt || { echo "설치 실패. 이 창을 캡처해서 보내주세요."; read; exit 1; }
fi
# 3) 입력 (Finder에서 폴더/파일을 이 창으로 끌어다 놓기)
clean(){ local p="$1"; p="${p%"${p##*[![:space:]]}"}"; p="${p//\\ / }"; p="${p#\'}"; p="${p%\'}"; echo "$p"; }
read -r -p "① 촬영 폴더를 끌어다 놓고 Enter: " MEDIA
read -r -p "② 나레이션 파일을 끌어다 놓고 Enter: " NAR
read -r -p "③ BGM 파일 (없으면 그냥 Enter): " MUSIC
read -r -p "④ 표지 문구 3줄을 | 로 구분 (없으면 Enter): " COVER
ARGS=("$(clean "$MEDIA")" "$(clean "$NAR")")
[ -n "$MUSIC" ] && ARGS+=(--music "$(clean "$MUSIC")")
[ -n "$COVER" ] && ARGS+=(--cover "$COVER")
.venv/bin/python auto_edit.py "${ARGS[@]}" && open "$(clean "$MEDIA")"
read -r -p "끝났습니다. Enter를 누르면 창이 닫힙니다."

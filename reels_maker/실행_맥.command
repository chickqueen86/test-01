#!/bin/bash
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "[오류] ffmpeg가 없습니다. 터미널에서: brew install ffmpeg"; read; exit 1; }
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
read -p "촬영 폴더를 끌어다 놓고 Enter: " MEDIA
read -p "나레이션 파일을 끌어다 놓고 Enter: " NAR
read -p "BGM 파일 (없으면 Enter): " MUSIC
read -p "표지 문구 3줄을 | 로 구분 (없으면 Enter): " COVER
ARGS=("$(eval echo $MEDIA)" "$(eval echo $NAR)")
[ -n "$MUSIC" ] && ARGS+=(--music "$(eval echo $MUSIC)")
[ -n "$COVER" ] && ARGS+=(--cover "$COVER")
.venv/bin/python auto_edit.py "${ARGS[@]}"
read -p "완료. Enter로 종료"

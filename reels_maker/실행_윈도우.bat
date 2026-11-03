@echo off
chcp 65001 >nul
cd /d %~dp0
where ffmpeg >nul 2>nul || (echo [오류] ffmpeg가 없습니다. https://www.gyan.dev/ffmpeg/builds/ 에서 설치 후 PATH에 추가하세요. & pause & exit /b)
if not exist .venv ( python -m venv .venv && .venv\Scripts\pip install -r requirements.txt )
set /p MEDIA=촬영 폴더 경로를 끌어다 놓고 Enter: 
set /p NAR=나레이션 파일을 끌어다 놓고 Enter: 
set /p MUSIC=BGM 파일 (없으면 그냥 Enter): 
set /p COVER=표지 문구 3줄을 ^| 로 구분 (없으면 Enter): 
set ARGS=%MEDIA% %NAR%
if not "%MUSIC%"=="" set ARGS=%ARGS% --music %MUSIC%
if not "%COVER%"=="" set ARGS=%ARGS% --cover "%COVER%"
.venv\Scripts\python auto_edit.py %ARGS%
pause

# 식당 릴스 메이커

촬영 소스(영상/사진) + YAML 문구 → 인스타 릴스(1080x1920, 30fps) 자동 생성.
스타일: 풀스크린 음식 클로즈업 · 좌상단 브랜드 로고 · 하단 중앙 외곽선 자막 · 강조 단어 컬러 · BGM 페이드아웃.

## 설치
- ffmpeg, Python 3.9+
- `pip install pyyaml pillow`
- 굵은 한글 폰트(Pretendard ExtraBold, 나눔스퀘어 EB 등)를 `style.font`에 지정 권장

## 사용
```bash
cd example
python ../make_reel.py project.yaml          # 1개
python make_reel.py 식당A/project.yaml 식당B/project.yaml   # 여러 개 일괄
```

## 장면 옵션
| 키 | 설명 |
|---|---|
| file | 영상 또는 사진 경로 |
| duration | 초 (사진 기본 3초, 영상 기본 최대 4초) |
| start | 영상 시작 지점(초) |
| speed | 영상 배속 (예: 1.5) |
| zoom | 사진 줌인 배율 (기본 1.15) |
| caption / captions | 자막 1개 / 여러 개(장면 시간을 균등 분할) |

자막에서 `\n`으로 줄바꿈, `style.highlight_words`에 있는 단어는 강조색으로 표시됩니다.

## 영상 + 나레이션 → 자동 완성 편집 (`auto_edit.py`)

```bash
pip install faster-whisper
python auto_edit.py 20261103_한우덮밥집/media 나레이션.m4a --music bgm.mp3 [--style 브랜드템플릿.yaml]
```

1. 나레이션을 음성인식해서 14자 안팎의 짧은 자막으로 자름 (단어 단위 싱크)
2. 자막이 바뀔 때마다 컷 전환. 촬영 지침의 파일명(`01_2_best.mp4`)을 읽어 컷 번호 순으로, `_best` 테이크를 우선 사용
3. 영상 길이 = 나레이션 길이. BGM은 나레이션 밑으로 자동으로 낮춤(15%)
4. 결과: `reel.mp4`, `project.yaml`(컷·자막 수정용), `captions.srt`

음성인식 오타(예: 출구 → 줄고)가 있으면 `project.yaml`의 captions만 고치고 `python make_reel.py .../project.yaml`로 다시 렌더링하세요.
정확도가 더 필요하면 `--model medium`을 쓰세요 (느려짐).
나레이션 파일 대신 나레이션이 녹음된 영상 파일을 넣어도 됩니다.

## v2: 레퍼런스 분석 적용 (team/00_통합요약.md 기준)
| 항목 | 적용 내용 |
|---|---|
| 색감 | 레퍼런스 80프레임 측정값(`presets/josun_color.json`)으로 클립별 색 매칭 (`color.strength`, 기본 0.85) |
| 컷 템포 | 구간별 컷 길이: 훅 0.61s → 가격 1.58s(진행자 2.6s 롱컷) → 식감 1.02s → ASMR 1.71s → 다른 메뉴 1.67s |
| 컷 순서 | 촬영지침 컷번호로 구간별 소스 풀 구성, 같은 테이크 연속 사용 금지 |
| 자막 | 나눔명조, 그림자, 화면 45% 지점, 컷과 독립된 나레이션 타임라인, 마침표 제거 |
| 소리 | 클립 현장음 유지(-10dB), BGM 나레이션 대비 -20dB(덕킹 없음), 압축 후 -17.5 LUFS |
| 엔딩 | 검은 화면 + 반짝이 + 로고 + present 1.3초, 자막 없음 |
| 표지 | `--cover "줄1|줄2|줄3"` → 노랑/흰 굵은 고딕 3줄 `reel_cover.jpg` |

녹화본 한계로 미적용: 슬로모션 배율, 정확한 폰트·BGM 템포, 칼날 와이프(촬영으로 만들어야 함).

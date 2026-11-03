#!/usr/bin/env python3
"""영상 + 나레이션 → 완성 릴스 자동 편집 v2 (레퍼런스 분석값 적용).

레퍼런스(진흙오리 릴스, team/00_통합요약.md) 구조를 그대로 따른다.
- 구간 비율 · 컷 템포: 훅 0~9% (0.6초 컷) → 메뉴명·가격 9~20% (가격 공개는 진행자 긴 컷)
  → 가성비·식감 20~42% (1.0초) → ASMR 42~65% (1.7초) → 곁들임·술·다른 메뉴 65~100% (1.65초)
- 컷은 비트가 아니라 시간 템포로, 자막은 컷과 독립된 나레이션 타임라인
- 촬영 지침 컷번호(01~08)로 구간별 소스 풀 구성, 같은 테이크 재사용 허용
- 마지막 1.3초는 엔딩 로고 카드 (마지막 나레이션 "꼭 가보세요"가 그 위에 흐름)

사용법:
  python auto_edit.py 촬영폴더/ 나레이션.m4a [--music bgm.mp3] [--cover "줄1|줄2|줄3"]
"""
import argparse, re, subprocess, sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import make_reel

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}

# (구간명, 끝 비율, 컷 길이초, 촬영지침 컷번호 우선순위)
# 01 훅 클로즈업 02 들어올리기 03 조리과정 04 탑뷰 05 단면·칼질 06 인기증거·진행자 07 외관 08 메뉴판
SECTIONS = [
    ("hook",   0.09, 0.61, [1, 5, 3, 2]),
    ("price",  0.20, 1.58, [5, 6, 7, 2]),
    ("taste",  0.42, 1.02, [2, 5, 1, 4, 3]),
    ("asmr",   0.65, 1.71, [5, 3]),
    ("more",   1.00, 1.67, [4, 2, 8, 3, 1]),
]
HOST_SHOTS = {6, 7}   # 가격 공개 순간에 길게 쓰는 사람/외관 컷 (레퍼런스 18번 2.6초)


def load_clips(media_dir):
    files = sorted(p for p in media_dir.rglob("*") if p.suffix.lower() in MEDIA_EXT)
    if not files:
        sys.exit(f"{media_dir} 안에 영상/사진이 없습니다.")
    by_shot = {}
    for f in files:
        m = re.match(r"(\d+)", f.stem)
        shot = int(m.group(1)) if m else 0
        by_shot.setdefault(shot, []).append(f)
    for v in by_shot.values():          # _best 테이크 먼저
        v.sort(key=lambda p: ("_best" not in p.stem, p.name))
    return by_shot


def transcribe(audio, model_size, max_chars):
    from faster_whisper import WhisperModel
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-af", "highpass=f=80",
                          "-f", "s16le", "-ac", "1", "-ar", "16000", "-"], capture_output=True, check=True).stdout
    wav = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(model_size, compute_type="int8")
    segs, _ = model.transcribe(wav, language="ko", word_timestamps=True, vad_filter=True)
    chunks, cur = [], None
    for seg in segs:
        for w in seg.words:
            text = w.word.strip()
            if not text:
                continue
            if cur and (len(cur["text"]) + len(text) + 1 > max_chars or w.start - cur["end"] > 0.18):
                chunks.append(cur); cur = None
            if cur is None:
                cur = {"start": float(w.start), "end": float(w.end), "text": text}
            else:
                cur["text"] += " " + text; cur["end"] = float(w.end)
            if re.search(r"[.!?~]$", text):
                chunks.append(cur); cur = None
        if cur:
            chunks.append(cur); cur = None
    # 레퍼런스 자막 규칙: 마침표·쉼표 없음, "?"·"..."만 허용. 다음 자막 시작까지 유지
    for i, c in enumerate(chunks):
        c["text"] = re.sub(r"[.,!]+$", "", c["text"]).strip()
        if i + 1 < len(chunks):
            c["end"] = max(c["end"], min(chunks[i + 1]["start"], c["end"] + 0.4))
    return chunks


def srt_time(t):
    ms = int(round(t * 1000)); h, ms = divmod(ms, 3_600_000); m, ms = divmod(ms, 60_000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def plan_scenes(by_shot, body_len, media_dir):
    pos, rot, scenes, t = {}, {}, [], 0.0
    sec_start = 0.0

    state = {"last": None}

    def take(pref, dur, sec):
        """구간 소스 풀을 돌아가며 사용. 직전과 같은 테이크는 피함 (레퍼런스: 연속 동일 컷 없음)."""
        pool = [s for s in pref if s in by_shot] or sorted(by_shot)
        cands = [(sh, t) for sh in pool for t in by_shot[sh]]
        i = rot.get(sec, 0)
        for k in range(len(cands)):
            sh, f = cands[(i + k) % len(cands)]
            if f != state["last"] or len(cands) == 1:
                rot[sec] = i + k + 1; state["last"] = f
                return f

    for name, end_ratio, cut, pref in SECTIONS:
        sec_end = body_len * end_ratio
        first = True
        while t < sec_end - 0.05:
            d = cut
            src = None
            if name == "price" and first and any(s in by_shot for s in HOST_SHOTS):
                src = take([s for s in (6, 7) if s in by_shot], 2.6, 'host'); d = 2.6   # 진행자 등장 긴 컷
            first = False
            d = min(d, sec_end - t)
            if sec_end - t - d < cut * 0.5:          # 남는 자투리는 마지막 컷에 흡수
                d = sec_end - t
            src = src or take(pref, d, name)
            sc = {"file": str(src.relative_to(media_dir)), "duration": round(d, 3), "section": name}
            if src.suffix.lower() not in make_reel.IMG_EXT:
                # 클립에서 움직임·선명도가 가장 좋은 구간을 고르고, 이미 쓴 구간은 피함
                used = pos.setdefault(src, [])
                p = make_reel.best_start(src, d, used)
                sc["start"] = p; used.append((p, p + d))
            scenes.append(sc); t += d
        sec_start = sec_end
    return scenes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media_dir"); ap.add_argument("narration")
    ap.add_argument("--music"); ap.add_argument("--preset", help="브랜드/스타일 프리셋 YAML")
    ap.add_argument("--cover", help='표지 문구 3줄, "|"로 구분')
    ap.add_argument("--model", default="medium", help="whisper 모델 (base/small/medium)")
    ap.add_argument("--max-chars", type=int, default=9, help="자막 한 조각 최대 글자수(공백 포함)")
    ap.add_argument("--out", default="reel.mp4")
    ap.add_argument("--out-dir", help="결과 저장 폴더 (기본: 촬영 폴더)")
    ap.add_argument("--script-dir", help="script.json 이 있는 폴더 (표지 문구)")
    a = ap.parse_args()

    media_dir = Path(a.media_dir).resolve()
    nar = Path(a.narration).resolve()
    by_shot = load_clips(media_dir)
    total = make_reel.probe_duration(nar)
    outro = make_reel.DEFAULTS["outro"]["duration"]
    body = max(total - outro, 3.0)

    print("▶ 나레이션 음성인식 중...")
    caps = transcribe(nar, a.model, a.max_chars)
    scenes = plan_scenes(by_shot, body, media_dir)

    cfg = {}
    if a.preset:
        cfg["preset"] = str(Path(a.preset).resolve())
    cfg.update({"output": a.out, "narration": {"file": str(nar)}})
    if a.music:
        cfg["music"] = {"file": str(Path(a.music).resolve())}
    cover = a.cover.split("|") if a.cover else None
    sj = (Path(a.script_dir) if a.script_dir else media_dir) / "script.json"          # 1단계(write_script.py)에서 만든 표지 문구 자동 사용
    if not cover and sj.exists():
        import json
        cover = json.loads(sj.read_text(encoding="utf-8")).get("cover") or None
    if cover:
        cfg["cover"] = {"lines": cover}
    cfg["captions"] = [{"start": round(c["start"], 3), "end": round(c["end"], 3), "text": c["text"]} for c in caps]
    cfg["scenes"] = scenes

    out_dir = Path(a.out_dir).resolve() if a.out_dir else media_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for sc in scenes:                      # 결과 폴더가 달라도 원본을 찾도록 절대경로
        sc["file"] = str(media_dir / sc["file"])
    proj = out_dir / "project.yaml"
    proj.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (out_dir / "captions.srt").write_text("".join(
        f"{n}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}\n\n"
        for n, c in enumerate(caps, 1)), encoding="utf-8")
    secs = {}
    for s in scenes:
        secs.setdefault(s["section"], []).append(s["duration"])
    print(f"  자막 {len(caps)}개, 컷 {len(scenes)}개 + 엔딩, 길이 {total:.1f}s")
    for k, v in secs.items():
        print(f"    {k:6s} {len(v):2d}컷  평균 {sum(v)/len(v):.2f}s")
    make_reel.make(proj)


if __name__ == "__main__":
    main()

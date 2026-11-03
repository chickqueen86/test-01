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
HOST_SHOTS = {6, 7}
FILE_POOLS = {}       # 대본(script.json)의 section_shots: 구간별로 내용이 맞는 촬영 파일   # 가격 공개 순간에 길게 쓰는 사람/외관 컷 (레퍼런스 18번 2.6초)


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


def tighten_narration(src, out, target_rate=7.0, max_tempo=1.3, syllables=None):
    """나레이션 속도 자동 조절 (레퍼런스: 쉼 없이 초당 약 7음절).
    1) 앞뒤 무음 제거, 문장 사이 0.15초 넘는 쉼을 0.1초로 줄임  2) 음높이 유지한 채 빠르게(최대 1.3배)."""
    tmp = Path(out).with_suffix(".tight.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af",
                    "silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.05:"
                    "stop_periods=-1:stop_duration=0.15:stop_threshold=-40dB:stop_silence=0.1",
                    "-ar", "48000", str(tmp)], check=True)
    L = make_reel.probe_duration(tmp)
    tempo = 1.0
    if syllables:
        tempo = min(max_tempo, max(1.0, L / (syllables / target_rate)))   # 목표 길이 = 음절 / 7
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-af", f"atempo={tempo:.3f},apad=pad_dur=0.3",
                    "-ar", "48000", str(out)], check=True)
    tmp.unlink()
    print(f"  나레이션: 쉼 정리 {L:.1f}초 → 속도 {tempo:.2f}배 → {L / tempo:.1f}초"
          + (f" ({syllables / (L / tempo):.1f}음절/초, 레퍼런스 7)" if syllables else ""))
    return out


def align_script(chunks, lines):
    """음성인식 결과의 '시간'에 대본의 '글자'를 입힘 (받아쓰기 오타 제거).
    인식된 글자들의 시간표를 만들고, 대본 줄마다 누적 음절 비율로 시작/끝 시간을 찾는다."""
    tl = []
    for c in chunks:
        chars = [ch for ch in c["text"] if re.match(r"[가-힣0-9A-Za-z]", ch)]
        for k, _ in enumerate(chars):
            tl.append(c["start"] + (c["end"] - c["start"]) * (k + 0.5) / max(len(chars), 1))
    if not tl:
        return chunks
    syl = [max(len(re.sub(r"[^가-힣0-9A-Za-z]", "", l["text"])), 1) for l in lines]
    tot = sum(syl); acc = 0; out = []
    for l, n in zip(lines, syl):
        i0 = int(acc / tot * (len(tl) - 1)); acc += n; i1 = int(acc / tot * (len(tl) - 1))
        out.append({"start": tl[i0] - 0.08, "end": tl[i1] + 0.1, "text": l["text"], "section": l.get("section", "")})
    for i in range(len(out) - 1):           # 다음 자막까지 빈틈 없이
        out[i]["end"] = out[i + 1]["start"]
    out[0]["start"] = max(0.0, out[0]["start"])
    return out


SECTION_MAP = {"다른": "more", "훅": "hook", "메뉴": "price", "가격": "price", "가성비": "price", "식감": "taste",
               "소리": "asmr", "비주얼": "asmr", "먹는": "more", "술": "more", "곁들임": "more",
               "다른": "more", "마무리": "more"}


def srt_time(t):
    ms = int(round(t * 1000)); h, ms = divmod(ms, 3_600_000); m, ms = divmod(ms, 60_000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def plan_scenes(by_shot, body_len, media_dir):
    # 초점 검사: 전체 클립의 '최고 초점' 중앙값 기준으로 절대 하한을 정하고, 통째로 흐린 클립은 제외
    all_files = [f for v in by_shot.values() for f in v]
    peaks = []
    for f in all_files:
        if f.suffix.lower() in make_reel.IMG_EXT:
            peaks.append(make_reel.image_focus(f))
        else:
            a = make_reel.analyze_clip(f)
            if a is not None:
                peaks.append(float(np.percentile(a["focus"], 90)))
    make_reel.FOCUS_FLOOR["value"] = 0.12 * float(np.median(peaks)) if peaks else 0.0
    for sh in list(by_shot):
        keep = [f for f in by_shot[sh] if make_reel.clip_in_focus(f)]
        for f in set(by_shot[sh]) - set(keep):
            print(f"  ✕ 초점 불량으로 제외: {f.name}")
        if keep:
            by_shot[sh] = keep
        else:
            del by_shot[sh]
    if not by_shot:
        raise SystemExit("초점이 맞는 영상이 없습니다.")
    pos, rot, scenes, t = {}, {}, [], 0.0
    sec_start = 0.0

    state = {"last": None}

    def take(pref, dur, sec):
        """구간 소스 풀을 돌아가며 사용. 직전과 같은 테이크는 피함 (레퍼런스: 연속 동일 컷 없음)."""
        kept = {f for v in by_shot.values() for f in v}
        if FILE_POOLS.get(sec):                       # 대본이 정한 구간별 장면 우선
            cands = [(0, f) for f in FILE_POOLS[sec] if f in kept]
        else:
            cands = []
        if not cands:
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
            p = None
            for attempt in range(12):            # 초점 맞는 구간이 있는 소스가 나올 때까지 다음 후보
                src = src or take(pref, d, name)
                if src.suffix.lower() in make_reel.IMG_EXT:
                    break
                used = pos.setdefault(src, [])
                p = make_reel.best_start(src, d, used)   # 움직임·초점 최고 구간, 초점 나간 프레임 포함 구간은 제외
                if p is not None:
                    used.append((p, p + d)); break
                src = None
            if src is None:
                raise SystemExit("초점이 맞는 구간이 부족합니다. 촬영 영상을 더 넣어 주세요.")
            sc = {"file": str(src.relative_to(media_dir)), "duration": round(d, 3), "section": name}
            if p is not None:
                sc["start"] = p
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
    ap.add_argument("--no-pace", action="store_true", help="나레이션 속도·쉼 자동 조절 끄기")
    ap.add_argument("--script-dir", help="script.json 이 있는 폴더 (표지 문구)")
    a = ap.parse_args()

    media_dir = Path(a.media_dir).resolve()
    nar = Path(a.narration).resolve()
    by_shot = load_clips(media_dir)
    total = make_reel.probe_duration(nar)
    import json
    sdir = Path(a.script_dir) if a.script_dir else media_dir
    lines = []
    if (sdir / "script.json").exists():
        lines = json.loads((sdir / "script.json").read_text(encoding="utf-8")).get("lines", [])
    md = sdir / "script.md"                 # 사람이 고친 대본(script.md)이 있으면 그 글자를 우선 사용
    if md.exists():
        body_md = md.read_text(encoding="utf-8").split("## 녹음용 대본", 1)[-1]
        body_md = re.split(r"\n_약|\n## ", body_md, maxsplit=1)[0]
        sec, md_lines = "", []
        for ln in body_md.splitlines()[1:]:
            ln = ln.strip()
            m = re.match(r"\*\*\[(.+?)\]\*\*", ln)
            if m:
                sec = m.group(1); continue
            if ln and not ln.startswith(("#", "_", "(")):
                md_lines.append({"section": sec, "text": ln})
        if md_lines:
            lines = md_lines
    syl = sum(len(re.sub(r"[^가-힣0-9]", "", l["text"])) for l in lines) or None
    out_dir0 = Path(a.out_dir).resolve() if a.out_dir else media_dir
    out_dir0.mkdir(parents=True, exist_ok=True)
    if not a.no_pace:
        nar = tighten_narration(nar, out_dir0 / "narration_paced.wav", syllables=syl)
    total = make_reel.probe_duration(nar)
    brand_yaml = Path(make_reel.ROOT / "brand.yaml")
    has_brand = brand_yaml.exists() and bool((yaml.safe_load(brand_yaml.read_text(encoding="utf-8")) or {}).get("brand", {}).get("parts")
                                             or (yaml.safe_load(brand_yaml.read_text(encoding="utf-8")) or {}).get("brand", {}).get("logo_image"))
    outro = make_reel.DEFAULTS["outro"]["duration"] if has_brand else 0.0
    body = max(total - outro, 3.0)

    print("▶ 나레이션 음성인식 중...")
    caps = transcribe(nar, a.model, a.max_chars)
    sj_data = json.loads((sdir / "script.json").read_text(encoding="utf-8")) if (sdir / "script.json").exists() else {}
    for sec_name, files in (sj_data.get("section_shots") or {}).items():
        key = next((v for k, v in SECTION_MAP.items() if k in sec_name), sec_name)
        FILE_POOLS.setdefault(key, [])
        FILE_POOLS[key] += [media_dir / f for f in files if (media_dir / f).exists()]
    if lines:
        caps = align_script(caps, lines)      # 자막 글자는 대본 그대로, 시간만 목소리에서
        ends = {}
        for c in caps:
            key = next((v for k, v in SECTION_MAP.items() if k in c.get("section", "")), None) or \
                  (c.get("section") if c.get("section") in ("hook", "price", "taste", "asmr", "more") else None)
            if key:
                ends[key] = min(c["end"], body) / body
        order = [x for x in ("hook", "price", "taste", "asmr", "more") if x in ends]
        if order:
            ends[order[-1]] = 1.0
            global SECTIONS
            base_sec = {n: (c, p) for n, _, c, p in SECTIONS}
            SECTIONS = [(n, ends[n], base_sec[n][0], base_sec[n][1]) for n in order]
    scenes = plan_scenes(by_shot, body, media_dir)

    cfg = {}
    if a.preset:
        cfg["preset"] = str(Path(a.preset).resolve())
    cfg.update({"output": a.out, "narration": {"file": str(nar)}})
    if not has_brand:
        cfg["outro"] = {"enabled": False}
    if a.music:
        cfg["music"] = {"file": str(Path(a.music).resolve())}
    cover = a.cover.split("|") if a.cover else None
    sj = (Path(a.script_dir) if a.script_dir else media_dir) / "script.json"          # 1단계(write_script.py)에서 만든 표지 문구 자동 사용
    if not cover and sj.exists():
        import json
        cover = json.loads(sj.read_text(encoding="utf-8")).get("cover") or None
    if cover:
        cfg["cover"] = {"lines": cover}
    cfg["captions"] = [{"start": round(c["start"], 3), "end": round(c["end"], 3), "text": c["text"]} for c in caps
                       if c["start"] < body]
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

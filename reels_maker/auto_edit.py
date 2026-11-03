#!/usr/bin/env python3
"""영상 + 나레이션 → 완성 릴스 자동 편집.

1) 나레이션을 음성인식(Whisper)해서 짧은 자막 조각으로 자름 (단어 단위 타이밍)
2) 자막 조각마다 컷을 바꾸며 촬영 소스를 순서대로 배치 (영상 길이 = 나레이션 길이)
3) 로고·자막·BGM(나레이션 아래로 낮춤)까지 합쳐 mp4 출력

사용법:
  python auto_edit.py 촬영폴더/ 나레이션.m4a [--music bgm.mp3] [--style project_style.yaml]
  → 촬영폴더/project.yaml(수정 가능), reel.mp4, captions.srt 생성
  자막 문구를 고치고 싶으면 project.yaml 수정 후: python make_reel.py 촬영폴더/project.yaml
"""
import argparse, re, sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import make_reel

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}


def pick_clips(media_dir):
    """촬영 지침 파일명(컷번호_테이크[_best]) 기준으로 컷당 1개, 컷번호 순 정렬."""
    files = sorted(p for p in media_dir.rglob("*") if p.suffix.lower() in MEDIA_EXT)
    if not files:
        sys.exit(f"{media_dir} 안에 영상/사진이 없습니다.")
    shots = {}
    for f in files:
        m = re.match(r"(\d+)", f.stem)
        key = int(m.group(1)) if m else 10_000 + len(shots)
        if key not in shots or "_best" in f.stem:
            shots[key] = f
    # 01번(훅) 먼저, 나머지는 번호순
    return [shots[k] for k in sorted(shots)]


def transcribe(audio, model_size, max_chars):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, compute_type="int8")
    # ffmpeg로 직접 16kHz mono 디코딩 (PyAV 버전 차이 회피, 영상 파일도 그대로 입력 가능)
    import subprocess, numpy as np
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-f", "s16le", "-ac", "1",
                          "-ar", "16000", "-"], capture_output=True, check=True).stdout
    wav = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    segs, _ = model.transcribe(wav, language="ko", word_timestamps=True, vad_filter=True)
    chunks, cur = [], None
    for seg in segs:
        for w in seg.words:
            text = w.word.strip()
            if not text:
                continue
            if cur is None:
                cur = {"start": float(w.start), "end": float(w.end), "text": text}
            elif len(cur["text"]) + len(text) + 1 > max_chars or w.start - cur["end"] > 0.6:
                chunks.append(cur); cur = {"start": float(w.start), "end": float(w.end), "text": text}
            else:
                cur["text"] += " " + text; cur["end"] = float(w.end)
            if re.search(r"[.!?~]$", text):          # 문장 끝에서 끊기
                chunks.append(cur); cur = None
        if cur:
            chunks.append(cur); cur = None
    for c in chunks:
        c["text"] = c["text"].rstrip(".,")
    return chunks


def srt_time(t):
    ms = int(round(t * 1000)); h, ms = divmod(ms, 3_600_000); m, ms = divmod(ms, 60_000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media_dir"); ap.add_argument("narration")
    ap.add_argument("--music"); ap.add_argument("--style", help="brand/style 이 들어있는 YAML (공통 템플릿)")
    ap.add_argument("--model", default="small", help="whisper 모델: base/small/medium (클수록 정확, 느림)")
    ap.add_argument("--max-chars", type=int, default=14, help="자막 한 조각 최대 글자수")
    ap.add_argument("--min-cut", type=float, default=1.2, help="컷 최소 길이(초)")
    ap.add_argument("--out", default="reel.mp4")
    a = ap.parse_args()

    media_dir = Path(a.media_dir).resolve()
    nar = Path(a.narration).resolve()
    clips = pick_clips(media_dir)
    total = make_reel.probe_duration(nar)

    print("▶ 나레이션 음성인식 중...")
    chunks = transcribe(nar, a.model, a.max_chars)
    if not chunks:
        sys.exit("나레이션에서 음성을 찾지 못했습니다.")

    # 자막 경계 = 컷 경계. 첫 컷은 0초부터, 마지막 컷은 나레이션 끝까지.
    bounds = [0.0] + [c["start"] for c in chunks[1:]] + [total]
    # 너무 짧은 컷은 앞 컷과 합침
    scenes, use_pos = [], {}
    i = 0
    for k, c in enumerate(chunks):
        st, en = bounds[k], bounds[k + 1]
        if scenes and en - st < a.min_cut:
            scenes[-1]["duration"] = round(scenes[-1]["duration"] + en - st, 3)
            scenes[-1]["captions"].append(c["text"])
            continue
        clip = clips[i % len(clips)]; i += 1
        sc = {"file": str(clip.relative_to(media_dir)) if clip.is_relative_to(media_dir) else str(clip),
              "duration": round(en - st, 3), "captions": [c["text"]]}
        if clip.suffix.lower() not in make_reel.IMG_EXT:
            clip_len = make_reel.probe_duration(clip)
            pos = use_pos.get(clip, min(1.0, max(clip_len - sc["duration"], 0)))  # 촬영 앞 1초 여유 건너뜀
            if pos + sc["duration"] > clip_len:
                pos = max(clip_len - sc["duration"], 0)
            sc["start"] = round(pos, 3); use_pos[clip] = pos + sc["duration"]
        scenes.append(sc)

    cfg = yaml.safe_load(Path(a.style).read_text(encoding="utf-8")) if a.style else {}
    cfg.setdefault("brand", {"parts": [["조선", "#E8202A"], ["클라쓰", "#FFFFFF"]], "size": 62})
    cfg.setdefault("style", {"caption_size": 64, "caption_y": 0.62})
    for key in ("font",):
        if cfg["style"].get(key):
            cfg["style"][key] = str(Path(a.style).resolve().parent / cfg["style"][key])
    cfg["output"] = a.out
    cfg["narration"] = {"file": str(nar)}
    if a.music:
        cfg["music"] = {"file": str(Path(a.music).resolve())}
    cfg["scenes"] = scenes

    proj = media_dir / "project.yaml"
    proj.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (media_dir / "captions.srt").write_text("".join(
        f"{n}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}\n\n"
        for n, c in enumerate(chunks, 1)), encoding="utf-8")
    print(f"  자막 {len(chunks)}개, 컷 {len(scenes)}개, 길이 {total:.1f}s → {proj}")
    make_reel.make(proj)


if __name__ == "__main__":
    main()

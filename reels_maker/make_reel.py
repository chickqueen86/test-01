#!/usr/bin/env python3
"""식당 릴스 렌더러 v2 - 레퍼런스(조선클라쓰 진흙오리 릴스) 분석값 적용.

적용 항목 (근거: team/00_통합요약.md)
- 색감: 레퍼런스 측정값(presets/josun_color.json)으로 클립별 색 매칭
- 자막: 나눔명조, 얇은 흰 글씨 + 부드러운 그림자, 화면 중앙, 컷과 독립된 전역 타임라인
- 로고: 좌상단 작게 상시 / 엔딩: 검은 화면 + 반짝이 + 로고 + present
- 소리: 클립 현장음(튀김·칼질) 유지, BGM은 나레이션 대비 약 -20dB, 덕킹 없음,
        최종 -17.5 LUFS / -0.3 dBTP 로 압축·정규화
- 표지: 노랑/흰 굵은 고딕 3줄 커버 이미지(cover.jpg) 별도 생성

사용법: python make_reel.py project.yaml
"""
import json, random, shutil, subprocess, sys, tempfile
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
W, H, FPS = 1080, 1920, 30
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULTS = {
    "style": {"font": str(ROOT / "fonts/NanumMyeongjo-Regular.ttf"),
              "caption_size": 50, "caption_y": 0.45, "caption_color": "#FFFFFF",
              "shadow_blur": 6, "shadow_alpha": 190},
    "brand": {"parts": [["조선", "#E8202A"], ["클라쓰", "#FFFFFF"]], "size": 46, "margin": 40,
              "font": str(ROOT / "fonts/BlackHanSans-Regular.ttf")},
    "color": {"match": str(ROOT / "presets/josun_color.json"), "strength": 0.85},
    "audio": {"target_lufs": -17.5, "true_peak": -0.3, "lra": 5,
              "ambient_db": -10, "music_db": -20, "narration_db": 0},
    "outro": {"enabled": True, "duration": 1.3, "text": "present"},
    "cover": {"enabled": True, "font": str(ROOT / "fonts/BlackHanSans-Regular.ttf"),
              "colors": ["#FFE14D", "#FFFFFF", "#FFFFFF"], "frame_time": 0.3},
}


def deep_merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg 실패:\n{' '.join(map(str, cmd))}\n{r.stderr[-2500:]}")
    return r


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def has_audio(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return bool(out.strip())


# ---------------------------------------------------------------- 색감
def sample_rgb(path, start, dur, is_img):
    """클립에서 프레임 몇 장을 뽑아 RGB 픽셀 배열 반환."""
    px = []
    times = [0] if is_img else [start + dur * f for f in (0.1, 0.35, 0.6, 0.85)]
    for t in times:
        cmd = ["ffmpeg", "-v", "error"] + ([] if is_img else ["-ss", f"{t:.3f}"]) + [
            "-i", str(path), "-frames:v", "1", "-vf",
            f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},scale=90:160",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        b = subprocess.run(cmd, capture_output=True).stdout
        if b:
            px.append(np.frombuffer(b, np.uint8).reshape(-1, 3).astype(float))
    return np.concatenate(px) if px else None


def color_filter(path, start, dur, is_img, ccfg):
    """레퍼런스 평균/표준편차로 채널별 색 전이(Reinhard 방식, RGB)."""
    if not ccfg or not ccfg.get("match") or not Path(ccfg["match"]).exists():
        return ""
    tgt = json.loads(Path(ccfg["match"]).read_text())
    px = sample_rgb(path, start, dur, is_img)
    if px is None:
        return ""
    s = float(ccfg.get("strength", 0.85))
    ms, ss = px.mean(0), np.maximum(px.std(0), 1)
    exprs = []
    for c in range(3):
        mt = ms[c] + (tgt["mean"][c] - ms[c]) * s
        st = ss[c] + (tgt["std"][c] - ss[c]) * s
        exprs.append(f"clip((val-{ms[c]:.2f})*{st / ss[c]:.4f}+{mt:.2f}\\,0\\,255)")
    return f",lutrgb=r='{exprs[0]}':g='{exprs[1]}':b='{exprs[2]}'"


# ---------------------------------------------------------------- 그래픽
def font(path, size):
    return ImageFont.truetype(str(path), size)


def wrap(draw, text, fnt, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            t = (cur + " " + word).strip()
            if draw.textlength(t, font=fnt) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur); cur = word
        lines.append(cur)
    return lines


def render_caption(text, st, out):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(st["font"], st["caption_size"])
    lines = wrap(d, text, f, W - 200)
    lh = int(st["caption_size"] * 1.35)
    y0 = int(H * st["caption_y"]) - lh * len(lines) // 2
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sd = ImageDraw.Draw(sh)
    for i, line in enumerate(lines):
        x = (W - d.textlength(line, font=f)) / 2
        sd.text((x + 2, y0 + i * lh + 3), line, font=f, fill=(0, 0, 0, st["shadow_alpha"]))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(st["shadow_blur"])))
    for i, line in enumerate(lines):
        x = (W - d.textlength(line, font=f)) / 2
        d.text((x, y0 + i * lh), line, font=f, fill=st["caption_color"])
    img.save(out)


def render_logo(brand, out):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    m = brand.get("margin", 40)
    if brand.get("logo_image"):
        logo = Image.open(brand["logo_image"]).convert("RGBA")
        lw = brand.get("logo_width", 200)
        img.alpha_composite(logo.resize((lw, int(logo.height * lw / logo.width))), (m, m))
    else:
        d = ImageDraw.Draw(img); f = font(brand["font"], brand["size"]); x = m
        for txt, col in brand["parts"]:
            d.text((x, m), txt, font=f, fill=col, stroke_width=2, stroke_fill="#000000")
            x += d.textlength(txt, font=f)
    img.save(out)


def render_outro(brand, ocfg, out_png):
    """검은 배경 + 반짝이 입자 + 로고 + present (레퍼런스 50번 컷)."""
    rnd = random.Random(7)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    sp = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sd = ImageDraw.Draw(sp)
    for _ in range(260):
        x, y, r = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(1, 3.2)
        a = int(rnd.uniform(60, 230))
        sd.ellipse((x - r, y - r, x + r, y + r), fill=(255, rnd.randint(150, 210), 90, a))
    img.alpha_composite(sp.filter(ImageFilter.GaussianBlur(0.8)))
    d = ImageDraw.Draw(img); f = font(brand["font"], 110)
    tw = sum(d.textlength(t, font=f) for t, _ in brand["parts"]); x = (W - tw) / 2
    for t, c in brand["parts"]:
        d.text((x, H / 2 - 110), t, font=f, fill=c); x += d.textlength(t, font=f)
    f2 = font(DEFAULTS["style"]["font"], 46); sub = ocfg.get("text", "present")
    d.text(((W - d.textlength(sub, font=f2)) / 2, H / 2 + 20), sub, font=f2, fill="#FFFFFF")
    img.save(out_png)


def render_cover(video, ccfg, out):
    """표지: 첫 장면 프레임 + 노랑/흰 굵은 고딕 3줄 (레퍼런스 표지 공식)."""
    lines = ccfg.get("lines")
    if not lines:
        return None
    frame = out.with_suffix(".frame.png")
    run(["ffmpeg", "-y", "-v", "error", "-ss", str(ccfg.get("frame_time", 0.3)), "-i", str(video),
         "-frames:v", "1", str(frame)])
    img = Image.open(frame).convert("RGBA"); frame.unlink()
    d = ImageDraw.Draw(img)
    sizes = ccfg.get("sizes", [96, 84, 60])
    y = int(H * ccfg.get("y", 0.30))
    for i, text in enumerate(lines):
        f = font(ccfg["font"], sizes[min(i, len(sizes) - 1)])
        col = ccfg["colors"][min(i, len(ccfg["colors"]) - 1)]
        tw = d.textlength(text, font=f)
        while tw > W - 60 and f.size > 30:
            f = font(ccfg["font"], f.size - 4); tw = d.textlength(text, font=f)
        d.text(((W - tw) / 2, y), text, font=f, fill=col, stroke_width=max(4, f.size // 12),
               stroke_fill="#000000")
        y += int(f.size * 1.18)
    img.convert("RGB").save(out, quality=95)
    return out


# ---------------------------------------------------------------- 장면
def build_scene(i, sc, cfg, tmp, base):
    src = (base / sc["file"]).resolve()
    is_img = src.suffix.lower() in IMG_EXT
    dur = float(sc.get("duration") or (3.0 if is_img else min(probe_duration(src), 4.0)))
    start = float(sc.get("start", 0))
    speed = float(sc.get("speed", 1.0))
    cf = color_filter(src, start, dur * speed, is_img, cfg.get("color"))
    if is_img:
        frames = int(dur * FPS); z = sc.get("zoom", 1.12)
        vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
              f"zoompan=z='1+({z}-1)*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={W}x{H}:fps={FPS}")
        ins = ["-loop", "1", "-t", str(dur), "-i", str(src)]
    else:
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setpts=PTS/{speed},fps={FPS}"
        ins = ["-ss", str(start), "-t", str(dur * speed), "-i", str(src)]
    vf += f"{cf},setsar=1,tpad=stop_mode=clone:stop_duration=2,format=yuv420p"
    # 현장음 유지 (없으면 무음 트랙) - concat을 위해 모든 장면을 48k 스테레오로 통일
    if not is_img and has_audio(src) and not sc.get("mute"):
        af = f"[0:a]atempo={speed},aresample=48000,aformat=channel_layouts=stereo,apad[a]" \
            if 0.5 <= speed <= 2 else "[1:a]anull[a]"
    else:
        af = "[1:a]anull[a]"
    ins += ["-f", "lavfi", "-t", str(dur), "-i", "anullsrc=r=48000:cl=stereo"]
    out = tmp / f"scene_{i:03d}.mp4"
    run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", f"[0:v]{vf}[v];{af}",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "17", "-r", str(FPS), "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
         str(out.with_suffix(".mov"))])
    return out.with_suffix(".mov"), dur


def build_outro(cfg, tmp):
    png = tmp / "outro.png"; render_outro(cfg["brand"], cfg["outro"], png)
    out = tmp / "scene_zzz_outro.mov"; d = float(cfg["outro"]["duration"])
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(d), "-i", str(png),
         "-f", "lavfi", "-t", str(d), "-i", "anullsrc=r=48000:cl=stereo",
         "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
         "-r", str(FPS), "-c:a", "pcm_s16le", "-shortest", str(out)])
    return out, d


# ---------------------------------------------------------------- 메인
def load_cfg(project_path):
    base = project_path.parent
    cfg = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
    if cfg.get("preset"):
        preset = yaml.safe_load((base / cfg["preset"]).read_text(encoding="utf-8"))
        cfg = deep_merge(preset, cfg)
    cfg = deep_merge(DEFAULTS, cfg)
    for sec, key in (("style", "font"), ("brand", "font"), ("brand", "logo_image"),
                     ("color", "match"), ("cover", "font")):
        v = cfg.get(sec, {}).get(key)
        if v and not Path(v).is_absolute():
            p = base / v
            cfg[sec][key] = str(p if p.exists() else ROOT / v)
    return cfg, base


def make(project_path):
    project_path = Path(project_path).resolve()
    cfg, base = load_cfg(project_path)
    out_path = base / cfg.get("output", "reel.mp4")
    tmp = Path(tempfile.mkdtemp(prefix="reel_"))
    try:
        parts, total = [], 0.0
        for i, sc in enumerate(cfg["scenes"]):
            p, d = build_scene(i, sc, cfg, tmp, base)
            parts.append(p); total += d
            print(f"  [{i+1}/{len(cfg['scenes'])}] {sc['file']} ({d:.2f}s)")
        outro_start = total
        if cfg["outro"].get("enabled", True):
            p, d = build_outro(cfg, tmp); parts.append(p); total += d

        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        joined = tmp / "joined.mov"
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", str(joined)])

        # 전역 자막 타임라인 (컷과 독립). 구버전 호환: 장면별 captions 를 전역으로 변환
        caps = list(cfg.get("captions") or [])
        if not caps:
            t = 0.0
            for sc in cfg["scenes"]:
                d = float(sc.get("duration", 0)); cs = sc.get("captions") or ([sc["caption"]] if sc.get("caption") else [])
                for k, c in enumerate(cs):
                    caps.append({"start": t + d * k / len(cs), "end": t + d * (k + 1) / len(cs), "text": c})
                t += d
        caps = [c for c in caps if c["start"] < outro_start]  # 엔딩 카드 위엔 자막 없음

        logo = tmp / "logo.png"; render_logo(cfg["brand"], logo)
        ins = ["-i", str(joined), "-loop", "1", "-i", str(logo)]
        vf = f"[0:v][1:v]overlay=0:0:enable='lt(t,{outro_start:.3f})'[v0]"
        last = "v0"
        for k, c in enumerate(caps):
            png = tmp / f"cap_{k:03d}.png"; render_caption(c["text"], cfg["style"], png)
            ins += ["-loop", "1", "-i", str(png)]
            end = min(c["end"], outro_start)
            vf += f";[{last}][{k+2}:v]overlay=0:0:enable='between(t,{c['start']:.3f},{end:.3f})'[v{k+1}]"
            last = f"v{k+1}"
        n_in = len(caps) + 2

        a = cfg["audio"]; mix = [f"[0:a]volume={a['ambient_db']}dB[amb]"]; labels = ["[amb]"]
        if cfg.get("narration"):
            ins += ["-i", str(base / cfg["narration"]["file"])]
            mix.append(f"[{n_in}:a]aresample=48000,volume={a['narration_db']}dB[nar]"); labels.append("[nar]"); n_in += 1
        if cfg.get("music"):
            m = cfg["music"]
            ins += ["-stream_loop", "-1", "-ss", str(m.get("start", 0)), "-i", str(base / m["file"])]
            mdb = m.get("db", a["music_db"])
            mix.append(f"[{n_in}:a]aresample=48000,volume={mdb}dB,"
                       f"afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[mus]"); labels.append("[mus]"); n_in += 1
        af = ";".join(mix) + f";{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0," \
             f"acompressor=threshold=-20dB:ratio=3:attack=5:release=120:makeup=2," \
             f"loudnorm=I={a['target_lufs']}:TP={a['true_peak']}:LRA={a['lra']}[aout]"
        run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", f"{vf};{af}",
             "-map", f"[{last}]", "-map", "[aout]", "-t", f"{total:.3f}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out_path)])
        msg = f"완료: {out_path} ({total:.1f}s)"
        if cfg["cover"].get("enabled", True) and cfg["cover"].get("lines"):
            cov = render_cover(joined, cfg["cover"], out_path.with_name(out_path.stem + "_cover.jpg"))
            msg += f"\n표지: {cov}"
        print(msg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: python make_reel.py project.yaml [project2.yaml ...]")
    for p in sys.argv[1:]:
        print(f"▶ {p}"); make(p)

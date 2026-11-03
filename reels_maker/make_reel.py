#!/usr/bin/env python3
"""식당 릴스 메이커 - 프로젝트 YAML 하나로 9:16 릴스(1080x1920) 자동 생성.

스타일: 풀스크린 음식 클로즈업 + 좌상단 브랜드 로고 + 하단 중앙 외곽선 자막(짧게 끊어치기).
사용법: python make_reel.py project.yaml
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/nanum/NanumSquareEB.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg 실패:\n{' '.join(cmd)}\n{r.stderr[-2000:]}")


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def find_font(cfg_font):
    for f in [cfg_font, *FALLBACK_FONTS]:
        if f and Path(f).exists():
            return f
    sys.exit("한글 폰트를 찾을 수 없습니다. style.font 에 .ttf/.otf 경로를 지정하세요.")


def wrap(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            t = (cur + " " + word).strip()
            if draw.textlength(t, font=font) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur); cur = word
        lines.append(cur)
    return lines


def render_caption(text, style, font_path, out):
    """투명 PNG에 외곽선 자막을 그린다 (highlight 단어는 강조색)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = style.get("caption_size", 64)
    font = ImageFont.truetype(font_path, size)
    stroke = style.get("caption_stroke", 6)
    lines = wrap(d, text, font, W - 160)
    lh = int(size * 1.3)
    y = int(H * style.get("caption_y", 0.62)) - lh * len(lines) // 2
    hl = style.get("highlight_words", [])
    for line in lines:
        lw = d.textlength(line, font=font)
        x = (W - lw) / 2
        # 단어 단위로 그려서 강조 단어 색상 변경
        for i, word in enumerate(line.split(" ")):
            token = word + (" " if i < len(line.split(" ")) - 1 else "")
            color = style.get("highlight_color", "#FFE14D") if any(h in word for h in hl) \
                else style.get("caption_color", "#FFFFFF")
            d.text((x, y), token, font=font, fill=color,
                   stroke_width=stroke, stroke_fill=style.get("caption_stroke_color", "#000000"))
            x += d.textlength(token, font=font)
        y += lh
    img.save(out)


def render_logo(brand, font_path, out):
    """좌상단 브랜드 로고: 이미지 파일이 있으면 사용, 없으면 2색 텍스트 로고 생성."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    m = brand.get("margin", 50)
    if brand.get("logo_image"):
        logo = Image.open(brand["logo_image"]).convert("RGBA")
        lw = brand.get("logo_width", 260)
        logo = logo.resize((lw, int(logo.height * lw / logo.width)))
        img.alpha_composite(logo, (m, m))
    elif brand.get("name") or brand.get("parts"):
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(font_path, brand.get("size", 60))
        x = m
        parts = brand.get("parts") or [[brand["name"], brand.get("color", "#E8202A")]]
        for txt, col in parts:
            d.text((x, m), txt, font=font, fill=col, stroke_width=4, stroke_fill="#FFFFFF")
            x += d.textlength(txt, font=font)
    img.save(out)


def build_scene(i, scene, style, font_path, logo_png, tmp, base):
    src = (base / scene["file"]).resolve()
    is_img = src.suffix.lower() in IMG_EXT
    dur = float(scene.get("duration") or (3.0 if is_img else min(probe_duration(src), 4.0)))
    start = float(scene.get("start", 0))
    caps = scene.get("captions") or ([scene["caption"]] if scene.get("caption") else [])

    cover = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1")
    if is_img:
        frames = int(dur * FPS)
        z = scene.get("zoom", 1.15)
        vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
              f"zoompan=z='1+({z}-1)*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={W}x{H}:fps={FPS},setsar=1")
        inputs = ["-loop", "1", "-t", str(dur), "-i", str(src)]
    else:
        speed = float(scene.get("speed", 1.0))
        vf = f"{cover},setpts=PTS/{speed},fps={FPS}"
        inputs = ["-ss", str(start), "-t", str(dur * speed), "-i", str(src)]

    inputs += ["-i", str(logo_png)]
    filt = f"[0:v]{vf},tpad=stop_mode=clone:stop_duration=2[v0];[v0][1:v]overlay=0:0[v1]"
    last = "v1"
    seg = dur / max(len(caps), 1)
    for k, text in enumerate(caps):
        png = tmp / f"cap_{i}_{k}.png"
        render_caption(text, style, font_path, png)
        inputs += ["-i", str(png)]
        filt += (f";[{last}][{k+2}:v]overlay=0:0:enable='between(t,{k*seg:.3f},{(k+1)*seg:.3f})'[c{k}]")
        last = f"c{k}"

    out = tmp / f"scene_{i:03d}.mp4"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filt, "-map", f"[{last}]",
         "-t", str(dur), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])
    return out, dur


def make(project_path):
    project_path = Path(project_path).resolve()
    base = project_path.parent
    cfg = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    style = cfg.get("style", {})
    font_path = find_font(style.get("font") and str((base / style["font"])))
    out_path = base / cfg.get("output", "reel.mp4")

    tmp = Path(tempfile.mkdtemp(prefix="reel_"))
    try:
        brand = dict(cfg.get("brand", {}))
        if brand.get("logo_image"):
            brand["logo_image"] = str(base / brand["logo_image"])
        logo_png = tmp / "logo.png"
        render_logo(brand, font_path, logo_png)

        scenes, total = [], 0.0
        for i, sc in enumerate(cfg["scenes"]):
            p, d = build_scene(i, sc, style, font_path, logo_png, tmp, base)
            scenes.append(p); total += d
            print(f"  [{i+1}/{len(cfg['scenes'])}] {sc['file']} ({d:.1f}s)")

        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in scenes))
        silent = tmp / "joined.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", str(silent)])

        music, nar = cfg.get("music"), cfg.get("narration")
        if music or nar:
            ins, mix = ["-i", str(silent)], []
            if nar:
                ins += ["-i", str(base / nar["file"])]
                mix.append(f"[{len(ins)//2 - 1}:a]volume={nar.get('volume', 1.0)},apad[n]")
            if music:
                ins += ["-stream_loop", "-1", "-ss", str(music.get("start", 0)), "-i", str(base / music["file"])]
                # 나레이션이 있으면 BGM 기본 볼륨을 낮춤
                mvol = music.get("volume", 0.15 if nar else 0.8)
                mix.append(f"[{len(ins)//2 - 1 if not nar else 2}:a]volume={mvol},"
                           f"afade=t=out:st={max(total-1.5,0):.2f}:d=1.5[m]")
            labels = ("[n]" if nar else "") + ("[m]" if music else "")
            k = labels.count("[")
            filt = ";".join(mix) + (f";{labels}amix=inputs={k}:duration=longest:normalize=0[a]" if k > 1
                                     else f";{labels}anull[a]")
            run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", filt,
                 "-map", "0:v", "-map", "[a]", "-t", f"{total:.3f}",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                 str(out_path)])
        else:
            shutil.copy(silent, out_path)
        print(f"완료: {out_path} ({total:.1f}s)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: python make_reel.py project.yaml [project2.yaml ...]")
    for p in sys.argv[1:]:
        print(f"▶ {p}")
        make(p)

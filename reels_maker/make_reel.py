#!/usr/bin/env python3
"""식당 릴스 렌더러 v2 - 레퍼런스(진흙오리 릴스) 분석값 적용.

적용 항목 (근거: team/00_통합요약.md)
- 색감: 레퍼런스 측정값(presets/ref_color.json)으로 클립별 색 매칭
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
              "caption_size": 56, "caption_y": 0.45, "caption_color": "#FFFFFF",
              "shadow_blur": 10, "shadow_alpha": 235},
    "brand": {"parts": [], "size": 46, "margin": 40,   # 브랜드는 brand.yaml 에서 (없으면 로고 없음)
              "font": str(ROOT / "fonts/BlackHanSans-Regular.ttf")},
    "color": {"match": str(ROOT / "presets/ref_color.json"), "strength": 0.85, "saturation": 1.08},
    "motion": {"auto": True, "threshold": 5.0, "zoom": 1.10},
    "look": {"sharpen": 0.45, "glow": 0.18, "glow_threshold": 0.78, "grain": 5, "vignette": 0},
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


_CF_CACHE = {}


def color_filter(path, start, dur, is_img, ccfg):
    """색보정: 클립의 채널별 분포(백분위)를 레퍼런스 분포에 맞추는 톤 커브 (히스토그램 매칭).
    평균만 맞추던 v1과 달리 어두운 곳/중간/밝은 곳을 각각 맞춘다 (진한 검정, 붉은 중간톤, 광택 하이라이트)."""
    if not ccfg or not ccfg.get("match") or not Path(ccfg["match"]).exists():
        return ""
    key = str(path)
    if key in _CF_CACHE:          # 같은 소스는 한 번만 계산 → 같은 테이크 컷끼리 색이 튀지 않음
        return _CF_CACHE[key]
    tgt = json.loads(Path(ccfg["match"]).read_text())
    L = 0 if is_img else probe_duration(path)
    px = sample_rgb(path, 0, L, is_img)
    if px is None:
        return ""
    s = float(ccfg.get("strength", 0.85))
    P = tgt["percentiles"]
    curves = []
    for i, c in enumerate("rgb"):
        src = np.percentile(px[:, i], P)
        dst = np.array(tgt["pct"][c], float)
        xs, ys = [0.0], [0.0]
        for x, y in zip(src, dst):
            x = x / 255; y = (x * 255 + (y - x * 255) * s) / 255
            if x - xs[-1] < 0.01:            # 너무 붙은 점 생략
                continue
            xs.append(x); ys.append(max(y, ys[-1]))   # 단조 증가 유지
        if xs[-1] < 0.999:
            xs.append(1.0); ys.append(max(1.0 * (1 - s) + (dst[-1] / 255) * s, ys[-1]))
        curves.append(" ".join(f"{x:.3f}/{min(y, 1):.3f}" for x, y in zip(xs, ys)))
    f = f",curves=r='{curves[0]}':g='{curves[1]}':b='{curves[2]}'"
    sat = ccfg.get("saturation")
    if sat:
        f += f",eq=saturation={sat}"
    _CF_CACHE[key] = f
    return f


def look_filter(lcfg):
    """영상 필터(룩): 선명도, 하이라이트 번짐(광택), 필름 입자, 비네트."""
    if not lcfg:
        return ""
    f = ""
    if lcfg.get("sharpen"):
        f += f",unsharp=5:5:{lcfg['sharpen']}:5:5:0"
    if lcfg.get("grain"):
        f += f",noise=c0s={lcfg['grain']}:c0f=t+u"
    if lcfg.get("vignette"):
        f += f",vignette=angle={lcfg['vignette']}"
    return f


def glow_graph(inp, out, lcfg):
    """하이라이트만 뽑아 흐리게 번지게 해서 겹침 (기름 광택·보케 느낌)."""
    g = (lcfg or {}).get("glow")
    if not g:
        return f"[{inp}]null[{out}]"
    th = lcfg.get("glow_threshold", 0.78)
    return (f"[{inp}]split[g0][g1];[g1]curves=all='0/0 {th:.2f}/0 1/1',gblur=sigma=18[g2];"
            f"[g0][g2]blend=all_mode=screen:all_opacity={g}[{out}]")


# ---------------------------------------------------------------- 움직임 분석 / 생성
_AN_CACHE = {}


def analyze_clip(path, fps=6):
    """클립을 초당 6장으로 훑어 구간별 움직임(프레임 차이)과 선명도(라플라시안 분산)를 잰다."""
    key = str(path)
    if key in _AN_CACHE:
        return _AN_CACHE[key]
    w, h = 72, 128
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(float)
    if len(fr) < 2:
        _AN_CACHE[key] = None
        return None
    motion = np.r_[0, np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))]
    motion[0] = motion[1]
    lap = fr[:, 1:-1, 1:-1] * 4 - fr[:, :-2, 1:-1] - fr[:, 2:, 1:-1] - fr[:, 1:-1, :-2] - fr[:, 1:-1, 2:]
    sharp = lap.var(axis=(1, 2))
    # 초점: 화면 중앙(음식이 있는 곳) 고해상도 라플라시안 분산 (모션블러·아웃포커스 모두 낮게 나옴)
    fw, fh = 360, 640
    raw2 = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                           f"fps={fps},scale={fw}:{fh}:force_original_aspect_ratio=increase,crop={fw}:{fh},"
                           f"crop={int(fw*0.7)}:{int(fh*0.6)},format=gray", "-f", "rawvideo", "-"],
                          capture_output=True).stdout
    g = np.frombuffer(raw2, np.uint8).reshape(-1, int(fh*0.6), int(fw*0.7)).astype(float)[:len(fr)]
    lap2 = g[:, 1:-1, 1:-1] * 4 - g[:, :-2, 1:-1] - g[:, 2:, 1:-1] - g[:, 1:-1, :-2] - g[:, 1:-1, 2:]
    focus = lap2.var(axis=(1, 2))
    n = min(len(fr), len(focus))
    res = {"t": np.arange(n) / fps, "motion": motion[:n], "sharp": sharp[:n], "focus": focus[:n]}
    _AN_CACHE[key] = res
    return res


FOCUS_FLOOR = {"value": 0.0}     # 전체 클립 기준 절대 하한 (auto_edit 가 설정)


def focus_threshold(a):
    """이 클립에서 '초점 맞음'으로 인정하는 최소값: 클립 최고 초점의 60% 와 전체 하한 중 큰 값."""
    return max(0.6 * float(np.percentile(a["focus"], 90)), FOCUS_FLOOR["value"])


def clip_in_focus(path):
    """클립 전체에 초점 맞는 구간이 하나라도 있는지."""
    if Path(path).suffix.lower() in IMG_EXT:
        return image_focus(path) >= FOCUS_FLOOR["value"]
    a = analyze_clip(path)
    return a is not None and float(np.percentile(a["focus"], 90)) >= FOCUS_FLOOR["value"]


def image_focus(path):
    a = _AN_CACHE.get(("img", str(path)))
    if a is None:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                              "scale=360:640:force_original_aspect_ratio=increase,crop=360:640,crop=252:384,format=gray",
                              "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True).stdout
        g = np.frombuffer(raw, np.uint8).reshape(384, 252).astype(float)
        lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
        a = float(lap.var()); _AN_CACHE[("img", str(path))] = a
    return a


def segment_motion(path, start, dur):
    a = analyze_clip(path)
    if a is None:
        return 0.0
    m = (a["t"] >= start) & (a["t"] <= start + dur)
    return float(a["motion"][m].mean()) if m.any() else 0.0


def best_start(path, dur, used=(), skip_head=0.5):
    """움직임·선명도 점수가 가장 높은 구간의 시작점. 이미 쓴 구간과 겹치면 감점."""
    a = analyze_clip(path)
    L = probe_duration(path)
    if a is None or L < dur:
        return None
    def norm(x):
        return (x - x.min()) / (np.ptp(x) + 1e-6)
    score = norm(a["motion"]) * 0.5 + norm(np.log1p(a["focus"])) * 0.5
    thr = focus_threshold(a)
    best, best_s = None, -1e9
    for st in np.arange(min(skip_head, max(L - dur, 0)), L - dur + 1e-6, 1 / 6):
        m = (a["t"] >= st) & (a["t"] <= st + dur)
        if not m.any():
            continue
        if a["focus"][m].min() < thr:        # 구간 안에 초점 나간 프레임이 하나라도 있으면 제외
            continue
        sc = score[m].mean()
        for (u0, u1) in used:
            ov = max(0, min(st + dur, u1) - max(st, u0))
            sc -= 0.8 * ov / dur
        if sc > best_s:
            best, best_s = float(st), sc
    return None if best is None else round(best, 3)     # None = 초점 맞는 구간 없음 → 이 클립 사용 안 함


MOVES = [(0.00, 0.00), (-0.03, 0.00), (0.03, 0.00), (0.00, -0.025), (0.00, 0.02)]   # 밀어넣기 + 미세 이동 방향


def motion_filter(i, dur, mcfg):
    """정적인 컷에 카메라 무빙 생성: 부드러운 가감속(ease-in-out) 푸시인 + 미세 슬라이드.
    2배 해상도에서 계산해 떨림 없이 매끄럽게."""
    z1 = float(mcfg.get("zoom", 1.10))
    dx, dy = MOVES[i % len(MOVES)]
    n = max(int(dur * FPS), 2)
    ease = f"(0.5-0.5*cos(PI*on/{n}))"
    z = f"1+({z1}-1)*{ease}"
    x = f"(iw-iw/zoom)/2+{dx}*iw*{ease}"
    y = f"(ih-ih/zoom)/2+{dy}*ih*{ease}"
    return (f",scale={W*2}:{H*2}:flags=lanczos,zoompan=z='{z}':x='max(0,min(iw-iw/zoom,{x}))'"
            f":y='max(0,min(ih-ih/zoom,{y}))':d=1:s={W}x{H}:fps={FPS}")


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
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(2)))   # 가까운 그림자 (밝은 음식 위 가독성)
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
    elif brand.get("parts"):
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
    if brand.get("logo_image"):
        logo = Image.open(brand["logo_image"]).convert("RGBA"); lw = 520
        logo = logo.resize((lw, int(logo.height * lw / logo.width)))
        img.alpha_composite(logo, ((W - lw) // 2, int(H / 2 - logo.height - 10)))
    tw = sum(d.textlength(t, font=f) for t, _ in brand.get("parts", [])); x = (W - tw) / 2
    for t, c in brand.get("parts", []):
        d.text((x, H / 2 - 110), t, font=f, fill=c); x += d.textlength(t, font=f)
    f2 = font(DEFAULTS["style"]["font"], 46); sub = ocfg.get("text", "present")
    d.text(((W - d.textlength(sub, font=f2)) / 2, H / 2 + 20), sub, font=f2, fill="#FFFFFF")
    img.save(out_png)


def pick_cover_frame(scenes, base):
    """표지용 프레임: 모든 장면 소스 중 초점이 가장 선명한 순간 (영상은 프레임, 사진은 그대로)."""
    best, best_s = None, -1
    for sc in scenes:
        src = (base / sc["file"]).resolve()
        if src.suffix.lower() in IMG_EXT:
            f = image_focus(src)
            if f > best_s:
                best, best_s = (src, None), f
            continue
        a = analyze_clip(src)
        if a is None:
            continue
        i = int(np.argmax(a["focus"]))
        if a["focus"][i] > best_s:
            best, best_s = (src, float(a["t"][i])), float(a["focus"][i])
    return best


def render_cover(video, ccfg, out, cfg=None, scenes=None, base=None):
    """표지: 가장 선명한 음식 프레임(색보정 적용) + 노랑/흰 굵은 고딕 3줄 (레퍼런스 표지 공식)."""
    lines = ccfg.get("lines")
    if not lines:
        return None
    frame = out.with_suffix(".frame.png")
    pick = pick_cover_frame(scenes, base) if scenes and "frame_time" not in ccfg.get("_user", {}) else None
    if pick:
        src, t = pick
        is_img = src.suffix.lower() in IMG_EXT
        cf = color_filter(src, 0, 0, is_img, (cfg or {}).get("color"))
        run(["ffmpeg", "-y", "-v", "error"] + ([] if t is None else ["-ss", f"{t:.2f}"]) + ["-i", str(src),
             "-frames:v", "1", "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}{cf}", str(frame)])
    else:
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(ccfg.get("frame_time", 0.3)), "-i", str(video),
             "-frames:v", "1", str(frame)])
    img = Image.open(frame).convert("RGBA"); frame.unlink()
    d = ImageDraw.Draw(img)
    sizes = ccfg.get("sizes", [96, 84, 60])
    y = int(H * ccfg.get("y", 0.30))
    for i, text in enumerate(lines):
        text = text.replace("·", ", ").replace("ㆍ", ", ")   # 표지 폰트에 가운뎃점 글자가 없음
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
        mc = cfg.get("motion", {})
        move = sc.get("move", "auto")
        if move is True or (move == "auto" and mc.get("auto", True)
                            and segment_motion(src, start, dur * speed) < float(mc.get("threshold", 5.0))):
            vf += motion_filter(i, dur, mc)
            print(f"    · 움직임 적음 → 카메라 무빙 생성")
    vf += f"{cf},setsar=1,tpad=stop_mode=clone:stop_duration=2,format=gbrp[g];" + \
        glow_graph("g", "gl", cfg.get("look")) + f";[gl]format=yuv420p{look_filter(cfg.get('look'))}"
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
    brand_file = ROOT / "brand.yaml"          # 대행사 공통 브랜드 설정 (실행 파일에서 한 번 입력)
    if brand_file.exists():
        cfg = deep_merge(yaml.safe_load(brand_file.read_text(encoding="utf-8")) or {}, cfg)
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
        has_brand = bool(cfg["brand"].get("parts") or cfg["brand"].get("logo_image"))
        if cfg["outro"].get("enabled", True) and has_brand:
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
        # 자막은 PNG를 시간순으로 이어 붙인 '자막 트랙' 하나로 합성 (오버레이 1회 → 빠름)
        blank = tmp / "blank.png"; Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
        lines, t = [], 0.0
        for k, c in enumerate(sorted(caps, key=lambda c: c["start"])):
            st, en = max(c["start"], t), min(c["end"], outro_start)
            if en <= st:
                continue
            if st > t:
                lines += [f"file '{blank}'", f"duration {st - t:.3f}"]
            png = tmp / f"cap_{k:03d}.png"; render_caption(c["text"], cfg["style"], png)
            lines += [f"file '{png}'", f"duration {en - st:.3f}"]; t = en
        lines += [f"file '{blank}'", f"duration {max(total - t, 0.04):.3f}", f"file '{blank}'"]
        (tmp / "caps.txt").write_text("\n".join(lines) + "\n")
        # 자막 트랙을 알파 포함 영상으로 먼저 굽는다 (타임스탬프 고정)
        captrack = tmp / "captrack.mov"
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "caps.txt"),
             "-vf", f"fps={FPS},format=argb", "-t", f"{total:.3f}", "-c:v", "qtrle", str(captrack)])
        ins = ["-i", str(joined), "-loop", "1", "-i", str(logo), "-i", str(captrack)]
        vf = (f"[2:v]format=rgba[cap];"
              f"[0:v][1:v]overlay=0:0:enable='lt(t,{outro_start:.3f})'[v0];"
              f"[v0][cap]overlay=0:0:eof_action=pass[v1]")
        last = "v1"
        n_in = 3

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
            cov = render_cover(joined, cfg["cover"], out_path.with_name(out_path.stem + "_cover.jpg"),
                               cfg, cfg["scenes"], base)
            msg += f"\n표지: {cov}"
        print(msg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: python make_reel.py project.yaml [project2.yaml ...]")
    for p in sys.argv[1:]:
        print(f"▶ {p}"); make(p)

#!/usr/bin/env python3
"""1단계: 촬영 영상 + 매장 이름 → 레퍼런스 스타일 나레이션 대본 + 표지 문구.

- 촬영 폴더의 각 클립에서 대표 프레임을 뽑아 Claude가 메뉴·장면을 인식
- 웹 검색으로 매장 메뉴·가격·특징 확인 (검색으로 확인 못 한 사실은 대본에 쓰지 않음)
- 레퍼런스(진흙오리 릴스) 대본 공식대로 작성
- 결과: 촬영폴더/script.md (녹음용 대본), script.json (표지 문구 등, 2단계가 자동으로 읽음)

사용법:
  python write_script.py 촬영폴더/ "매장 이름" [--note "대표메뉴 19,900원, 하이볼 있음"]
기본: 맥에 설치된 Claude Code(`claude` 명령, 로그인 상태)로 실행 → API 키 불필요
선택: --api 옵션이면 Anthropic API 사용 (ANTHROPIC_API_KEY 필요)
"""
import argparse, base64, json, re, subprocess, sys
from pathlib import Path


MODEL = "claude-opus-5-5"
MEDIA_EXT = {".mp4", ".mov", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}

STYLE_GUIDE = """
# 레퍼런스 대본 공식 (진흙오리 릴스 분석 결과)
- 길이: 나레이션 약 43초, 전체 280~320음절 (초당 약 7음절, 빠르고 쉼 없이). 출력 전에 한글 음절 수를 세어 이 범위에 맞춘다
- 한 줄 = 자막 1개 = 4~9자 (공백 포함), 0.5~1.5초 분량. 짧게 끊어 쓴다
- 문장은 "-는데 / -고 / -며"로 이어 가다가 구간이 끝날 때만 "-ㅂ니다"로 맺는다
- 구간 순서와 비율:
  1. 훅 (0~4초): 경고/희소성 + 감각어 예고. 예) 스킵하면 / 다시는 못 볼 / 귀르가즘 끝판왕 / 신기한 비주얼을 지닌 / 한정 판매
  2. 메뉴명 공개 (약 4초): "OOO입니다"
  3. 가격 예고와 공개 (5~9초): "그런데.. / 가격이...?" 로 한 박자 끈 뒤 "단돈 OO원에 / 이렇게 나옵니다"
  4. 가성비·통념 깨기 (9~14초): 이 메뉴는 보통 비싸다는 통념 → 다시 가격 강조 ("2만원이 안되는 착한 가격에")
  5. 식감 (14~19초): 의성어·짝 표현 (바삭바삭 / 겉바속촉 / 특제 소스를 바르고 / 진짜 맛있는 거 아시죠?)
  6. 소리·비주얼 (19~29초): 처음 보는 비주얼 / 소리가 진짜 역대급 / 칼을 살짝 대기만 해도 / 썰리는 소리가 벌써 맛있어요 / 멍 때리고 침 흘리게
  7. 먹는 법 (29~31초): "OO와 함께 먹는 게 이 집의 킥이며"
  8. 술·곁들임 (31~35초): "병으로도 잔으로도 팝니다" 같은 짝 표현
  9. 다른 메뉴 (35~42초): 메뉴명 + 한 단어 감상 (단짠단짠 / 실패 없는 조합)
  10. 마무리: 정확히 두 줄 "저장하시고" / "꼭 가보세요" 만 쓴다 (앞에 다른 말 붙이지 않음). 첫 줄 "스킵하면"으로 이어지게(루프)
- 반복 장치: 단돈·진짜·바삭바삭 같은 단어를 2번씩, 짝 표현(겉바속촉, 단짠단짠, 병으로도/잔으로도)
- 금지: 매장 이름·주소·위치는 나레이션에 넣지 않는다 (레퍼런스와 동일). 이모지, 느낌표 남발 금지
- 자막 표기: 마침표·느낌표 없음, 질문/예고에만 "..." "?" 사용
# 표지 공식
- 3줄, 굵은 노랑/흰 글씨: 1줄 비교·후킹(예: 베이징덕보다 맛있는), 2줄 가격+메뉴명(예: 19,900원 진흙통오리튀김), 3줄 부연(예: 믿기지 않는 가격(술안주이자 보양식))
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["facts", "unverified", "lines", "cover", "shot_notes", "section_shots"],
    "properties": {
        "facts": {"type": "array", "items": {"type": "string"},
                  "description": "대본에 쓴 사실과 그 근거(영상/검색 URL/사용자 메모)"},
        "unverified": {"type": "array", "items": {"type": "string"},
                       "description": "확인 못 해서 대본에서 뺐거나 사용자가 확인해야 할 것"},
        "lines": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["section", "text"],
            "properties": {"section": {"type": "string"}, "text": {"type": "string"}}}},
        "cover": {"type": "array", "items": {"type": "string"}},
        "section_shots": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}},
                          "description": "구간 이름(lines의 section과 같게) → 그 구간 대사와 내용이 맞는 원본 촬영 파일명 목록(확장자 포함, 예: 05_1.mov). 대사가 장어면 장어 컷만"},
        "shot_notes": {"type": "array", "items": {"type": "string"},
                       "description": "대본 구간별로 어울리는 촬영 파일과 부족한 컷"},
    },
}


def keyframes(media_dir, per_clip=2, max_images=60):
    files = sorted(p for p in Path(media_dir).rglob("*") if p.suffix.lower() in MEDIA_EXT
                   and not p.name.startswith("reel"))
    if len(files) * per_clip > max_images:      # 클립이 많으면 클립당 1장씩 (영상 수 제한 없음)
        per_clip = 1
    out = []
    for f in files:
        if len(out) >= max_images:
            print(f"  (장면 {max_images}장까지만 분석에 사용, 나머지 클립은 편집에는 모두 사용됨)")
            break
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            times = [None]
        else:
            dur = float(json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(f)],
                capture_output=True, text=True).stdout)["format"]["duration"])
            times = [dur * (k + 1) / (per_clip + 1) for k in range(per_clip)]
        for t in times:
            cmd = ["ffmpeg", "-v", "error"] + ([] if t is None else ["-ss", f"{t:.2f}"]) + \
                  ["-i", str(f), "-frames:v", "1", "-vf", "scale=512:-2", "-f", "image2", "-c:v", "mjpeg", "-"]
            jpg = subprocess.run(cmd, capture_output=True).stdout
            if jpg:
                out.append((f.name, base64.standard_b64encode(jpg).decode()))
    return out


def build_prompt(store, note, image_list_text):
    return f"""매장 이름: {store}
사용자 메모(가장 정확한 정보, 검색 결과와 다르면 이것을 따름): {note or '없음'}

할 일:
1. 촬영 장면 이미지들을 Read 도구로 모두 열어 보고 메뉴와 장면 종류를 파악한다.
{image_list_text}
2. 웹 검색으로 이 매장의 대표 메뉴, 가격, 특징(곁들임, 술, 다른 메뉴)을 확인한다.
3. 아래 공식대로 나레이션 대본과 표지 3줄을 쓴다.
규칙: 영상이나 검색 결과, 사용자 메모로 확인한 사실만 쓴다. 가격을 확인 못 했으면 가격 문장 대신
가성비 표현을 쓰고 unverified에 적는다. 촬영에 없는 메뉴는 대본에 넣지 않는다. 파일을 만들거나 수정하지 않는다.
{STYLE_GUIDE}
출력: 마지막에 아래 스키마의 JSON 하나만 ```json 코드블록으로 출력한다.
{json.dumps(SCHEMA, ensure_ascii=False)}"""


def run_claude_code(prompt, workdir):
    """맥의 Claude Code를 비대화 모드로 실행 (구독 로그인 사용, API 키 불필요)."""
    import shutil
    if not shutil.which("claude"):
        sys.exit("Claude Code(claude 명령)를 찾을 수 없습니다. 설치·로그인 후 다시 실행하세요.")
    r = subprocess.run(["claude", "-p", prompt, "--output-format", "text",
                        "--allowedTools", "Read", "WebSearch", "WebFetch"],
                       cwd=workdir, capture_output=True, text=True, timeout=900)
    if r.returncode:
        sys.exit("Claude Code 실행 실패:\n" + (r.stderr or r.stdout)[-1500:])
    return r.stdout


def run_api(content):
    import anthropic
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": content}]
    for _ in range(5):   # 웹 검색이 길어지면 pause_turn으로 끊겨 돌아오므로 이어서 요청
        with client.beta.messages.stream(
            model=MODEL, max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "high"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
            messages=messages,
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason == "refusal":
            sys.exit("모델이 요청을 거절했습니다. 매장 이름/메모를 확인해 주세요.")
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    return "".join(b.text for b in resp.content if b.type == "text")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media_dir"); ap.add_argument("store")
    ap.add_argument("--note", default="", help="사장님이 아는 사실 (메뉴, 가격 등) - 검색보다 우선")
    ap.add_argument("--api", action="store_true", help="Claude Code 대신 Anthropic API 사용")
    ap.add_argument("--out", help="대본 저장 폴더 (기본: 촬영 폴더)")
    a = ap.parse_args()
    media = Path(a.media_dir).resolve()
    out = Path(a.out).resolve() if a.out else media
    out.mkdir(parents=True, exist_ok=True)

    print("▶ 촬영 영상에서 장면 추출 중...")
    frames = keyframes(media)
    if not frames:
        sys.exit("촬영 폴더에 영상/사진이 없습니다.")

    if a.api:
        content = []
        for name, b64 in frames:
            content.append({"type": "text", "text": f"[파일: {name}]"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
        content.append({"type": "text", "text": build_prompt(a.store, a.note, "(이미지는 위에 첨부됨)")})
        print("▶ (API) 매장 정보 검색 + 대본 작성 중 (1~3분)...")
        text = run_api(content)
    else:
        fdir = out / "_script_frames"; fdir.mkdir(exist_ok=True)
        listing = []
        for k, (name, b64) in enumerate(frames):
            fp = fdir / f"{k:02d}_{Path(name).stem}.jpg"
            fp.write_bytes(base64.standard_b64decode(b64))
            listing.append(f"   - {fp.name} (원본 파일: {name})")
        print("▶ (Claude Code) 장면 확인 + 매장 정보 검색 + 대본 작성 중 (2~5분)...")
        text = run_claude_code(build_prompt(a.store, a.note, "   이미지 폴더: 현재 폴더\n" + "\n".join(listing)), fdir)
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.findall(r"(\{.*\})", text, re.S)
    if not m:
        sys.exit("대본 결과를 읽지 못했습니다:\n" + text[-1500:])
    data = json.loads(m[-1])
    for k in SCHEMA["required"]:
        data.setdefault(k, [])

    (out / "script.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# {a.store} 릴스 대본", "", "## 녹음용 대본 (한 줄씩 끊어서, 빠르게, 쉬지 않고)", ""]
    sec = None
    for ln in data["lines"]:
        if ln["section"] != sec:
            sec = ln["section"]; md += ["", f"**[{sec}]**"]
        md.append(ln["text"])
    syl = sum(len(re.sub(r"[^가-힣0-9]", "", l["text"])) for l in data["lines"])
    md += ["", f"_약 {syl}음절 · 예상 {syl / 7:.0f}초 (레퍼런스: 약 300음절 · 43초)_", "",
           "## 표지 문구", *[f"{i+1}. {c}" for i, c in enumerate(data["cover"])], "",
           "## 확인한 사실과 근거", *[f"- {f}" for f in data["facts"]], "",
           "## 확인 필요", *([f"- {u}" for u in data["unverified"]] or ["- 없음"]), "",
           "## 컷 메모", *[f"- {s}" for s in data["shot_notes"]]]
    (out / "script.md").write_text("\n".join(md), encoding="utf-8")
    import shutil; shutil.rmtree(out / "_script_frames", ignore_errors=True)
    print(f"완료: {out / 'script.md'}")
    if data["unverified"]:
        print("⚠ 확인이 필요한 항목이 있습니다. script.md 의 '확인 필요'를 보세요.")


if __name__ == "__main__":
    main()

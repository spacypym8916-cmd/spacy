# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10"]
# ///
"""서울 골목 사거리 장면에 쓸 한글 간판 텍스처를 PIL로 만듭니다.

세로 간판 5개, 가로 간판 7개, 모퉁이 가게 'CONNECT AI LAB' 간판 1개를
PNG로 저장하고, 장면 스크립트가 읽을 signs.json(이름, 크기, 비율)을 함께 씁니다.
모든 상호는 가상의 일반 업종명이며 실존 상호나 브랜드는 쓰지 않습니다.

사용법:
    uv run blender/make_signs.py                 # 시스템 한글 글꼴 자동 탐색
    uv run blender/make_signs.py --font 경로.ttf  # 글꼴 직접 지정
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent

# (파일 이름, 간판 글자, 보조 글자, 바탕색, 글자색, 테두리색)
VERTICAL = [
    ("v_cafe", "카페", "COFFEE", (92, 58, 38), (250, 236, 210), (250, 236, 210)),
    ("v_piano", "피아노학원", "", (24, 44, 104), (255, 255, 255), (255, 214, 64)),
    ("v_dental", "치과", "", (255, 255, 255), (20, 92, 200), (20, 92, 200)),
    ("v_karaoke", "노래연습장", "", (128, 20, 120), (255, 232, 60), (255, 120, 220)),
    ("v_pcbang", "PC방", "24H", (14, 14, 18), (60, 230, 255), (60, 230, 255)),
]
HORIZONTAL = [
    ("h_bunsik", "분식", "김밥 · 떡볶이 · 라면", (230, 60, 40), (255, 255, 255), (255, 220, 80)),
    ("h_mart", "마트", "식품 · 생활용품", (20, 120, 70), (255, 255, 255), (255, 255, 255)),
    ("h_laundry", "세탁", "드라이클리닝 · 수선", (40, 110, 200), (255, 255, 255), (200, 230, 255)),
    ("h_pharmacy", "약국", "", (255, 255, 255), (30, 140, 70), (30, 140, 70)),
    ("h_chicken", "치킨", "후라이드 · 양념", (250, 190, 20), (120, 30, 10), (120, 30, 10)),
    ("h_flower", "꽃집", "꽃다발 · 화분", (250, 240, 236), (210, 60, 110), (120, 170, 90)),
    ("h_realty", "부동산", "매매 · 전세 · 월세", (16, 52, 92), (255, 255, 255), (230, 190, 90)),
]

KOREAN_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/NanumGothicExtraBold.ttf",
    "/Library/Fonts/NanumGothicBold.ttf",
    str(Path.home() / "Library/Fonts/NanumGothicExtraBold.ttf"),
    str(Path.home() / "Library/Fonts/NanumGothicBold.ttf"),
    # Windows
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    # Linux
    "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
MONO_FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consolab.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]


class FontSpec:
    """A font file plus the face index to use (bold when the file has one)."""

    def __init__(self, path: str):
        self.path = path
        self.index = 0
        if path.lower().endswith(".ttc"):
            for i in range(16):
                try:
                    style = ImageFont.truetype(path, 20, index=i).getname()[1].lower()
                except OSError:
                    break
                if "bold" in style or "heavy" in style:
                    self.index = i
                    if "extra" in style or "heavy" in style:
                        break

    def get(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.path, size, index=self.index)


def find_font(candidates: list[str], sample: str) -> FontSpec | None:
    for path in candidates:
        if not Path(path).is_file():
            continue
        spec = FontSpec(path)
        font = spec.get(40)
        # A font without these glyphs draws the same box (tofu) for each; compare two glyphs.
        glyphs = [_glyph_bytes(font, ch) for ch in sample]
        if all(glyphs) and glyphs[0] != glyphs[-1]:
            return spec
    return None


def _glyph_bytes(font: ImageFont.FreeTypeFont, ch: str) -> bytes:
    img = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(img).text((8, 4), ch, font=font, fill=255)
    return img.tobytes() if img.getbbox() else b""


def fit_font(spec: FontSpec, text: str, max_w: float, max_h: float, start: int = 400) -> ImageFont.FreeTypeFont:
    size = start
    while size > 8:
        font = spec.get(size)
        l, t, r, b = font.getbbox(text)
        if r - l <= max_w and b - t <= max_h:
            return font
        size = int(size * 0.94)
    return spec.get(8)


def draw_centered(draw: ImageDraw.ImageDraw, cx: float, cy: float, text: str, font, fill) -> None:
    l, t, r, b = font.getbbox(text)
    draw.text((cx - (l + r) / 2, cy - (t + b) / 2), text, font=font, fill=fill)


def panel(w: int, h: int, bg, border, radius: int, inset: int, stroke: int) -> Image.Image:
    img = Image.new("RGB", (w, h), tuple(int(c * 0.55) for c in bg))  # sign box edge, darker
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, w - 5, h - 5], radius=radius, fill=bg)
    d.rounded_rectangle([inset, inset, w - inset - 1, h - inset - 1], radius=max(2, radius - inset // 2),
                        outline=border, width=stroke)
    return img


def make_vertical(spec: FontSpec, title: str, sub: str, bg, fg, border, w=256, h=1024) -> Image.Image:
    img = panel(w, h, bg, border, radius=26, inset=16, stroke=6)
    d = ImageDraw.Draw(img)
    chars = _vertical_units(title)
    top, bottom = 48, h - (150 if sub else 48)
    cell_h = (bottom - top) / len(chars)
    font = fit_font(spec, "가", (w - 70), cell_h * 0.86)
    for i, ch in enumerate(chars):
        f = font if len(ch) == 1 else fit_font(spec, ch, w - 70, cell_h * 0.8)
        draw_centered(d, w / 2, top + cell_h * (i + 0.5), ch, f, fg)
    if sub:
        d.line([(40, h - 140), (w - 40, h - 140)], fill=border, width=4)
        draw_centered(d, w / 2, h - 82, sub, fit_font(spec, sub, w - 70, 70), fg)
    return img


def _vertical_units(title: str) -> list[str]:
    # Keep runs of Latin letters together ("PC") so they read across, Hangul one per line.
    units, buf = [], ""
    for ch in title:
        if ch.isascii() and ch.isalnum():
            buf += ch
            continue
        if buf:
            units.append(buf)
            buf = ""
        units.append(ch)
    if buf:
        units.append(buf)
    return units


def make_horizontal(spec: FontSpec, title: str, sub: str, bg, fg, border, w=1280, h=256) -> Image.Image:
    img = panel(w, h, bg, border, radius=18, inset=14, stroke=5)
    d = ImageDraw.Draw(img)
    if title == "약국":
        # Pharmacy: a green cross badge beside the name.
        cx, cy, s = 200, h / 2, 64
        d.rounded_rectangle([cx - s, cy - s * 0.32, cx + s, cy + s * 0.32], radius=8, fill=fg)
        d.rounded_rectangle([cx - s * 0.32, cy - s, cx + s * 0.32, cy + s], radius=8, fill=fg)
        draw_centered(d, w / 2 + 70, h / 2, title, fit_font(spec, title, w * 0.5, h * 0.66), fg)
        return img
    if sub:
        title_font = fit_font(spec, title, w * 0.42, h * 0.66)
        draw_centered(d, w * 0.29, h / 2, title, title_font, fg)
        d.line([(w * 0.56, 52), (w * 0.56, h - 52)], fill=border, width=4)
        draw_centered(d, w * 0.775, h / 2, sub, fit_font(spec, sub, w * 0.38, h * 0.26), fg)
    else:
        draw_centered(d, w / 2, h / 2, title, fit_font(spec, title, w * 0.6, h * 0.66), fg)
    return img


def make_connect(spec: FontSpec, w=1280, h=256) -> Image.Image:
    img = Image.new("RGB", (w, h), (8, 8, 8))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, w - 11, h - 11], outline=(38, 38, 38), width=4)
    text = "CONNECT AI LAB"
    font = fit_font(spec, text, w * 0.78, h * 0.42)
    l, t, r, b = font.getbbox(text)
    tw = r - l
    x0 = (w - tw) / 2 - 34
    cy = h / 2
    d.text((x0 - l, cy - (t + b) / 2), text, font=font, fill=(255, 255, 255))
    # Green block cursor right after the text, as on a terminal.
    cap_t, cap_b = font.getbbox("C")[1], font.getbbox("C")[3]
    cw = max(18, int((cap_b - cap_t) * 0.55))
    cx0 = x0 + tw + 22
    d.rectangle([cx0, cy - (cap_b - cap_t) / 2 - 6, cx0 + cw, cy + (cap_b - cap_t) / 2 + 6], fill=(40, 255, 110))
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description="한글 간판 텍스처 만들기")
    ap.add_argument("--font", help="한글 글꼴 파일 경로 (.ttf/.otf/.ttc)")
    ap.add_argument("--mono-font", help="CONNECT AI LAB 간판용 고정폭 글꼴 경로")
    ap.add_argument("--out", default=str(HERE / "signs"), help="저장 폴더 (기본: blender/signs)")
    args = ap.parse_args()

    ko = FontSpec(args.font) if args.font else find_font(KOREAN_FONT_CANDIDATES, "간판가")
    if ko is None:
        print("[오류] 한글 글꼴을 찾지 못했습니다. --font 로 .ttf 경로를 지정하세요.")
        return 1
    mono = FontSpec(args.mono_font) if args.mono_font else (find_font(MONO_FONT_CANDIDATES, "CAI") or ko)
    print(f"한글 글꼴: {ko.path} (face {ko.index})")
    print(f"영문 글꼴: {mono.path} (face {mono.index})")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"vertical": [], "horizontal": [], "corner": None}
    for name, title, sub, bg, fg, border in VERTICAL:
        make_vertical(ko, title, sub, bg, fg, border).save(out / f"{name}.png")
        manifest["vertical"].append({"name": name, "title": title, "file": f"{name}.png", "aspect": 0.25})
    for name, title, sub, bg, fg, border in HORIZONTAL:
        make_horizontal(ko, title, sub, bg, fg, border).save(out / f"{name}.png")
        manifest["horizontal"].append({"name": name, "title": title, "file": f"{name}.png", "aspect": 5.0})
    make_connect(mono).save(out / "connect_ai_lab.png")
    manifest["corner"] = {"name": "connect_ai_lab", "title": "CONNECT AI LAB", "file": "connect_ai_lab.png", "aspect": 5.0}
    (out / "signs.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"간판 {len(VERTICAL) + len(HORIZONTAL) + 1}개를 {out} 에 저장했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

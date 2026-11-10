"""화면 번역 덮개 그리기 (PIL). 11/10 tk 캔버스 → PIL 로 바꿈.

tk 캔버스로 그릴 땐 글자 크기를 칸에 '맞을 때까지 줄여서' 글자가 콩알만 해지고, 정렬·줄 간격이 원본과 달라 어색했다.
여기서는 원본 슬라이드처럼 보이게:
  - 글자 크기 = 원본 줄 높이에 맞춤 (한국어가 넘치면 칸을 늘리지 크기는 75% 밑으로 안 줄임)
  - 굵기: 큰 글자(제목)는 Pretendard SemiBold, 본문은 Medium
  - 정렬: 원본 줄들이 가운데 정렬이면 가운데, 아니면 왼쪽
  - 줄 간격: 원본 줄 간격을 따라감
  - 칸 = 원본 배경색으로 칠한 불투명 판 → 글자 가장자리(안티에일리어싱)가 투명색과 섞이지 않는다

render(size, blocks, key) → RGB 이미지. key 색 자리는 화면에서 뚫린다.
"""
import os
import statistics

from PIL import Image, ImageDraw, ImageFont

_FONTS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts")
_USER_FONTS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")


def _font_path(weight):
    names = {"title": ["Pretendard-SemiBold.otf", "malgunbd.ttf"], "body": ["Pretendard-Medium.otf", "malgun.ttf"]}[weight]
    for n in names:
        for d in (_FONTS, _USER_FONTS):
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
    return None


_cache = {}


def font(weight, px):
    key = (weight, px)
    if key not in _cache:
        p = _font_path(weight)
        _cache[key] = ImageFont.truetype(p, px) if p else ImageFont.load_default()
    return _cache[key]


def _wrap(draw, text, f, width):
    """한국어는 띄어쓰기 단위로, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines, cur = [], ""
    for word in text.split(" "):
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=f) <= width or not cur:
            cur = test
            while draw.textlength(cur, font=f) > width and len(cur) > 1:  # 한 단어가 칸보다 길 때
                cut = len(cur)
                while cut > 1 and draw.textlength(cur[:cut], font=f) > width:
                    cut -= 1
                lines.append(cur[:cut])
                cur = cur[cut:]
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _align(b, frame_w):
    ls = b["lines"]
    if len(ls) >= 2:
        lefts = [l["x"] for l in ls]
        centers = [l["x"] + l["w"] / 2 for l in ls]
        return "center" if statistics.pstdev(centers) < statistics.pstdev(lefts) * 0.6 else "left"
    return "left"  # 한 줄짜리는 원래 자리(왼쪽)에서 시작 — 가운데 열 소제목이 화면 가운데로 끌려가던 것


def layout(blocks, frame_w):
    """각 칸의 글꼴·줄·자리를 정한다. b['draw'] = {...}"""
    tmp = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    hs = sorted(b["line_h"] for b in blocks) or [20]
    body_h = hs[len(hs) // 2]
    for b in blocks:
        if not b.get("ko"):
            continue
        weight = "title" if b["line_h"] > body_h * 1.25 else "body"
        n = len(b["lines"])
        pitch = (b["lines"][-1]["y"] - b["lines"][0]["y"]) / (n - 1) if n > 1 else b["line_h"] * 1.35
        want = max(10, int(b["line_h"] * 1.02))   # 원본 글자 키에 맞춘 글꼴 크기(px)
        width = max(b["w"], 40)
        lo = max(9, int(want * 0.6))
        for px in range(want, lo - 1, -1):
            f = font(weight, px)
            lines = _wrap(tmp, b["ko"], f, width)
            step = max(pitch * px / want, px * 1.25)
            if len(lines) <= max(n, 1) or (step * len(lines) <= b["h"] * 1.6 and px <= want * 0.85) or px == lo:
                break
        widest = max(tmp.textlength(l, font=f) for l in lines)
        align = _align(b, frame_w)
        b["draw"] = {"font": f, "lines": lines, "step": step, "align": align,
                     "w": max(b["w"], widest), "h": max(b["h"], step * (len(lines) - 1) + px * 1.15), "px": px}
    return blocks


def render(size, blocks, key=(255, 0, 254)):
    W, H = size
    img = Image.new("RGB", (W, H), key)
    d = ImageDraw.Draw(img)
    layout(blocks, W)
    for b in blocks:
        dr = b.get("draw")
        if not dr:
            continue
        pad = max(3, dr["px"] // 6)
        if dr["align"] == "center":
            cx = b["x"] + b["w"] / 2
            x0 = cx - dr["w"] / 2
        else:
            x0 = b["x"]
        # 원본 칸과 번역 칸을 둘 다 덮는 판 (원본 영어가 삐져나오지 않게)
        bx0 = min(b["x"], x0) - pad
        bx1 = max(b["x"] + b["w"], x0 + dr["w"]) + pad
        by0 = b["y"] - pad
        by1 = b["y"] + max(b["h"], dr["h"]) + pad
        d.rectangle([bx0, by0, bx1, by1], fill=b["bg"])
        y = b["y"] + (b["line_h"] - dr["px"]) * 0.35
        for ln in dr["lines"]:
            lw = d.textlength(ln, font=dr["font"])
            x = (b["x"] + b["w"] / 2 - lw / 2) if dr["align"] == "center" else b["x"]
            d.text((x, y), ln, font=dr["font"], fill=b["fg"])
            y += dr["step"]
    return img


def preview(shot, blocks):
    """시험용: 실제 화면 캡처 위에 덮개를 얹은 그림."""
    key = (255, 0, 254)
    over = render(shot.size, blocks, key)
    mask = Image.eval(over.convert("RGB"), lambda v: v).point(lambda v: 0)
    import numpy as np  # noqa: F401  (없으면 아래 느린 방법)
    return _composite(shot, over, key)


def _composite(shot, over, key):
    shot = shot.convert("RGB").copy()
    px_o = over.load()
    W, H = over.size
    m = Image.new("L", over.size, 0)
    pm = m.load()
    for y in range(H):
        for x in range(W):
            if px_o[x, y] != key:
                pm[x, y] = 255
    shot.paste(over, (0, 0), m)
    return shot

"""동영상 동시 통역기 앱 아이콘 만들기 (11/08). 희랑 메이커 마크는 앱 아이콘이 아니라 '만든 사람 · 희랑' 자리에 쓴다.

모양: 앱 타일(검은 둥근 사각형) 안에 말풍선 둘 — 뒤 흰 풍선 'A'(들은 영어), 앞 파란 풍선 '가'(옮긴 한국어).
파랑 = 앱 주 버튼 색(#1f6bff). 1024 로 그려서 줄인다 (작은 크기 테두리 깨짐 줄이기).

실행: .venv\\Scripts\\python.exe ui\\brand\\make_icon.py  → app.ico, app_256.png, app.svg
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
INK, BLUE, WHITE = (17, 19, 24), (31, 107, 255), (255, 255, 255)
FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def bubble(d, box, r, fill, tail_left, gap=0):
    """둥근 말풍선 + 아래쪽 꼬리. gap>0 이면 타일 색 테두리를 둘러 겹친 풍선과 떨어뜨린다."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    tx = x0 + w * 0.22 if tail_left else x1 - w * 0.22
    sgn = -1 if tail_left else 1
    tail = [(tx - 60, y1 - 10), (tx + 60, y1 - 10), (tx + sgn * 40, y1 + 95)]
    if gap:
        d.rounded_rectangle((x0 - gap, y0 - gap, x1 + gap, y1 + gap), r + gap, fill=INK)
        d.polygon([(tail[0][0] - gap, tail[0][1]), (tail[1][0] + gap, tail[1][1]), (tail[2][0] + sgn * gap * 0.6, tail[2][1] + gap * 1.4)], fill=INK)
    d.rounded_rectangle(box, r, fill=fill)
    d.polygon(tail, fill=fill)


def centered(d, box, text, font, fill, dy=0):
    x0, y0, x1, y1 = box
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.text(((x0 + x1) / 2 - (l + r) / 2, (y0 + y1) / 2 - (t + b) / 2 + dy), text, font=font, fill=fill)


def draw(size=1024):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, size - 1, size - 1), 224, fill=INK)
    back = (150, 170, 610, 560)     # 흰 풍선 'A'
    front = (414, 414, 874, 804)    # 파란 풍선 '가'
    bubble(d, back, 110, WHITE, tail_left=True)
    bubble(d, front, 110, BLUE, tail_left=False, gap=34)
    fa = ImageFont.truetype(os.path.join(FONTS, "segoeuib.ttf"), 300)
    fk = ImageFont.truetype(os.path.join(FONTS, "malgunbd.ttf"), 270)
    centered(d, (back[0], back[1], back[2] - 110, back[3]), "A", fa, INK, dy=-6)
    centered(d, front, "가", fk, WHITE, dy=-4)
    return im


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
<rect width="1024" height="1024" rx="224" fill="#111318"/>
<rect x="150" y="170" width="460" height="390" rx="110" fill="#fff"/><path d="M191 550H311L211 655z" fill="#fff"/>
<rect x="380" y="380" width="528" height="458" rx="144" fill="#111318"/><path d="M679 794H867L833 947z" fill="#111318"/>
<rect x="414" y="414" width="460" height="390" rx="110" fill="#1f6bff"/><path d="M713 794H833L813 899z" fill="#1f6bff"/>
<text x="325" y="470" font-family="Segoe UI,Arial,sans-serif" font-weight="700" font-size="300" text-anchor="middle" fill="#111318">A</text>
<text x="644" y="700" font-family="Malgun Gothic,Pretendard,sans-serif" font-weight="700" font-size="270" text-anchor="middle" fill="#fff">가</text>
</svg>
"""

if __name__ == "__main__":
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "app_256.png"))
    big.save(os.path.join(HERE, "app.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    with open(os.path.join(HERE, "app.svg"), "w", encoding="utf-8") as f:
        f.write(SVG)
    # 작은 크기 미리보기 (확인용, git 엔 안 올림)
    sheet = Image.new("RGBA", (16 + 24 + 32 + 48 + 64 + 128 + 70, 140), (233, 235, 239, 255))
    x = 10
    for s in (16, 24, 32, 48, 64, 128):
        sheet.alpha_composite(big.resize((s, s), Image.LANCZOS), (x, 130 - s))
        x += s + 10
    sheet.save(os.path.join(HERE, "..", "..", "logs", "icon_preview.png"))
    print("만듦: app.ico, app_256.png, app.svg")

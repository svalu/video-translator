"""번역기 창을 캡처해서 'Caption'(홈) 메뉴에 빨간 박스를 그린다. 결과: where_caption.png"""
import os
import uiautomation as auto
from PIL import ImageGrab, ImageDraw, ImageFont

win = auto.WindowControl(searchDepth=1, Name="LiveCaptions Translator")
if not win.Exists(3, 0.5):
    raise SystemExit("번역기 창을 못 찾았어")
win.SetActive()
item = win.TextControl(Name="Caption")
wr = win.BoundingRectangle
target = item.BoundingRectangle if item.Exists(2, 0.3) else None
img = ImageGrab.grab(bbox=(wr.left, wr.top, wr.right, wr.bottom), all_screens=True)
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("malgunbd.ttf", 18)
except OSError:
    font = ImageFont.load_default()
if target:
    # 메뉴 항목(아이콘 + 글자) 한 줄 전체를 감싼다
    x0, y0 = target.left - wr.left - 40, target.top - wr.top - 8
    x1, y1 = target.right - wr.left + 12, target.bottom - wr.top + 8
    d.rectangle([x0, y0, x1, y1], outline=(230, 40, 60), width=4)
    label = "여기 (Caption = 홈)"
    tw = d.textlength(label, font=font)
    lx, ly = x1 + 10, y0
    d.rectangle([lx, ly, lx + tw + 14, ly + 30], fill=(230, 40, 60))
    d.text((lx + 7, ly + 4), label, fill="white", font=font)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "where_caption.png")
img.save(out)
print("saved", out, "target" if target else "no-target", wr, target)

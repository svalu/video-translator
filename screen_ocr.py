"""화면 글자 읽기 (윈도우 기본 OCR, Windows.Media.Ocr). 무료·로컬.

이 PC 에는 OCR 언어가 '한국어'만 깔려 있는데(11/06), 한국어 엔진이 영어 글자도 같이 읽는다.
영어 엔진(en-US)이 깔려 있으면 그걸 먼저 쓴다.

read_lines(PIL 이미지) → [{"text", "x", "y", "w", "h"}]  (이미지 픽셀 좌표)
"""
import asyncio

from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

_engine = None


def engine():
    global _engine
    if _engine is None:
        for tag in ("en-US", "en-GB", "ko"):
            if OcrEngine.is_language_supported(Language(tag)):
                _engine = OcrEngine.try_create_from_language(Language(tag))
                break
        else:
            _engine = OcrEngine.try_create_from_user_profile_languages()
    return _engine


def _to_bitmap(img):
    img = img.convert("RGBA")
    w, h = img.size
    r, g, b, a = img.split()
    data = __import__("PIL.Image", fromlist=["Image"]).merge("RGBA", (b, g, r, a)).tobytes()  # BGRA
    writer = DataWriter()
    writer.write_bytes(data)
    buf = writer.detach_buffer()
    return SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED)


async def _recognize(img):
    return await engine().recognize_async(_to_bitmap(img))


def read_lines(img):
    """OCR 엔진은 한 변 최대 크기가 있어서(대략 2600px) 크면 줄여서 읽고 좌표를 되돌린다."""
    limit = OcrEngine.max_image_dimension
    scale = 1.0
    if max(img.size) > limit:
        scale = limit / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    res = asyncio.run(_recognize(img))
    out = []
    for line in res.lines:
        rects = [wd.bounding_rect for wd in line.words]
        if not rects:
            continue
        x0 = min(r.x for r in rects)
        y0 = min(r.y for r in rects)
        x1 = max(r.x + r.width for r in rects)
        y1 = max(r.y + r.height for r in rects)
        out.append({"text": line.text, "x": x0 / scale, "y": y0 / scale, "w": (x1 - x0) / scale, "h": (y1 - y0) / scale})
    return out


if __name__ == "__main__":
    import sys
    from PIL import Image
    im = Image.open(sys.argv[1])
    import time
    t = time.time()
    lines = read_lines(im)
    print(f"{len(lines)}줄 · {time.time() - t:.2f}초 · 엔진 {engine().recognizer_language.language_tag}")
    for ln in lines:
        print(f"  ({ln['x']:.0f},{ln['y']:.0f},{ln['w']:.0f}x{ln['h']:.0f}) {ln['text']}")

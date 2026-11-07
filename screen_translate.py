"""화면 번역: 단축키(Ctrl+Shift+T)를 누르면 지금 보고 있는 창의 영어 글자를 읽어서
같은 자리에 한국어를 덮어씌운다. 다시 누르거나 화면이 바뀌면 걷어 낸다.

  캡처(맨 앞 창) → 윈도우 OCR(screen_ocr) → 줄을 문단으로 묶기 → EXAONE 한 번에 번역 → 덮개 창에 그리기
  덮개 창: 항상 맨 위, 클릭은 아래로 통과, 화면 캡처에서 빠짐(WDA_EXCLUDEFROMCAPTURE) — 자기 한국어를 다시 읽지 않게.

앱(app.py --screen)이 켤 때 같이 띄운다. 앱이 꺼지면 같이 꺼진다.
"""
import ctypes
import ctypes.wintypes as wt
import json
import queue
import re
import statistics
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request

from PIL import ImageChops, ImageGrab, ImageStat

BASE = "http://127.0.0.1:8765"
OLLAMA = "http://127.0.0.1:11434/api/chat"
TITLE = "화면 번역 덮개"
u32 = ctypes.windll.user32

SYSTEM = """너는 Zendesk(고객 상담 소프트웨어) 교육 슬라이드를 한국어로 옮기는 번역가야. 읽는 사람은 한국 콜센터·UX 실무자.
입력은 화면 글자를 OCR 로 읽은 것이라 오타가 섞여 있다(l↔I, 0↔O, 1↔l, ? 는 못 읽은 글자). 뜻으로 바로잡는다.
규칙:
- 번호마다 한 줄씩, 같은 번호로 한국어 번역만 출력한다. 예) 1. 번역문
- '✓'로 시작하는 줄만 번역도 '✓ '로 시작한다. 없는 숫자(24/7 등)를 지어내지 않는다.
- 짧고 슬라이드답게(명사형·개조식). 원문보다 길게 늘이지 않는다.
- 이 용어는 영어 그대로: Zendesk, Copilot, AI agent, IVR, QA, ACW, CSAT, SLA, ticket, macro, trigger, dashboard.
- 사람 이름·제품 이름·URL·숫자만 있는 줄은 그대로 둔다.
- 설명·메모 없이 번호 줄만."""


# ---------- 화면 ----------
def foreground_rect(skip):
    """맨 앞 창(우리 창 빼고)의 화면 좌표. 최대화면 화면 전체."""
    h = u32.GetForegroundWindow()
    if not h or h in skip:
        return None, None
    r = wt.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    if r.right - r.left < 200 or r.bottom - r.top < 150:
        return None, None
    return h, (max(r.left, 0), max(r.top, 0), r.right, r.bottom)


def window_title(h):
    n = u32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(h, b, n + 1)
    return b.value


# ---------- 줄 → 문단 ----------
_BULLET = re.compile(r"^(?:[✓✔•·▪►>\-–—*]|7|v|V)\s+(?=[A-Z0-9])")
_JUNK = re.compile(r"https?://|www\.|\.com/|docs\.goog|^[^A-Za-z]*$")


def clean_ocr(t):
    """OCR 이 영어를 읽으며 흔히 틀리는 것 바로잡기 (한국어 엔진·영어 엔진 둘 다)."""
    t = t.strip()
    t = re.sub(r"^(?:7|v|V|J|/)\s+(?=[A-Z0-9])", "✓ ", t)          # 체크표시 ✓ 를 7 로 읽음
    t = re.sub(r"(?<=[A-Za-z0-9/ ])시(?=\s|&|$)", "AI", t)            # AI 를 '시'로 읽음 (한국어 엔진)
    t = re.sub(r"\bA[1l]\b", "AI", t)                                  # AI 를 A1·Al 로 읽음 (영어 엔진, 11/08)
    t = re.sub(r"\b2417\b", "24/7", t)
    t = re.sub(r"\b(\d{2,3})0?7\.", r"\1%", t)  # 1007. → 100%  (% 를 7 로 읽음)
    t = t.replace("『", "r").replace("』", "l")
    t = re.sub(r"[가-힣]", "?", t)                                      # 남은 한글 한 글자 = 못 읽은 글자
    return t


def keep_line(ln):
    t = ln["text"].strip()
    hangul = len(re.findall(r"[가-힣]", t))
    if hangul > max(2, sum(c.isalpha() for c in t) * 0.3):   # 한국어 줄 (우리 자막 등)
        return False
    if _JUNK.search(t):
        return False
    return sum(c.isalpha() for c in t) >= 3


def group_blocks(lines, top_skip=0):
    """같은 왼쪽 선·가까운 줄 간격·비슷한 글자 높이면 한 문단. 체크표시로 시작하면 새 문단.
    top_skip: 창 위쪽에서 건너뛸 높이 (브라우저 탭·주소창)"""
    lines = [dict(ln, text=clean_ocr(ln["text"])) for ln in lines if keep_line(ln) and ln["y"] >= top_skip]
    lines = sorted(lines, key=lambda l: (round(l["y"] / 8), l["x"]))
    blocks = []
    for ln in lines:
        placed = False
        for b in reversed(blocks[-6:]):
            last = b["lines"][-1]
            h = statistics.median(l["h"] for l in b["lines"])
            same_col = abs(ln["x"] - b["lines"][0]["x"]) < max(h * 1.6, 30) or (ln["x"] > b["x"] and ln["x"] < b["x"] + b["w"] * 0.3)
            gap = ln["y"] - (last["y"] + last["h"])
            if same_col and -h * 0.5 < gap < h * 0.95 and 0.7 < ln["h"] / h < 1.4 and not _BULLET.match(ln["text"]):
                b["lines"].append(ln)
                placed = True
                break
        if not placed:
            blocks.append({"lines": [ln]})
        b = blocks[-1] if not placed else b
        xs = [l["x"] for l in b["lines"]]
        ys = [l["y"] for l in b["lines"]]
        b["x"], b["y"] = min(xs), min(ys)
        b["w"] = max(l["x"] + l["w"] for l in b["lines"]) - b["x"]
        b["h"] = max(l["y"] + l["h"] for l in b["lines"]) - b["y"]
    for b in blocks:
        b["text"] = " ".join(l["text"].strip() for l in b["lines"])
        b["line_h"] = statistics.median(l["h"] for l in b["lines"])
    return blocks


# ---------- 번역 ----------
_cache = {}


def _ask(chunk, model):
    """번호 붙여 한 번에 보내고 {번호: 번역} 으로 받는다. 같은 번호가 또 나오면(모델이 되풀이) 처음 것만."""
    user = "\n".join(f"{n}. {t}" for n, t in enumerate(chunk, 1))
    body = json.dumps({"model": model, "stream": False, "keep_alive": "30m",
                       "options": {"temperature": 0.1, "num_predict": 60 * len(chunk) + 100},
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.load(r)["message"]["content"]
    got = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
        if m and 1 <= int(m.group(1)) <= len(chunk):
            got.setdefault(int(m.group(1)), m.group(2).strip())
    if len(chunk) == 1 and 1 not in got:  # 하나만 보냈는데 번호 없이 답함 → 첫 줄을 번역으로
        first = next((l.strip() for l in out.splitlines() if l.strip()), "")
        if first:
            got[1] = first
    return got


def translate_blocks(texts, model):
    """11/08: EXAONE 이 여러 블록을 받으면 2번을 1번 아래 들여쓰기로 붙이고 번호를 빼먹어서 빈칸이 됐다.
    → 짝이 안 맞은 블록만 하나씩 다시 묻는다. 빈 번역은 캐시에 안 넣는다(다음에 누르면 다시 시도)."""
    todo = [t for t in dict.fromkeys(texts) if t not in _cache]
    for i in range(0, len(todo), 14):  # 너무 길면 나눠서
        chunk = todo[i:i + 14]
        got = _ask(chunk, model)
        for n, t in enumerate(chunk, 1):
            ko = got.get(n, "")
            if not ko and len(chunk) > 1:
                ko = _ask([t], model).get(1, "")
            if ko:
                _cache[t] = ko
    return [_cache.get(t, "") for t in texts]


# ---------- 색 ----------
def box_colors(img, x, y, w, h):
    """문단 칸의 배경색(테두리 픽셀 중앙값)과 글자색(배경과 가장 다른 픽셀들 평균)."""
    pad = 3
    crop = img.crop((max(0, x - pad), max(0, y - pad), min(img.width, x + w + pad), min(img.height, y + h + pad)))
    small = crop.convert("RGB").resize((max(1, crop.width // 2), max(1, crop.height // 2)))
    px = list(small.getdata())
    W, H = small.size
    border = [px[i] for i in range(len(px)) if i < W or i >= len(px) - W or i % W in (0, W - 1)]
    bg = tuple(int(statistics.median(c[k] for c in border)) for k in range(3))
    dist = sorted(px, key=lambda c: -sum(abs(c[k] - bg[k]) for k in range(3)))
    top = dist[:max(1, len(dist) // 12)]
    fg = tuple(int(sum(c[k] for c in top) / len(top)) for k in range(3))
    if sum(abs(fg[k] - bg[k]) for k in range(3)) < 120:  # 글자색이 흐리면 대비 강제
        fg = (17, 19, 24) if sum(bg) > 380 else (255, 255, 255)
    return "#%02x%02x%02x" % bg, "#%02x%02x%02x" % fg


# ---------- 덮개 창 ----------
class ScreenTranslator:
    KEY = "#ff00fe"  # 뚫리는 색

    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 화면 픽셀 그대로 (캡처 좌표 = 창 좌표)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.withdraw()
        self.win = tk.Toplevel(self.root)
        self.win.title(TITLE)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=self.KEY)
        self.win.attributes("-transparentcolor", self.KEY)
        self.cv = tk.Canvas(self.win, bg=self.KEY, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.win.withdraw()
        self.fam = next((f for f in ("Pretendard", "Pretendard SemiBold", "Malgun Gothic") if f in tkfont.families()),
                        "Malgun Gothic")
        self.shown = False
        self.busy = False
        self.rect = None
        self.base = None          # 덮을 때 찍어 둔 화면 (바뀌면 걷기)
        self.model = "exaone3.5:7.8b"
        self.events = queue.Queue()
        self.last_req = None
        self.last_ok = time.time()
        self.last_fore = None
        self._styled = False
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        self.root.after(300, self._tick)

    def _own(self):
        out = set()
        for t in (TITLE, "한국어 자막", "한국어 자막 배경", "동영상 번역기"):
            h = u32.FindWindowW(None, t)
            if h:
                out.add(h)
        return out

    def _style(self):
        """클릭 통과 + 캡처에서 빼기. 창이 실제로 만들어진 뒤 한 번."""
        if self._styled:
            return
        self.win.update_idletasks()
        h = u32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        ex = u32.GetWindowLongW(h, -20)
        u32.SetWindowLongW(h, -20, ex | 0x20 | 0x80000 | 0x80 | 0x08000000)  # TRANSPARENT|LAYERED|TOOLWINDOW|NOACTIVATE
        u32.SetWindowDisplayAffinity(h, 0x11)  # WDA_EXCLUDEFROMCAPTURE
        self._styled = True

    # ---- 단축키 (자기 메시지 루프 스레드) ----
    def _hotkey_loop(self):
        MOD = 0x0002 | 0x0004 | 0x4000  # CTRL | SHIFT | NOREPEAT
        if not u32.RegisterHotKey(None, 1, MOD, ord("T")):
            self.events.put(("toast", "Ctrl+Shift+T 를 다른 프로그램이 쓰고 있어요"))
            return
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                self.events.put(("toggle", None))

    # ---- 주기적으로 ----
    def _tick(self):
        try:
            while True:
                kind, arg = self.events.get_nowait()
                if kind == "toggle":
                    self.toggle()
                elif kind == "draw":
                    self._draw(*arg)
                elif kind == "toast":
                    self._toast(arg)
        except queue.Empty:
            pass
        # 앱 상태: 모델 이름, 앱 창의 '화면 번역' 버튼
        try:
            with urllib.request.urlopen(BASE + "/api/state?overlay=1", timeout=1) as r:
                s = json.load(r)
            self.last_ok = time.time()
            self.model = s["settings"].get("local_model", self.model)
            req = s.get("screen_req", 0)
            if self.last_req is None:
                self.last_req = req
            elif req != self.last_req:
                self.last_req = req
                self.toggle(use_last=True)
        except Exception:
            if time.time() - self.last_ok > 15:
                self.root.destroy()
                return
        # 앱 창 버튼을 누르면 앞 창이 앱이 되니까, 그 전에 보던 창을 기억해 둔다
        h, rect = foreground_rect(self._own())
        if h:
            self.last_fore = (h, rect)
        # 덮개가 떠 있는데 아래 화면이 바뀌면 걷는다 (슬라이드 넘김·스크롤)
        if self.shown and self.base is not None and not self.busy:
            try:
                now = ImageGrab.grab(self.rect, all_screens=True).convert("L").resize(self.base.size)
                diff = ImageStat.Stat(ImageChops.difference(now, self.base)).mean[0]
                if diff > 6:
                    self.hide()
            except Exception:
                pass
        self.root.after(700, self._tick)

    # ---- 켜고 끄기 ----
    def toggle(self, use_last=False):
        if self.busy:
            return
        if self.shown:
            self.hide()
            return
        h, rect = foreground_rect(self._own())
        if (not h or use_last) and self.last_fore:
            h, rect = self.last_fore
        if not h:
            self._toast("번역할 창을 앞에 두고 다시 눌러 주세요")
            return
        self.busy = True
        self.rect = rect
        self._toast("화면 읽는 중…")
        threading.Thread(target=self._work, args=(rect,), daemon=True).start()

    def _chrome_height(self, rect):
        """브라우저 창이면 탭·주소창 줄(위쪽 약 110px, 화면 배율 반영)은 번역하지 않는다. 전체 화면 영상이면 0."""
        h = self.last_fore[0] if self.last_fore else None
        cls = ctypes.create_unicode_buffer(64)
        if h:
            u32.GetClassNameW(h, cls, 64)
        sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
        full = rect[0] <= 0 and rect[1] <= 0 and rect[2] >= sw and rect[3] >= sh
        if cls.value.startswith("Chrome_WidgetWin") and not full:
            return int(110 * u32.GetDpiForWindow(h) / 96) if h else 110
        return 0

    def hide(self):
        self.cv.delete("all")
        self.win.withdraw()
        self.shown = False
        self.base = None

    def _work(self, rect):
        try:
            from screen_ocr import read_lines
            t0 = time.time()
            img = ImageGrab.grab(rect, all_screens=True)
            blocks = group_blocks(read_lines(img), top_skip=self._chrome_height(rect))
            if not blocks:
                self.events.put(("toast", "영어 글자를 못 찾았어요"))
                self.busy = False
                return
            self.events.put(("toast", f"번역 중… ({len(blocks)}칸)"))
            kos = translate_blocks([b["text"] for b in blocks], self.model)
            for b, ko in zip(blocks, kos):
                b["ko"] = ko
                b["bg"], b["fg"] = box_colors(img, int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"]))
            base = img.convert("L").resize((max(1, img.width // 8), max(1, img.height // 8)))
            self.events.put(("draw", (rect, blocks, base, time.time() - t0)))
        except Exception as e:
            self.events.put(("toast", f"화면 번역 실패: {type(e).__name__}"))
            self.busy = False

    def _draw(self, rect, blocks, base, secs):
        x0, y0, x1, y1 = rect
        self.win.geometry(f"{x1 - x0}x{y1 - y0}+{x0}+{y0}")
        self.cv.delete("all")
        self.win.deiconify()
        self._style()
        hs = sorted(b["line_h"] for b in blocks)
        body_h = hs[len(hs) // 2]
        for b in blocks:
            if not b.get("ko"):
                continue
            x, y, w, h = int(b["x"]) - 3, int(b["y"]) - 2, int(b["w"]) + 6, int(b["h"]) + 4
            self.cv.create_rectangle(x, y, x + w, y + h, fill=b["bg"], outline=b["bg"])
            bold = b["line_h"] > body_h * 1.25
            size = int(b["line_h"] * 0.82)
            while size >= 9:  # 칸에 들어갈 때까지 줄인다
                f = tkfont.Font(family=self.fam, size=-size, weight="bold" if bold else "normal")
                item = self.cv.create_text(x + 3, y + 1, text=b["ko"], font=f, fill=b["fg"], width=w + int(w * 0.12), anchor="nw")
                bx = self.cv.bbox(item)
                if bx[3] - bx[1] <= h + 4 or size == 9:
                    if bx[2] > x + w:  # 오른쪽으로 넘치면 배경을 조금 늘림
                        self.cv.create_rectangle(x + w, y, bx[2] + 3, y + h, fill=b["bg"], outline=b["bg"])
                        self.cv.tag_raise(item)
                    if bx[3] > y + h:
                        self.cv.create_rectangle(x, y + h, max(x + w, bx[2] + 3), bx[3] + 2, fill=b["bg"], outline=b["bg"])
                        self.cv.tag_raise(item)
                    break
                self.cv.delete(item)
                size -= 1
        self.shown = True
        self.busy = False
        self.base = base
        self._toast(f"번역 덮음 · {secs:.1f}초 · 다시 Ctrl+Shift+T 면 원문", 2500)

    def _toast(self, text, ms=None):
        """덮개 위쪽 가운데에 작은 알림."""
        if not hasattr(self, "_tw"):
            self._tw = tk.Toplevel(self.root)
            self._tw.overrideredirect(True)
            self._tw.attributes("-topmost", True)
            self._tw.attributes("-alpha", 0.92)
            self._tl = tk.Label(self._tw, bg="#111318", fg="#ffffff", padx=14, pady=7,
                                font=(self.fam, 11, "bold"))
            self._tl.pack()
            self._tw.update_idletasks()
            th = u32.GetParent(self._tw.winfo_id()) or self._tw.winfo_id()
            u32.SetWindowDisplayAffinity(th, 0x11)
            u32.SetWindowLongW(th, -20, u32.GetWindowLongW(th, -20) | 0x20 | 0x80000 | 0x80 | 0x08000000)
        self._tl.configure(text=text)
        self._tw.update_idletasks()
        r = self.rect or (0, 0, self.root.winfo_screenwidth(), 200)
        x = (r[0] + r[2]) // 2 - self._tw.winfo_reqwidth() // 2
        self._tw.geometry(f"+{x}+{r[1] + 90}")
        self._tw.deiconify()
        self._tw.lift()
        if hasattr(self, "_tjob"):
            self.root.after_cancel(self._tjob)
        self._tjob = self.root.after(ms or (60000 if self.busy else 2500), self._tw.withdraw)

    def run(self):
        self.root.mainloop()


def main():
    if u32.FindWindowW(None, TITLE):
        return
    ScreenTranslator().run()


if __name__ == "__main__":
    main()

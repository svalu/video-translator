"""한국어 자막 줄: 영상 위에 항상 떠 있는 띠. 앱(app.py)이 켜고, 앱이 꺼지면 같이 꺼진다.

앱의 /api/state 를 0.3초마다 읽어서, 지금 읽는 한국어(크게)와 영어 원문(작게)을 보여 준다.
글자 크기·배경색·불투명도·영어 보이기·숨기기는 앱 창의 '자막 줄' 카드에서 바꾸고, 여기엔 바로 반영된다.
  끌기 = 옮기기 (자리는 .overlay.json 에 기억) · 오른쪽 클릭 = 빠른 메뉴

배경만 반투명하고 글자는 또렷하게: 창을 두 겹으로 겹친다.
  아래 창(배경판) = 배경색, 불투명도 슬라이더 값만큼 투명
  위 창(글자판)   = 불투명. 바탕을 '열쇠 색'으로 칠하고 그 색을 완전히 뚫리게 해서 글자만 남긴다.
  열쇠 색은 배경색과 거의 같은 색으로 해서 글자 가장자리(안티에일리어싱)가 티 나지 않게 한다.
실행(따로 시험할 때): .venv\\Scripts\\pythonw.exe subtitle_overlay.py
"""
import json
import os
import re
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request

from paths import DATA as HERE
PREF = os.path.join(HERE, ".overlay.json")
BASE = "http://127.0.0.1:8765"
API = BASE + "/api/state?overlay=1"
TITLE = "한국어 자막"


def load_pref():
    try:
        with open(PREF, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ink_for(hex_):
    """배경이 밝으면 검은 글자, 어두우면 흰 글자. (본문, 영어 원문, 흐린 글자)"""
    n = int(hex_[1:], 16)
    r, g, b = n >> 16, (n >> 8) & 255, n & 255
    if 0.299 * r + 0.587 * g + 0.114 * b > 150:
        return "#111318", "#4a4f5c", "#5d6370"
    return "#ffffff", "#c2c6d0", "#c9cdd6"  # 흐린 글자도 반투명 배경 위에서 읽히게 밝게


def outline_for(ink):
    """글자 테두리: 흰 글자엔 검은 테두리, 검은 글자엔 흰 테두리. 뒤가 밝든 어둡든 읽히게."""
    return "#000000" if ink == "#ffffff" else "#ffffff"


def key_for(hex_):
    """배경색과 1 차이 나는 열쇠 색 (이 색은 화면에서 뚫린다)."""
    n = int(hex_[1:], 16)
    b = n & 255
    b = b + 1 if b < 255 else b - 1
    return f"#{(n & 0xFFFF00) | b:06x}"


class Overlay:
    def __init__(self):
        self.pref = load_pref()
        # 아래 창: 배경판
        bg = self.bgwin = tk.Tk()
        bg.title(TITLE + " 배경")
        bg.overrideredirect(True)
        bg.attributes("-topmost", True)
        # 위 창: 글자판
        top = self.top = tk.Toplevel(bg)
        top.title(TITLE)
        top.overrideredirect(True)
        top.attributes("-topmost", True)

        self.fam = next((f for f in ("Pretendard", "Pretendard Medium", "Malgun Gothic") if f in tkfont.families()),
                        "Malgun Gothic")
        self.f_ko = tkfont.Font(family=self.fam, size=22, weight="bold")
        self.f_en = tkfont.Font(family=self.fam, size=11)
        sw, sh = bg.winfo_screenwidth(), bg.winfo_screenheight()
        self.w = int(sw * 0.62)
        self.x = self.pref.get("x", (sw - self.w) // 2)
        self.y = self.pref.get("y", sh - 230)
        # 글자는 캔버스에 직접 그린다: 테두리(8방향으로 조금씩 밀어 그린 그림자) 위에 본문
        self.cv = tk.Canvas(top, width=self.w, height=60, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.ko_text, self.en_text = "자막을 기다리는 중…", ""

        # 글자판의 뚫린 곳을 누르면 배경판이 눌린다 → 둘 다 끌 수 있게
        for wdg in (bg, top, self.cv):
            wdg.bind("<ButtonPress-1>", self._press)
            wdg.bind("<B1-Motion>", self._drag)
            wdg.bind("<ButtonRelease-1>", lambda e: self._save())
            wdg.bind("<Button-3>", self._menu)

        self.cfg = {}          # 지금 적용된 모양 설정
        self.shown = True
        self.last_ok = time.time()
        self.last_text = ""
        self.last_change = time.time()
        self._size_key = None
        self.apply({"overlay_size": 22, "overlay_bg": "#0f1014", "overlay_alpha": 88, "overlay_en": True})
        bg.after(300, self.poll)

    # ---- 두 창 맞추기 ----
    def _sync(self, force=False):
        """글자판 크기에 배경판을 맞추고, 글자판을 위로."""
        self.top.update_idletasks()
        w, h = max(self.w, self.top.winfo_reqwidth()), self.top.winfo_reqheight()
        key = (w, h, self.x, self.y)
        # 처음 뜰 때는 위치 지정이 무시될 수 있어서, 실제 자리가 다르면 다시 맞춘다
        moved = (self.top.winfo_x(), self.top.winfo_y()) != (self.x, self.y)
        if force or moved or key != self._size_key:
            self._size_key = key
            self.top.geometry(f"{w}x{h}+{self.x}+{self.y}")
            self.top.update_idletasks()
        self._glue_bg()

    def _glue_bg(self):
        """배경판을 글자판과 똑같은 자리·크기로, 바로 아래 겹에 붙인다.
        (tk geometry 로 배경판(루트)을 옮기면 가끔 무시돼서 윈도우 API 로 직접 맞춘다)"""
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        top_h = u.GetParent(self.top.winfo_id()) or self.top.winfo_id()
        bg_h = u.GetParent(self.bgwin.winfo_id()) or self.bgwin.winfo_id()
        rc = wt.RECT()
        u.GetWindowRect(top_h, ctypes.byref(rc))
        # hWndInsertAfter=글자판 → 배경판은 글자판 바로 아래. 0x0010 = SWP_NOACTIVATE
        w, h = rc.right - rc.left, rc.bottom - rc.top
        u.SetWindowPos(bg_h, top_h, rc.left, rc.top, w, h, 0x0010)
        # 둥근 모서리: 두 창 모두 둥근 사각형 모양으로 자른다 (배경판 0% 일 때도 글자판 모양이 같게)
        scale = w / max(1, self.top.winfo_width())
        r = int(int(self.cfg.get("overlay_radius", 16)) * scale)
        shape = (w, h, r)
        if shape != getattr(self, "_shape", None):
            self._shape = shape
            g = ctypes.windll.gdi32
            for hwnd in (bg_h, top_h):
                rgn = g.CreateRoundRectRgn(0, 0, w + 1, h + 1, 2 * r, 2 * r) if r else 0
                u.SetWindowRgn(hwnd, rgn, True)  # 윈도우가 rgn 을 가져가므로 지우지 않는다

    # ---- 글자 그리기 ----
    def _text(self, x, y, txt, font, fill, width, ring):
        """테두리 있는 글자. ring = 테두리 두께(px). 반환: 아래쪽 y"""
        if ring:
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if (dx or dy) and dx * dx + dy * dy <= ring * ring + 1:
                        self.cv.create_text(x + dx, y + dy, text=txt, font=font, fill=self.outline,
                                            width=width, justify="center", anchor="n")
        item = self.cv.create_text(x, y, text=txt, font=font, fill=fill, width=width, justify="center", anchor="n")
        return self.cv.bbox(item)[3]

    def _draw(self):
        cv = self.cv
        cv.delete("all")
        w = self.w
        size = int(self.cfg.get("overlay_size", 22))
        ring = max(1, round(size / 14))
        y = self._text(w // 2, 12, self.ko_text or "…", self.f_ko, self.ink, w - 40, ring)
        if self.cfg.get("overlay_en", True) and self.en_text:
            y = self._text(w // 2, y + 4, self.en_text, self.f_en, self.ink_en, w - 40, 1)
        cv.configure(width=w, height=y + 12)
        self._sync()

    # ---- 모양 ----
    def apply(self, st):
        new = {k: st[k] for k in ("overlay_size", "overlay_bg", "overlay_alpha", "overlay_en", "overlay_radius") if k in st}
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(new.get("overlay_bg", ""))):
            new["overlay_bg"] = "#0f1014"
        if new == self.cfg:
            return
        self.cfg = new
        bgc = new["overlay_bg"].lower()
        key = key_for(bgc)
        self.ink, self.ink_en, _ = ink_for(bgc)
        self.outline = outline_for(self.ink)
        size = int(new["overlay_size"])
        self.f_ko.configure(size=size)
        self.f_en.configure(size=max(10, size // 2))
        # 배경판: 색 + 불투명도
        self.bgwin.configure(bg=bgc)
        self.bgwin.attributes("-alpha", max(0.0, min(1.0, int(new["overlay_alpha"]) / 100)))
        # 글자판: 열쇠 색 바탕을 뚫고, 글자는 불투명
        for wdg in (self.top, self.cv):
            wdg.configure(bg=key)
        self.top.attributes("-transparentcolor", key)
        self._draw()
        self._sync(force=True)

    # ---- 끌기·메뉴 ----
    def _press(self, e):
        self._dx, self._dy = e.x_root - self.x, e.y_root - self.y

    def _drag(self, e):
        self.x, self.y = e.x_root - self._dx, e.y_root - self._dy
        self._sync()

    def _post(self, patch):
        try:
            req = urllib.request.Request(BASE + "/api/settings", json.dumps(patch).encode(),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:
            pass

    def _menu(self, e):
        size = int(self.cfg.get("overlay_size", 22))
        m = tk.Menu(self.top, tearoff=0)
        m.add_command(label="글자 크게", command=lambda: self._post({"overlay_size": min(44, size + 2)}))
        m.add_command(label="글자 작게", command=lambda: self._post({"overlay_size": max(14, size - 2)}))
        m.add_command(label="영어 원문 보이기/숨기기",
                      command=lambda: self._post({"overlay_en": not self.cfg.get("overlay_en", True)}))
        m.add_separator()
        m.add_command(label="자막 줄 숨기기 (앱 창에서 다시 켜기)", command=lambda: self._post({"overlay_on": False}))
        m.tk_popup(e.x_root, e.y_root)

    def _save(self):
        try:
            with open(PREF, "w", encoding="utf-8") as f:
                json.dump({"x": self.x, "y": self.y}, f)
        except OSError:
            pass

    # ---- 글자 받아 오기 ----
    def poll(self):
        try:
            with urllib.request.urlopen(API, timeout=1) as r:
                s = json.load(r)
            self.last_ok = time.time()
            st = s.get("settings", {})
            self.apply(st)
            want = bool(st.get("overlay_on", True))
            if want != self.shown:
                self.shown = want
                for wnd in (self.bgwin, self.top):
                    if want:
                        wnd.deiconify()
                        wnd.attributes("-topmost", True)
                    else:
                        wnd.withdraw()
                if want:
                    self._sync(force=True)
            ko = s.get("speaking") or (s["recent"][0]["ko"] if s.get("recent") else "")
            en = s["recent"][0]["en"] if s.get("speaking") and s.get("recent") else ""
            if not s.get("running"):
                ko, en = "읽기를 멈췄어", ""
            if ko != self.last_text:  # (11/06: 한동안 새 문장이 없을 때 흐리게 하던 건 뺐다 — 흐려 보인다고 해서)
                self.last_text, self.last_change = ko, time.time()
                self.ko_text, self.en_text = ko, en
                self._draw()
            if self.shown:
                self._sync()
        except Exception:
            if time.time() - self.last_ok > 15:  # 앱이 꺼졌으면 같이 끈다
                self.bgwin.destroy()
                return
        self.bgwin.after(300, self.poll)

    def run(self):
        self.bgwin.mainloop()


def main():
    import ctypes
    if ctypes.windll.user32.FindWindowW(None, TITLE):  # 이미 떠 있으면 또 띄우지 않기
        return
    Overlay().run()


if __name__ == "__main__":
    main()

"""동영상 번역기 앱 (11/06 이름 바꿈, 예전 이름: 자막 읽어주기).

파이썬이 PC 안에서만(127.0.0.1) 화면(ui/index.html)과 API를 내주고,
WebView2 창(pywebview)으로 띄운다. 창을 닫으면 앱이 끝난다. pywebview 가 없으면 예전처럼 Edge '앱 모드'.

실행: .venv\\Scripts\\pythonw.exe app.py            (창까지 띄움)
      .venv\\Scripts\\python.exe app.py --no-window  (서버만, 점검용)
      app.py --overlay                              (한국어 자막 줄만 — 앱이 알아서 부른다)
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from paths import RES
from reader_core import HERE, Reader
from setup_check import Setup
from version import VERSION

PORT = 8765
UI = os.path.join(RES, "ui", "index.html")
reader = Reader()
setup = Setup(lambda: reader.settings["local_model"])
screen_req = [0]  # 앱 창의 '화면 번역' 버튼을 누른 횟수 — 화면 번역 프로세스가 보고 움직인다
last_ping = [time.time()]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 콘솔 조용히
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = open(UI, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/setup":
            self._json(setup.check())
        elif self.path.startswith("/api/state"):
            if "overlay" not in self.path:  # 자막 줄이 부르는 건 '앱 창 살아 있음'으로 치지 않는다
                last_ping[0] = time.time()
            self._json(state())
        else:
            self._json({"error": "없는 주소"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if self.path == "/api/start":
            reader.start()
            open_overlay()
        elif self.path == "/api/stop":
            reader.stop()
        elif self.path == "/api/settings":
            reader.update(data)
        elif self.path == "/api/test-voice":
            reader.test_voice()
        elif self.path == "/api/setup/start":
            setup.start_ollama()
            return self._json(setup.check())
        elif self.path == "/api/setup/install":
            setup.install_ollama()
            return self._json(setup.check())
        elif self.path == "/api/setup/pull":
            setup.pull_model()
            return self._json(setup.check())
        elif self.path == "/api/setup/cuda":
            setup.get_cuda()
            return self._json(setup.check())
        elif self.path == "/api/screen":
            screen_req[0] += 1
        elif self.path == "/api/open-logs":
            os.startfile(os.path.join(HERE, "logs"))
        else:
            return self._json({"error": "없는 주소"}, 404)
        self._json(state())


def state():
    return {**reader.state(), "screen_req": screen_req[0], "version": VERSION}


TITLE = "동영상 번역기"
ICON = os.path.join(RES, "ui", "brand", "app.ico")  # 창 제목줄·작업 표시줄 = 앱 아이콘 (11/08: 메이커 마크는 '만든 사람' 자리로)
APP_ID = "Heerang.VideoTranslator"


def self_command(*args):
    """나 자신을 다시 부르는 명령. exe 로 묶였을 땐 exe, 아니면 pythonw app.py"""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return [pyw if os.path.exists(pyw) else sys.executable, os.path.abspath(__file__), *args]


def open_screen():
    """화면 번역(Ctrl+Shift+T) 담당. 앱과 같이 켜지고 같이 꺼진다."""
    subprocess.Popen(self_command("--screen"), cwd=HERE)


def open_overlay():
    """영상 위에 항상 떠 있는 한국어 자막 줄. 이미 떠 있으면 새로 뜬 쪽이 알아서 그냥 끝난다."""
    subprocess.Popen(self_command("--overlay"), cwd=HERE)


def find_edge():
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def already_running():
    """이미 앱이 떠 있으면 True. (윈도우는 같은 포트를 두 번 열어도 막지 않아서 앱 두 개가 같이 읽는 일이 생겼다)"""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/state?overlay=1", timeout=1).read()
        return True
    except Exception:
        return False


def set_window_icon():
    """WebView2 창이 뜨면 제목줄·작업 표시줄 아이콘을 메이커 마크로 (pythonw 아이콘 대신)."""
    import ctypes
    u = ctypes.windll.user32
    u.LoadImageW.restype = ctypes.c_void_p
    u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
    for _ in range(100):
        hwnd = u.FindWindowW(None, TITLE)
        if hwnd:
            for which, size in ((1, 256), (0, 32)):  # ICON_BIG, ICON_SMALL
                h = u.LoadImageW(None, ICON, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                if h:
                    u.SendMessageW(hwnd, 0x0080, which, h)  # WM_SETICON
            return
        time.sleep(0.1)


def focus_existing():
    """이미 떠 있는 창을 앞으로."""
    import ctypes
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, TITLE)
    if hwnd:
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
        u.SetForegroundWindow(hwnd)
        return True
    return False


def run_webview(url):
    """WebView2 창을 띄우고 닫힐 때까지 기다린다. pywebview 가 없으면 False."""
    try:
        import webview
    except ImportError:
        return False
    webview.create_window(TITLE, url, width=480, height=880, min_size=(380, 560), background_color="#e9ebef")
    webview.start(set_window_icon, storage_path=os.path.join(HERE, ".webview"))
    return True


def open_window(url):
    edge = find_edge()
    if edge:
        profile = os.path.join(HERE, ".app-window")  # 앱 창 전용 프로필 (평소 Edge 와 섞이지 않게)
        subprocess.Popen([edge, f"--app={url}", "--window-size=480,820", f"--user-data-dir={profile}",
                          "--no-first-run", "--no-default-browser-check"])
    else:
        os.startfile(url)


def main():
    if "--overlay" in sys.argv:
        from subtitle_overlay import main as overlay_main
        return overlay_main()
    if "--screen" in sys.argv:
        from screen_translate import main as screen_main
        return screen_main()
    try:  # 작업 표시줄에서 python 이 아니라 '동영상 번역기'로 따로 묶이게
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    url = f"http://127.0.0.1:{PORT}/"
    if already_running():  # 바로가기를 또 누르면 떠 있는 창을 앞으로
        if "--no-window" not in sys.argv and not focus_existing():
            open_window(url)
        return
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("동영상 번역기:", url)
    open_screen()

    if "--no-window" not in sys.argv and run_webview(url):
        pass  # WebView2 창이 닫혔다 → 끝
    else:
        if "--no-window" not in sys.argv:
            open_window(url)
        _wait_until_window_gone()
    reader.stop()
    server.shutdown()
    print("끝")


def _wait_until_window_gone():
    """(Edge 창 방식) 화면이 10초 넘게 안 부르면 창이 닫힌 걸로 보고 끝낸다."""
    try:
        while True:
            time.sleep(1)
            if time.time() - last_ping[0] > 10:
                break
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

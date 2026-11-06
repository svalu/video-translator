"""LiveCaptions Translator 창 안의 글자 칸을 훑어보기 (읽기만 함).
실행: .venv\\Scripts\\python.exe probe_translator_window.py [초]  → 그 시간 동안 1초마다 글자가 있는 칸만 출력
"""
import sys
import time
import uiautomation as auto

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 0
win = auto.WindowControl(searchDepth=1, Name="LiveCaptions Translator")
if not win.Exists(3, 0.5):
    print("LiveCaptions Translator 창을 못 찾았어.")
    raise SystemExit(1)

SKIP = {"LiveCaptions Translator", "Caption", "Setting", "History", "Info"}


def snapshot():
    rows = []
    for c, depth in auto.WalkControl(win, maxDepth=14):
        if c.ControlTypeName in ("TextControl", "EditControl", "DocumentControl"):
            name = (c.Name or "").strip()
            if name and name not in SKIP:
                rows.append((c.ControlTypeName, c.AutomationId, name.replace("\n", " / ")))
    return rows


end = time.time() + SECONDS
while True:
    rows = snapshot()
    print(time.strftime("%H:%M:%S"), "-" * 40)
    for t, aid, name in rows:
        print(f"  {t} | {aid} | {name[:120]}")
    if time.time() >= end:
        break
    time.sleep(1)

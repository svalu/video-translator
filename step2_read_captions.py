"""2단계: 윈도우 실시간 자막 창에서 영어 글자를 읽어 오기.

윈도우 실시간 자막(Win+Ctrl+L)이 켜져 있어야 한다.
LiveCaptions-Translator와 같은 방법: 자막 창(LiveCaptionsDesktopWindow) 안의
글자 칸(AutomationId = CaptionsTextBlock)을 0.5초마다 읽고, 바뀌면 출력한다.

실행: .venv\\Scripts\\python.exe step2_read_captions.py [초]   (기본 30초 동안)
"""
import sys
import time

import uiautomation as auto

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 30


def find_caption_box():
    win = auto.WindowControl(searchDepth=1, ClassName="LiveCaptionsDesktopWindow")
    if not win.Exists(3, 0.5):
        return None, None
    box = win.Control(AutomationId="CaptionsTextBlock")
    if not box.Exists(3, 0.5):
        return win, None
    return win, box


win, box = find_caption_box()
if win is None:
    print("실시간 자막 창을 못 찾았어. Win+Ctrl+L 로 켜 줘.")
    sys.exit(1)
print("자막 창 찾음:", repr(win.Name))
if box is None:
    print("창은 있는데 글자 칸(CaptionsTextBlock)을 못 찾았어. 창 안의 이름들:")
    for c, depth in auto.WalkControl(win, maxDepth=6):
        print("  " * depth, c.ControlTypeName, "|", c.AutomationId, "|", c.Name[:60])
    sys.exit(2)

print(f"{SECONDS:.0f}초 동안 읽을게. 영상을 틀어 봐.\n")
last = None
end = time.time() + SECONDS
while time.time() < end:
    try:
        text = box.Name or ""
    except Exception as e:  # 창이 닫히거나 다시 그려질 때
        print("읽기 실패:", e)
        break
    if text != last:
        lines = [l for l in text.splitlines() if l.strip()]
        print("▶", lines[-1] if lines else "(비어 있음)")
        last = text
    time.sleep(0.5)
print("\n끝")

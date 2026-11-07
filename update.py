"""업데이트.bat 이 부른다: 패키지 맞추기 → 떠 있는 앱 끄기 → exe 빌드(바로가기 포함) → 새 exe 켜기.

bat 은 git pull 과 .venv 만들기만 하고(한글 없이 — cmd 코드페이지에 안 깨지게), 나머지는 여기서 한다.
git pull 뒤에 불리니까 이 파일은 늘 최신판이다.
"""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
NO_WINDOW = 0x08000000


def step(n, text):
    print(f"\n[{n}/4] {text}", flush=True)


def main():
    from version import VERSION
    print(f"동영상 번역기 업데이트 · 받아 온 버전 v{VERSION}")

    step(1, "패키지 맞추는 중 (처음이면 몇 분)")
    r = subprocess.run([PY, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", os.path.join(HERE, "requirements.txt")])
    if r.returncode != 0:
        print("  ✗ 패키지 설치가 안 됐어. 위 메시지를 드롱한테 보여 줘.")
        return 1

    step(2, "떠 있는 동영상 번역기 끄는 중 (exe 를 새로 덮어쓰려고)")
    subprocess.run(["taskkill", "/im", "VideoTranslator.exe", "/f"], capture_output=True, creationflags=NO_WINDOW)
    time.sleep(1)

    step(3, "exe 만드는 중 (1분쯤)")
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    log = os.path.join(HERE, "logs", "빌드.log")
    with open(log, "w", encoding="utf-8") as f:
        r = subprocess.run([PY, os.path.join(HERE, "build_exe.py")], cwd=HERE, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(f"  ✗ 빌드가 안 됐어. 기록: {log}")
        return 1
    print("  ✓ dist\\VideoTranslator\\VideoTranslator.exe · 바탕화면·시작 메뉴 바로가기 갱신")

    step(4, "새 동영상 번역기 켜는 중")
    exe = os.path.join(HERE, "dist", "VideoTranslator", "VideoTranslator.exe")
    os.startfile(exe)  # 윈도우가 따로 켠다 — 자식으로 켜면 업데이트 창이 닫힐 때 같이 꺼졌다 (11/08)
    print(f"\n끝. v{VERSION} 이 떠 있어.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

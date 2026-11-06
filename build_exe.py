"""동영상 번역기를 exe 로 묶는다 (PyInstaller, 폴더 방식).

실행: .venv\\Scripts\\python.exe build_exe.py
결과: dist\\VideoTranslator\\VideoTranslator.exe  (+ 같은 폴더의 _internal)
      dist\\VideoTranslator.zip                    (다른 사람한테 줄 때 이 zip 하나)

exe 안에 안 들어가는 것: Ollama 와 번역 모델 — 앱의 '준비 점검' 카드가 버튼으로 설치·받기를 해 준다.
"""
import os
import shutil
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

PyInstaller.__main__.run([
    "app.py",
    "--name", "VideoTranslator",
    "--noconfirm", "--clean",
    "--windowed",                       # 검은 창 없이
    "--icon", os.path.join("ui", "brand", "app.ico"),
    "--add-data", f"ui{os.pathsep}ui",  # 화면·아이콘
    "--collect-all", "uiautomation",    # 자막 창 읽기 (DLL 포함)
    "--collect-all", "webview",         # WebView2 창
    "--collect-all", "clr_loader",
    "--collect-all", "pythonnet",
    "--collect-data", "certifi",        # Edge 목소리 https
    "--hidden-import", "comtypes.stream",
    "--hidden-import", "subtitle_overlay",
    "--exclude-module", "pyttsx3",      # 예전 방식에서만 썼다
])

# 버전별 zip: 지금 버전은 dist\VideoTranslator_v0.x.zip, 다른 버전은 dist\Archive\ 로 (같은 버전 다시 묶으면 덮어씀)
from version import VERSION
dist = os.path.join(HERE, "dist", "VideoTranslator")
archive = os.path.join(HERE, "dist", "Archive")
os.makedirs(archive, exist_ok=True)
for name in os.listdir(os.path.join(HERE, "dist")):
    if name.startswith("VideoTranslator_v") and name.endswith(".zip") and name != f"VideoTranslator_v{VERSION}.zip":
        shutil.move(os.path.join(HERE, "dist", name), os.path.join(archive, name))
zip_base = os.path.join(HERE, "dist", f"VideoTranslator_v{VERSION}")
if os.path.exists(zip_base + ".zip"):
    os.remove(zip_base + ".zip")
shutil.make_archive(zip_base, "zip", os.path.dirname(dist), "VideoTranslator")
print("만듦:", dist, "/", zip_base + ".zip")

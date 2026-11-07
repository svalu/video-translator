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
    # Whisper 귀 (11/08). GPU 부품(nvidia-* 약 2GB)은 넣지 않는다 — 준비 점검이 %LOCALAPPDATA% 로 받는다
    "--hidden-import", "whisper_ear",
    "--collect-all", "faster_whisper",  # VAD 모델(onnx) 등 자료 포함
    "--collect-all", "ctranslate2",
    "--collect-all", "pyaudiowpatch",   # PC 소리 받기 (루프백)
    "--collect-binaries", "onnxruntime",
    "--exclude-module", "nvidia",
    "--exclude-module", "pyttsx3",      # 예전 방식에서만 썼다
])

# C++ 런타임 바꿔 넣기 (11/08): PyInstaller 가 어느 패키지에서 오래된 MSVCP140.dll(14.36)을 주워 넣는데,
# Whisper 엔진(ctranslate2)은 새 컴파일러(14.40+)로 만들어져서 std::mutex 쓰는 순간 0xc0000005 로 exe 가 통째로 죽는다.
# → 이 PC 윈도우에 깔린 최신 런타임(System32)으로 덮어쓴다.
_internal = os.path.join(HERE, "dist", "VideoTranslator", "_internal")
_sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
for _n in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"):
    if os.path.exists(os.path.join(_sys32, _n)):
        shutil.copy2(os.path.join(_sys32, _n), os.path.join(_internal, _n))

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

# build\ 는 PyInstaller 중간 작업장. 거기에도 같은 이름·크기의 VideoTranslator.exe 가 남는데 _internal 이 없어서
# 누르면 "Failed to load Python DLL" 이 뜬다(11/08 실제로 헷갈림). 완성본은 dist\ 에만 남긴다.
shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
print("만듦:", dist, "/", zip_base + ".zip", "(중간 build\\ 폴더는 지움)")

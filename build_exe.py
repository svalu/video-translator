"""동영상 동시 통역기를 exe 로 묶는다 (PyInstaller, 폴더 방식).

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

# 11/10: dist\VideoTranslator 폴더를 탐색기·백신 등이 붙잡고 있으면 PyInstaller 가 폴더째 지우다 실패한다
# (안은 비워도 폴더 자체는 못 지움, 안에 쓰기는 됨). → 다른 곳(build\_out)에 만들고 내용만 옮겨 담는다.
_OUT = os.path.join(HERE, "build", "_out")

PyInstaller.__main__.run([
    "app.py",
    "--name", "VideoTranslator",
    "--noconfirm", "--clean",
    "--distpath", _OUT,
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

# build\_out\VideoTranslator → dist\VideoTranslator : 폴더는 두고 안의 것만 갈아 끼운다
_dst = os.path.join(HERE, "dist", "VideoTranslator")
os.makedirs(_dst, exist_ok=True)
for _n in os.listdir(_dst):
    _p = os.path.join(_dst, _n)
    shutil.rmtree(_p, ignore_errors=True) if os.path.isdir(_p) else os.remove(_p)


# 11/11: 회사 보안 프로그램(Apex One)이 exe 를 오탐해서 격리할 때 dist 안의 파일 하나(WebView2Loader.dll)를 잠가
# 지우지도 덮어쓰지도 못했다. 새로 만든 것과 크기가 같은(= 같은 판) 잠긴 파일은 그대로 두고 넘어간다.
def _copy(src, dst):
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)):
            raise
        print("잠겨 있어서 두고 감 (같은 파일):", dst)


shutil.copytree(os.path.join(_OUT, "VideoTranslator"), _dst, dirs_exist_ok=True, copy_function=_copy)

# C++ 런타임 바꿔 넣기 (11/08): PyInstaller 가 어느 패키지에서 오래된 MSVCP140.dll(14.36)을 주워 넣는데,
# Whisper 엔진(ctranslate2)은 새 컴파일러(14.40+)로 만들어져서 std::mutex 쓰는 순간 0xc0000005 로 exe 가 통째로 죽는다.
# → 이 PC 윈도우에 깔린 최신 런타임(System32)으로 덮어쓴다.
_internal = os.path.join(HERE, "dist", "VideoTranslator", "_internal")
_sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
for _n in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"):
    if os.path.exists(os.path.join(_sys32, _n)):
        shutil.copy2(os.path.join(_sys32, _n), os.path.join(_internal, _n))

# 어느 커밋으로 만든 exe 인지 적어 둔다 → 앱이 GitHub 최신과 비교해 '새 버전 있어'를 띄운다 (updater.py, 11/08)
import subprocess as _sp
try:
    _head = _sp.run(["git", "-C", HERE, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    _dirty = bool(_sp.run(["git", "-C", HERE, "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True).stdout.strip())
    with open(os.path.join(HERE, "dist", "VideoTranslator", "build_commit.txt"), "w", encoding="utf-8") as _f:
        _f.write(_head + (" dirty" if _dirty else "") + "\n")
except Exception:
    pass  # git 이 없으면 알림 없이

# 나눠 줄 때 안내문 (11/11): zip 을 받은 사람이 제일 먼저 보게 exe 옆에. 메모장이 헷갈리지 않게 BOM 붙은 UTF-8.
with open(os.path.join(HERE, "처음_읽어주세요.txt"), encoding="utf-8") as _f:
    _guide = _f.read()
with open(os.path.join(HERE, "dist", "VideoTranslator", "처음_읽어주세요.txt"), "w", encoding="utf-8-sig") as _f:
    _f.write(_guide)

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

# 바탕화면·시작 메뉴 '동영상 동시 통역기' 바로가기 → 완성본 exe (11/08: build\ 의 가짜 exe 를 눌러 헤맨 뒤로)
# 한글 이름이라 PowerShell 에 UTF-16 으로 넘긴다 (bat/콘솔 코드페이지에 안 휘둘리게)
import base64
import subprocess
_exe = os.path.join(dist, "VideoTranslator.exe")
_ps = (
    "$ws = New-Object -ComObject WScript.Shell; "
    "foreach ($d in [Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs')) { "
    "$l = $ws.CreateShortcut((Join-Path $d '동영상 동시 통역기.lnk')); "
    f"$l.TargetPath = '{_exe}'; $l.WorkingDirectory = '{dist}'; $l.IconLocation = '{_exe},0'; "
    "$l.Description = '영어 영상·화면을 한국어로 (희랑)'; $l.Save(); "
    "Remove-Item (Join-Path $d '동영상 번역기.lnk') -ErrorAction SilentlyContinue }"  # 11/10 옛 이름 바로가기 정리
)
subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", base64.b64encode(_ps.encode("utf-16-le")).decode()],
               check=False, capture_output=True)
# 윈도우는 아이콘을 경로로 캐시해서 exe 아이콘을 바꿔도 바로가기가 한동안 예전 그림이다 → 캐시 새로 고침
subprocess.run(["ie4uinit.exe", "-show"], check=False, capture_output=True)
print("만듦:", dist, "/", zip_base + ".zip", "(중간 build\\ 폴더는 지움, 바로가기 갱신)")

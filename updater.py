"""새 버전 알림 (11/08): 켤 때·3시간마다 GitHub 의 main 과 '지금 켜진 앱'을 비교해서, 뒤처졌으면 화면에 띠를 띄운다.

PC 가 여럿(집 윈10 / 회사 윈11)이라 업데이트할 때를 사람이 기억하면 형상이 어긋난다 → 앱이 알려 준다.

  지금 켜진 앱의 커밋: exe 면 빌드할 때 적어 둔 dist\\VideoTranslator\\build_commit.txt, 코드로 돌리면 git HEAD
  최신: git fetch 뒤 origin/main
  누르면: 저장소의 업데이트.bat 을 새 창으로 (pull → 빌드 → 앱 다시 켜기)

git 이 없거나, 인터넷이 없거나, 저장소 밖에서 돈 exe(zip 으로 받은 것)면 아무것도 안 띄운다.
"""
import os
import re
import subprocess
import sys
import threading
import time

NO_WINDOW = 0x08000000
EVERY = 3 * 3600
BAT = "업데이트.bat"


def repo_dir():
    """이 앱이 속한 git 저장소 폴더. exe 면 <저장소>\\dist\\VideoTranslator\\VideoTranslator.exe 에서 두 칸 위."""
    if getattr(sys, "frozen", False):
        d = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sys.executable))))
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    return d if os.path.isdir(os.path.join(d, ".git")) and os.path.exists(os.path.join(d, BAT)) else None


def _git(repo, *args, timeout=20):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # 비공개 저장소 로그인 창이 뜨지 않게
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                       timeout=timeout, creationflags=NO_WINDOW, env=env)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout.strip()


def running_commit(repo):
    """지금 켜진 앱이 어느 커밋으로 만들어졌나."""
    test = os.environ.get("VT_UPDATE_TEST_LOCAL")  # 시험용: 예전 판인 척
    if test:
        return _git(repo, "rev-parse", test)
    if getattr(sys, "frozen", False):
        p = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "build_commit.txt")
        try:
            with open(p, encoding="utf-8") as f:
                return f.read().split()[0]
        except (OSError, IndexError):
            return None
    return _git(repo, "rev-parse", "HEAD")


class Updater:
    def __init__(self):
        self.repo = repo_dir()
        self.info = {"available": False, "checking": False, "behind": 0, "latest_version": "", "latest": "", "error": ""}
        self._lock = threading.Lock()

    def start(self):
        if not self.repo:
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        time.sleep(3)  # 켜자마자 말고 화면이 뜬 뒤에
        while True:
            self.check()
            time.sleep(EVERY)

    def check(self):
        if not self.repo:
            return self.info
        with self._lock:
            self.info["checking"] = True
            try:
                _git(self.repo, "fetch", "--quiet", "origin", "main", timeout=30)
                latest = _git(self.repo, "rev-parse", "origin/main")
                mine = running_commit(self.repo)
                behind = 0
                if mine and mine != latest:
                    try:
                        behind = int(_git(self.repo, "rev-list", "--count", f"{mine}..{latest}"))
                    except Exception:
                        behind = 1  # 커밋을 못 찾음(예전 빌드) → 일단 새 버전으로 본다
                elif not mine:
                    behind = 1      # 빌드 기록이 없는 예전 exe
                ver = re.search(r'VERSION\s*=\s*"([^"]+)"', _git(self.repo, "show", "origin/main:version.py"))
                self.info.update(available=behind > 0, behind=behind, latest_version=ver.group(1) if ver else "",
                                 latest=_git(self.repo, "log", "-1", "--format=%s", "origin/main")[:80], error="")
            except Exception as e:   # 인터넷 없음·git 없음 → 조용히
                self.info.update(available=False, error=f"{type(e).__name__}")
            finally:
                self.info["checking"] = False
        return self.info

    def run_update(self):
        """업데이트.bat 을 새 창으로. 그 안의 update.py 가 이 앱을 끄고 새로 만들어 다시 켠다."""
        if not self.repo:
            return False
        os.startfile(os.path.join(self.repo, BAT))
        return True

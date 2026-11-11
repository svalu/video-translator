"""번역기 앱 없이: 윈도우 실시간 자막을 직접 읽어 문장을 끊고, 내 PC 모델(Ollama)로 번역한다.

  CaptionSegmenter  실시간 자막 글자 → 끝난 영어 문장 (중복 없이, 한 번씩)
  LocalTranslator   영어 문장 → 한국어 (Ollama, 앞 문장 맥락 + 용어집)

실시간 자막 원문은 마침표를 꽤 정확히 찍는다(2단계 기록). 그래서 '뒤에 다음 문장이 붙은 문장'은
끝난 걸로 보고, 마지막 문장은 마침표로 끝난 채 잠깐 안 바뀌면 끝난 걸로 본다.
"""
import json
import re
import subprocess
import time
import urllib.request
from difflib import SequenceMatcher

import uiautomation as auto

OLLAMA = "http://127.0.0.1:11434/api/chat"

SYSTEM = """너는 영어 강의를 한국어로 동시통역하는 통역사야.
강의 주제: Zendesk(고객 상담 소프트웨어) 교육. 듣는 사람은 한국 콜센터·UX 실무자.
규칙:
- '옮길 문장' 한 줄만 한국어로 옮긴다. 앞 문장은 맥락 참고용이고, 앞 문장 내용을 번역에 다시 넣지 않는다.
- 영어는 실시간 자막이라 말버릇(you know, so, his his)·반복·끊긴 말이 섞여 있다. 군더더기는 빼고 말하려는 뜻만 자연스러운 존댓말 한두 문장으로.
- 이 용어는 영어 그대로 쓴다: Zendesk, Copilot, AI agent, ticket, macro, trigger, escalation, knowledge base, resolution platform, agent workspace.
  (agent = 상담원, AI agent = AI 에이전트, ticket = 티켓)
- 자막이 잘못 알아들은 듯한 고유명사(Zenda Desk → Zendesk)는 바로잡는다.
- 옮길 문장이 인사말·추임새뿐이거나 뜻을 알 수 없으면 '-' 한 글자만 출력한다.
- 설명·메모·주석·괄호 설명(Note: …)·따옴표·영어 원문 없이 한국어 번역만 출력한다. 뜻이 애매해도 가장 그럴듯한 번역 하나만.
- 번역을 한 번만 쓴다. '더 명확한 번역은…', '추가 설명이 필요하면…' 같은 말, 다시 고쳐 쓴 번역, 대안은 절대 붙이지 않는다.
- 문장 조각(예: 'Consistency and reporting purposes.')이면 그 조각만 짧게 옮긴다."""

# 11/10: 모델이 번역 뒤에 듣는 사람한테 하는 말을 붙여 자막에 그대로 나왔다.
#   "…확인하세요. 정확한 이해를 위해 조금 더 명확한 표현이 필요할 수 있으나… 알려주세요. 더 명확한 번역을 위해…"
#   "가장 적절한 번역은 다음과 같습니다: "…""
# 이런 말이 든 문장부터는 버린다.
_META = re.compile(r"번역|명확한 표현|추가 설명|설명이 필요|알려\s?주세요|조정할 수 있|주어진 (?:정보|문장)|원문|문맥상|맥락상|의역")


def strip_meta(ko):
    """번역 앞뒤에 붙은 '번역에 대한 말'을 떼고 번역만 남긴다."""
    # 첫 문장이 '…번역은 다음과 같습니다: "X"' 모양이면 X 만
    m = re.match(r"[^:：.?!]*(?:번역|표현)[^:：.?!]*다음과 같[^:：.?!]*[:：]\s*(.+)", ko, re.S)
    if m:
        ko = m.group(1)
    ko = ko.strip().strip('"“”\'').strip()
    keep = []
    for s in re.split(r"(?<=[.?!])\s+", ko):
        if _META.search(s):
            break
        keep.append(s)
    return " ".join(keep).strip().strip('"“”\'').strip()


def norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def find_caption_box(launch=False):
    """윈도우 실시간 자막 창의 글자 칸. 없으면 (launch=True일 때) 실시간 자막을 켜 본다."""
    win = auto.WindowControl(searchDepth=1, ClassName="LiveCaptionsDesktopWindow")
    if not win.Exists(1, 0.5):
        if not launch:
            return None
        # 11/10: 오래 두면 실시간 자막이 '응답 없음' 상태로 보이지 않는 창만 남아 있었다 (IsHungAppWindow).
        # 프로세스가 살아 있으니 다시 켜도(Popen) 새 창이 안 뜬다 → 멈춘 걸 끄고 새로 켠다.
        import ctypes
        u32 = ctypes.windll.user32
        hw = u32.FindWindowW("LiveCaptionsDesktopWindow", None)
        if hw and (u32.IsHungAppWindow(hw) or (not u32.IsWindowVisible(hw) and not u32.IsIconic(hw))):
            subprocess.run(["taskkill", "/im", "LiveCaptions.exe", "/f"], capture_output=True, creationflags=0x08000000)
            time.sleep(1.5)
        try:
            subprocess.Popen(["LiveCaptions.exe"])  # 윈도우 기본 프로그램 (Win+Ctrl+L 과 같음)
        except OSError:
            return None
        if not win.Exists(15, 0.5):  # 새로 켜면 창이 뜨기까지 6초 넘게 걸리기도 한다 (11/10)
            return None
    return CaptionBox(win)


class CaptionBox:
    """실시간 자막 글자 칸. .Name 으로 지금 글자를 읽는다.
    11/10: 막 켠 실시간 자막은 말소리가 들리기 전까지 '…표시 준비 완료'(ReadyToCaptionTextBlock)만 있고
    글자 칸(CaptionsTextBlock)이 아직 없다. 예전엔 그걸 '창 없음'으로 봐서 '안 보여. Win+Ctrl+L' 을 띄웠고,
    그 말대로 단축키를 누르면 토글이라 오히려 꺼졌다. → 창만 있으면 찾은 걸로 치고, 칸이 없으면 빈 글자."""

    def __init__(self, win):
        self.win = win
        self.box = None

    @property
    def Name(self):
        if self.box is not None:
            try:
                return self.box.Name or ""
            except Exception:
                self.box = None  # 칸이 없어졌다 (조용해짐·창 닫힘) → 아래에서 다시 찾는다
        b = self.win.Control(searchDepth=6, AutomationId="CaptionsTextBlock")
        if b.Exists(0, 0):
            self.box = b
            return b.Name or ""
        if not self.win.Exists(0, 0):
            raise LookupError("실시간 자막 창이 닫혔어")  # 부르는 쪽이 다시 찾기·다시 켜기를 한다
        return ""


def _caption_hwnd():
    import ctypes
    return ctypes.windll.user32.FindWindowW("LiveCaptionsDesktopWindow", None)


_hidden_style = {}


def hide_caption_window():
    """회색 실시간 자막 창을 최소화하고 작업 표시줄에서도 뺀다. 숨겨도 글자는 계속 읽힌다(11/06 확인)."""
    import ctypes
    u, h = ctypes.windll.user32, _caption_hwnd()
    if not h:
        return
    ex = u.GetWindowLongW(h, -20)
    _hidden_style.setdefault(h, ex)
    u.SetWindowLongW(h, -20, ex | 0x80)  # WS_EX_TOOLWINDOW
    u.ShowWindow(h, 6)                   # SW_MINIMIZE


def show_caption_window():
    import ctypes
    u, h = ctypes.windll.user32, _caption_hwnd()
    if not h or h not in _hidden_style:
        return
    u.SetWindowLongW(h, -20, _hidden_style.pop(h))
    u.ShowWindow(h, 9)                   # SW_RESTORE


_SPLIT = re.compile(r"(?<=[.?!])\s+")


class CaptionSegmenter:
    """자막 칸 전체 글자를 계속 받아서, 끝난 영어 문장을 한 번씩만 내보낸다."""

    STABLE = 0.6      # 뒤에 다음 문장이 붙은 문장: 이만큼 안 바뀌면 끝
    LAST_STABLE = 1.2 # 마지막 문장: 마침표로 끝나고 이만큼 안 바뀌면 끝
    MIN_WORDS = 4     # 이보다 짧으면 다음 문장 앞에 붙인다 ("So.", "And then.")
    CARRY_MAX = 4.0   # 짧은 문장을 붙일 다음 문장이 이 안에 안 오면 버림 (추임새)

    def __init__(self):
        self.seen = {}        # 문장 원문 → 처음 본 시각 (이 모양 그대로)
        self.born = {}        # 문장 앞부분(정규화 12자) → 처음 들린 시각 (지연 재기용)
        self.emitted = []     # 내보낸 문장 [정규화 글, 자막 칸에서 마지막으로 본 시각]
        self.carry = None     # (짧은 문장, 시각, 처음 들린 시각)

    def prime(self, text):
        """시작할 때 이미 자막 칸에 있던 문장은 읽은 걸로 친다."""
        now = time.time()
        for s in _SPLIT.split(" ".join(text.split())):
            if norm(s):
                self.emitted.append([norm(s), now])

    FORGET = 20.0  # 자막 칸에서 사라지고 이만큼 지나면 '읽은 문장'에서 뺀다

    def _done(self, key, now):
        """이미 내보낸 문장인가. 11/11: 영상을 되감아 같은 문장이 다시 나오면 '읽은 것'으로 쳐서 영영 안 읽었다.
        → 자막 칸에 계속 보이는 동안(마지막으로 본 지 20초 안)만 읽은 것으로 친다. 보일 때마다 시각을 새로 고친다."""
        for e in self.emitted[-40:]:
            if now - e[1] > self.FORGET:
                continue
            k = e[0]
            if key == k or (len(key) >= 15 and key in k) or (len(key) > 20 and SequenceMatcher(None, key, k).ratio() > 0.85):
                e[1] = now
                return True
        return False

    def feed(self, text, now):
        """[(영어 문장, 처음 들린 시각)] 를 돌려준다."""
        flat = " ".join(text.split())
        sents = [s for s in _SPLIT.split(flat) if s.strip()]
        out = []
        alive = set()
        for i, s in enumerate(sents):
            alive.add(s)
            self.seen.setdefault(s, now)
            head = norm(s)[:12]
            self.born.setdefault(head, now)
            last = i == len(sents) - 1
            age = now - self.seen[s]
            if last:
                ok = s.endswith((".", "?", "!")) and age >= self.LAST_STABLE
            else:
                ok = age >= self.STABLE
            key = norm(s)
            if not key or self._done(key, now) or not ok:
                continue
            self.emitted.append([key, now])
            out += self._emit(s, now, self.born.get(head, now))
        # 오래된 기록 정리 (자막 칸에서 사라진 문장)
        self.seen = {s: t for s, t in self.seen.items() if s in alive}
        if len(self.born) > 400:
            self.born = dict(list(self.born.items())[-200:])
        if len(self.emitted) > 200:
            self.emitted = self.emitted[-100:]
        if self.carry and now - self.carry[1] > self.CARRY_MAX:
            self.carry = None
        return out

    def _emit(self, s, now, born):
        if len(s.split()) < self.MIN_WORDS:
            if self.carry:  # 짧은 게 두 번 연달아 오면 이어 붙여 둔다
                s = self.carry[0] + " " + s
                born = self.carry[2]
            if len(s.split()) < self.MIN_WORDS:
                self.carry = (s, now, born)
                return []
            self.carry = None
            return [(s, born)]
        if self.carry:
            s, born = self.carry[0] + " " + s, self.carry[2]
            self.carry = None
        return [(s, born)]


_PAREN = re.compile(r"\s*[\(（]([^()（）]*)[\)）]?")


def strip_notes(ko):
    """모델이 번역 뒤에 붙이는 메모를 뗀다. 예: '…있습니다. (Note: The original sentence seems…)'
    괄호 안이 영어 위주로 길거나 Note/참고/설명으로 시작하면 지운다. (ROI) 같은 짧은 건 둔다."""
    def drop(m):
        inner = m.group(1).strip()
        latin = sum(c.isascii() and c.isalpha() for c in inner)
        if re.match(r"(note|참고|주의|설명|번역|원문)", inner, re.I) or (len(inner) > 15 and latin > len(inner) * 0.5):
            return ""
        return m.group(0)
    ko = _PAREN.sub(drop, ko)
    ko = re.split(r"\s*(?:Note|NOTE|참고)\s*[:：]", ko)[0]  # 괄호 없이 붙은 메모
    return ko.strip()


class LocalTranslator:
    """Ollama 로 번역. 앞 문장 2개를 맥락으로 준다."""

    def __init__(self, model):
        self.model = model
        self.ctx = []
        self.last_sec = 0.0

    def _call(self, messages, keep="30m", num_predict=200):
        body = json.dumps({
            "model": self.model, "stream": False, "keep_alive": keep,
            "options": {"temperature": 0.2, "num_predict": num_predict},
            "messages": messages,
        }).encode()
        req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["message"]["content"].strip()

    def warm(self):
        """모델을 그래픽 메모리에 올려 둔다 (첫 문장이 늦지 않게). 실패하면 예외."""
        self._call([{"role": "user", "content": "안녕"}], num_predict=1)

    def unload(self):
        try:
            body = json.dumps({"model": self.model, "keep_alive": 0}).encode()
            urllib.request.urlopen(urllib.request.Request(
                "http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"}), timeout=10)
        except Exception:
            pass

    def translate(self, en):
        user = ""
        if self.ctx:
            user += "앞 문장(참고만):\n" + "\n".join(self.ctx[-2:]) + "\n\n"
        user += "옮길 문장:\n" + en
        t = time.time()
        # 답 길이를 원문에 맞춰 묶는다 — 번역 뒤에 설명을 길게 늘어놓지 못하게 (11/10)
        cap = 40 + 5 * len(en.split())
        ko = self._call([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], num_predict=cap)
        self.last_sec = time.time() - t
        self.ctx.append(en)
        self.ctx = self.ctx[-4:]
        ko = ko.strip().strip('"“”').strip()
        if ko.startswith("- "):
            ko = ko[2:].strip()  # "- 어떻게…" 처럼 '-' 를 앞에 붙여 답할 때가 있다
        ko = strip_notes(strip_meta(ko))
        if ko.startswith("(") and ko.endswith(")"):
            return ""  # "(의미 파악 어려움)" 같은 메모는 읽지 않는다
        if ko in ("-", "") or not re.search(r"[가-힣]", ko):
            return ""  # 추임새·뜻 모를 문장, 또는 한국어가 아닌 답
        return ko


if __name__ == "__main__":
    # 점검: 30초 동안 끊은 문장과 번역을 찍어 본다.  python local_pipeline.py [초] [모델]
    import sys
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30
    model = sys.argv[2] if len(sys.argv) > 2 else "exaone3.5:7.8b"
    box = find_caption_box(launch=True)
    if box is None:
        print("실시간 자막 창을 못 찾았어 (Win+Ctrl+L)")
        sys.exit(1)
    seg, tr = CaptionSegmenter(), LocalTranslator(model)
    tr.warm()
    seg.prime(box.Name or "")  # 이미 화면에 있던 문장은 건너뜀
    end = time.time() + secs
    while time.time() < end:
        for en, born in seg.feed(box.Name or "", time.time()):
            ko = tr.translate(en)
            print(f"[{time.time() - born:4.1f}초 · 번역 {tr.last_sec:.1f}초] {en}\n   → {ko or '(건너뜀)'}")
        time.sleep(0.3)

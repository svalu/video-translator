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

# 11/11: 줌 강의를 듣는데 'Zoom IQ licensing'을 'Zendesk 라이선스'로 옮겼다 — 강의 주제가 Zendesk 로 박혀 있어서.
# → 최근 영어 문장에 나온 제품 이름으로 강의 주제를 알아서 고르고(TOPICS), 원문에 없는 회사·제품 이름은 넣지 않게 한다.
TOPICS = {
    "zendesk": {
        "about": "Zendesk(고객 상담 소프트웨어) 교육. 듣는 사람은 한국 콜센터·UX 실무자.",
        "terms": "Zendesk, Copilot, AI agent, ticket, macro, trigger, escalation, knowledge base, resolution platform, agent workspace."
                 " (agent = 상담원, AI agent = AI 에이전트, ticket = 티켓)",
        "fix": "자막이 잘못 알아들은 듯한 고유명사는 바로잡는다: Zenda Desk → Zendesk, E12·Eat well → Eatwell(데모용 가상 음식 배달 회사).",
    },
    "zoom": {
        "about": "Zoom(화상회의·전화·컨택센터) 파트너 교육. 듣는 사람은 한국 실무자.",
        "terms": "Zoom, Zoom IQ, Revenue Accelerator, AI Companion, Zoom Meetings, Zoom Phone, Contact Center, Virtual Agent,"
                 " Zoom Workplace, Workvivo, Zoom Spaces, revenue, pipeline, deal, forecasting, coaching, license.",
        "fix": "",
    },
    "general": {
        "about": "업무용 소프트웨어 교육.",
        "terms": "회사·제품·기능 이름.",
        "fix": "",
    },
}

SYSTEM_TMPL = """너는 영어 강의를 한국어로 동시통역하는 통역사야.
강의 주제: {about}
규칙:
- '옮길 문장' 한 줄만 한국어로 옮긴다. 앞 문장은 맥락 참고용이고, 앞 문장 내용을 번역에 다시 넣지 않는다.
- 영어는 실시간 자막이라 말버릇(you know, so, his his)·반복·끊긴 말이 섞여 있다. 군더더기는 빼고 말하려는 뜻만 자연스러운 존댓말 한두 문장으로.
- 이 용어는 영어 그대로 쓴다: {terms}
- 회사·제품 이름은 원문에 나온 그대로 쓴다. 원문에 없는 회사·제품 이름을 넣거나 다른 이름으로 바꾸지 않는다.
{fix}- 옮길 문장이 인사말·추임새뿐이거나 뜻을 알 수 없으면 '-' 한 글자만 출력한다.
- 설명·메모·주석·괄호 설명(Note: …)·따옴표·영어 원문 없이 한국어 번역만 출력한다. 뜻이 애매해도 가장 그럴듯한 번역 하나만.
- 번역을 한 번만 쓴다. '더 명확한 번역은…', '추가 설명이 필요하면…' 같은 말, 다시 고쳐 쓴 번역, 대안은 절대 붙이지 않는다.
- 문장 조각(예: 'Consistency and reporting purposes.')이면 그 조각만 짧게 옮긴다."""


def system_prompt(topic, extra_terms=(), title=""):
    t = TOPICS.get(topic, TOPICS["general"])
    about, terms = t["about"], t["terms"]
    if title:  # 11/12: 브라우저 창 제목 = 강의 이름. 이름을 알아보는 데만 쓰게 한다
        about += f" 지금 열려 있는 페이지 제목은 '{title}' (참고만 — 원문에 없는 말을 번역에 넣지 않는다)."
    if extra_terms:
        terms += " 이 강의에서 들린 이름: " + ", ".join(extra_terms) + "."
    return SYSTEM_TMPL.format(about=about, terms=terms, fix=("- " + t["fix"] + "\n") if t["fix"] else "")


# ---- 11/12: 용어를 강의에서 저절로 끌어온다 (Zendesk·Zoom 말고 다른 강의에서도 쓰게) ----
# ① 들린 영어에서 두 번 넘게 나온 이름(대문자로 시작하는 낱말 묶음)을 '영어 그대로 쓸 용어'에 넣는다.
# ② 브라우저 창 제목(강의 이름)을 강의 주제에 넣고, 자막이 그 이름을 잘못 받아 적었으면 바로잡는다.
_NAME_RE = re.compile(r"[A-Z][A-Za-z0-9]*(?:[ -][A-Z][A-Za-z0-9]*){0,2}")
_NOT_NAME = set("""I OK Okay Yes No Mr Mrs Ms Dr Monday Tuesday Wednesday Thursday Friday Saturday Sunday
January February March April May June July August September October November December English Korean Korea""".split())


def name_candidates(en, known=()):
    """문장 안의 이름 후보. 문장 첫 낱말은 그냥 대문자일 수 있어서, 이미 아는 이름으로 시작할 때만 친다."""
    known = {k.lower() for k in known}
    out = []
    for m in _NAME_RE.finditer(en):
        name = m.group()
        words = re.split(r"[ -]", name)
        at_start = m.start() == 0 or en[:m.start()].rstrip()[-1:] in ".?!:"
        if at_start and words[0].lower() not in known:
            # 첫 낱말은 떼고 나머지만 ('With Workvivo' → 'Workvivo', 'The Activity Feed' → 'Activity Feed')
            if len(words) == 1:
                continue
            name = name[len(words[0]) + 1:]
            words = words[1:]
        if all(w in _NOT_NAME for w in words):
            continue
        out.append(name)
    return out


_BROWSER_TAIL = re.compile(r"(?:\s[-–—]\s(?:프로필 ?\d*|Profile ?\d*|개인|회사|Personal|Work))?"
                           r"\s[-–—]\s(?:Google Chrome|Chrome|Microsoft\s?Edge|Naver Whale|Whale|Mozilla Firefox|Firefox)$")
_title_cache = [0.0, ""]


def browser_title(max_age=8.0):
    """맨 위에 있는 브라우저 창의 제목 (끝의 ' - Chrome' 은 뗀다). 없으면 ''. max_age 초 동안은 기억해 둔 걸 준다."""
    now = time.time()
    if now - _title_cache[0] < max_age:
        return _title_cache[1]
    found = [""]
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def each(hwnd, _):
            if not u.IsWindowVisible(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if not n:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            t = buf.value.replace("​", "")
            if _BROWSER_TAIL.search(t):
                found[0] = _BROWSER_TAIL.sub("", t).strip()
                return False  # EnumWindows 는 위에 있는 창부터 준다
            return True

        u.EnumWindows(each, 0)
    except Exception:
        pass
    _title_cache[:] = [now, found[0][:120]]
    return _title_cache[1]


def fix_by_title(en, title):
    """자막이 강의 이름을 잘못 받아 적었으면 제목에 있는 이름으로 (Vivo → Workvivo, Work Vivo → Workvivo)."""
    names = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]{5,}", title) if w[0].isupper()]
    if not names:
        return en
    in_title = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", title)}

    def swap(m):
        w = m.group()
        if w.lower() in in_title:
            return w
        for n in names:
            a, b = w.lower(), n.lower()
            if len(a) >= 4 and b.endswith(a):
                return n
            # 비슷하게 잘못 적은 것. 복수형(Partners)·앞부분만 같은 말(Learn)은 다른 낱말이니 건드리지 않는다
            if not a.startswith(b) and not b.startswith(a) and SequenceMatcher(None, a, b).ratio() >= 0.85:
                return n
        return w

    for n in names:  # 두 낱말로 쪼개 적은 것: 'Work Vivo' → 'Workvivo'
        for i in range(2, len(n) - 1):
            en = re.sub(r"\b" + re.escape(n[:i]) + r"[ -]" + re.escape(n[i:]) + r"\b", n, en, flags=re.I)
    return re.sub(r"\b[A-Z][A-Za-z0-9]{3,}\b", swap, en)


# 11/13: 자막이 잘못 받아 적은 문장('The total cost of India.')을 번역하지 않고 되물었다
#   → '이해가 되지 않아요. 다시 말씀해 주시겠어요?'  (통역이 아니라 대화 상대처럼 대답한 것)
_REPLY = re.compile(r"다시 (?:한 ?번 )?(?:말씀|설명)|이해(?:가|를)? ?(?:되지|하지|할 수) ?(?:않|못|없)|무슨 (?:말씀|뜻|의미)"
                    r"|(?:문장|내용|질문)(?:이|을)? ?(?:불완전|불분명|이상|명확하지|파악하기)|도와드릴")
_REPLY_OK = re.compile(r"understand|again|repeat|pardon|sorry|mean|clear|explain|help|confus|sense", re.I)


def is_reply(en, ko):
    """번역이 아니라 모델이 듣는 사람한테 한 대답인가 (원문에 그런 말이 없는데 되묻거나 못 알아듣겠다고 함)."""
    return bool(_REPLY.search(ko)) and not _REPLY_OK.search(en)


def foreign_names(en, ko):
    """번역에 들어갔는데 원문에는 없는 영어 이름 (대문자로 시작, 4자 이상). 모델이 지어낸 이름을 잡는다."""
    flat = re.sub(r"[^a-z0-9]", "", en.lower())
    out = []
    for w in re.findall(r"[A-Z][A-Za-z0-9]{3,}", ko):
        k = w.lower()
        if k not in flat and k.rstrip("s") not in flat:
            out.append(w)
    return out


def guess_topic(sentences, prev="zendesk"):
    """최근 영어 문장에 나온 제품 이름으로 강의 주제를 고른다. 둘 다 안 나오면 하던 대로."""
    text = " ".join(sentences).lower()
    zd = len(re.findall(r"zendesk|zenda desk", text))
    # 11/12: Workvivo 강의는 'Zoom' 을 거의 안 말한다 → 줌 제품 이름도 센다 (자막은 Workvivo 를 'Vivo'·'Work Vivo' 로 적는다)
    zm = len(re.findall(r"\bzoom\b|\b(?:work ?)?vivo\b|ai companion", text))
    if zm > zd:
        return "zoom"
    if zd > zm:
        return "zendesk"
    return prev


SYSTEM = system_prompt("zendesk")  # 예전 이름 (다른 곳에서 부를 때)

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
    LAST_STABLE_SHORT = 2.5  # 마지막 문장이 네 낱말 이하면 이만큼 (11/13)
    MIN_WORDS = 4     # 이보다 짧으면 다음 문장 앞에 붙인다 ("So.", "And then.")
    CARRY_MAX = 4.0   # 짧은 문장을 붙일 다음 문장이 이 안에 안 오면 버림 (추임새)

    def __init__(self):
        self.seen = {}        # 문장 원문 → 처음 본 시각 (이 모양 그대로)
        self.born = {}        # 문장 앞부분(정규화 12자) → [처음 들린 시각, 마지막으로 본 시각] (지연 재기용)
        self.emitted = []     # 내보낸 문장 [정규화 글, 자막 칸에서 마지막으로 본 시각] (기준 문장을 못 찾을 때만 씀)
        self.last_key = None  # 마지막으로 내보낸(또는 읽은 걸로 친) 문장 — 자막 칸에서 이 뒤에 붙는 문장이 새 문장
        self.new_born = []    # 기준 뒤 새 문장들이 처음 나타난 시각 (차례대로). 되감기 때 옛 문장의 시각을 안 쓰게
        self.carry = None     # (짧은 문장, 시각, 처음 들린 시각)
        self.prev_out = ""    # 바로 앞에 내보낸 문장 (정규화) — 그 끝을 되풀이한 조각을 거른다
        self.last_raw = None  # 마지막으로 내보낸 문장의 원래 글 — 자막이 그 문장을 뒤로 늘리면 늘어난 부분을 읽는다

    # 11/13: 말하다 쉬면 실시간 자막이 'From the Zoom app, tap on.' 처럼 마침표를 찍어 두었다가 말이 이어지면 문장을 늘린다.
    # 이런 말로 끝난 마지막 문장은 아직 안 끝난 것이니 더 기다린다.
    WAIT_WORDS = DANGLING_WORDS = set("""a an the your our their my its and or of to be on in at for with by from that which
        however but so because if when then is are can will""".split())
    LAST_STABLE_OPEN = 5.0

    # 11/13: 실시간 자막이 문장을 고쳐 쓰는 도중의 조각이 그대로 읽혔다.
    #   '…for over a decade.' 다음에 'For over a decade.' 가 또 / 'Are integral to your.' / 'She wants to be a.'
    # 관사·소유격·접속사로 끝난 짧은 문장은 말이 끊긴 조각이다 (뒤에 올 낱말이 없다).
    DANGLING = set("a an the your our their my its and or of to be".split())

    def _fragment(self, s):
        words = s.split()
        if len(words) > 6:
            return False
        k = norm(s)
        if len(words) <= 5 and len(k) >= 6 and self.prev_out.endswith(k):
            return True   # 앞 문장의 끝을 되풀이
        return s.endswith(".") and words[-1].strip(".,").lower() in self.DANGLING

    def prime(self, text):
        """시작할 때 이미 자막 칸에 있던 문장은 읽은 걸로 친다.
        11/11: 말하던 중인 마지막 문장(마침표 없이 끝남)은 빼 둔다 — 반쯤 들린 문장을 '읽은 것'으로 쳐서 그 긴 문장을 통째로 안 읽었다.
        마침표로 끝난 마지막 문장은 이미 끝난 옛 문장이라 읽지 않는다 (자동 시험에서 찾음: 시작하자마자 옛 문장을 읽었다)."""
        now = time.time()
        flat = " ".join(text.split())
        keys = [norm(x) for x in _SPLIT.split(flat) if norm(x)]
        done = keys if flat.endswith((".", "?", "!")) else keys[:-1]
        for k in done:
            self.emitted.append([k, now])
        self.last_key = done[-1] if done else None
        self.last_raw = None

    FORGET = 20.0  # 자막 칸에서 사라지고 이만큼 지나면 '읽은 문장'에서 뺀다

    @staticmethod
    def _same(a, b):
        return a == b or (len(a) >= 15 and len(b) >= 15 and (a in b or b in a)) or             (len(a) > 20 and SequenceMatcher(None, a, b).ratio() > 0.85)

    def _done(self, key, now):
        """(기준 문장을 못 찾았을 때) 이미 내보낸 문장인가. 자막 칸에 보이는 동안(마지막으로 본 지 20초 안)만."""
        for e in self.emitted[-40:]:
            if now - e[1] > self.FORGET:
                continue
            if self._same(key, e[0]):
                e[1] = now
                return True
        return False

    def feed(self, text, now):
        """[(영어 문장, 처음 들린 시각)] 를 돌려준다.

        11/11: 예전엔 '내용이 같은 문장을 이미 읽었나'로 걸렀다. 영상을 되감으면 자막 칸에 옛 문장과 새로 들린 같은 문장이
        같이 보여서, 새 문장까지 '읽은 것'으로 쳐서 안 읽었다. → '마지막으로 읽은 문장' 뒤에 붙은 문장만 새 문장으로 본다
        (자막 칸은 새 말이 끝에 붙는 두루마리). 기준 문장이 칸에서 밀려나 안 보이면 예전처럼 내용으로 거른다."""
        flat = " ".join(text.split())
        sents = [x for x in _SPLIT.split(flat) if x.strip()]
        keys = [norm(x) for x in sents]
        out = []
        alive = set()
        start = None
        # 기준 찾기: 마지막으로 읽은 문장 → 못 찾으면(자막이 그 문장을 고쳐 썼을 때) 그 앞에 읽은 문장들 차례로.
        # 11/11: 예전엔 못 찾으면 '20초 넘게 지난 문장은 안 읽은 것'으로 쳐서, 칸에 남아 있던 옛 문장 10여 개를 뭉치로 다시 내보냈다
        # (지연 75~120초, 같은 문장을 세 번씩 → 목소리가 밀려 계속 건너뜀).
        hit_last = False
        for n_anchor, anchor in enumerate([self.last_key] + [e[0] for e in reversed(self.emitted[-8:])]):
            if not anchor:
                continue
            hit_last = n_anchor == 0
            for j in range(len(keys) - 1, -1, -1):  # 끝에서부터: 같은 문장이 두 번 보이면 나중 것이 기준
                if keys[j] and self._same(keys[j], anchor):
                    start = j + 1
                    break
            if start is not None:
                break
        if start is None and self.emitted and len(keys) > 3:
            # 읽은 문장이 칸에 하나도 안 보인다 (칸이 통째로 바뀜) → 옛 문장을 쏟아 내지 말고 지금부터 다시 시작
            self.prime(text)
            start = len(keys) if flat.endswith((".", "?", "!")) else len(keys) - 1
        # 11/13: 내보낸 문장을 자막이 뒤로 늘렸으면 ('…tap on.' → '…tap on the phone icon to see the subsections.')
        # 예전엔 '이미 읽은 문장'으로 쳐서 늘어난 부분이 통째로 빠졌다 (희랑: "그냥 생략하는 경우가 있다").
        # → 늘어난 뒷부분만 새 문장처럼 내보낸다.
        tail_at, tail = None, ""
        if start is not None and hit_last and self.last_raw:
            j = start - 1
            head = self.last_raw.rstrip(".?!… ").lower()
            full = sents[j].strip()
            if len(head) >= 10 and full.lower().startswith(head) and len(full.rstrip(".?!… ")) - len(head) >= 8:
                tail = full[len(head):].lstrip(" ,;:-")
                if len(tail.split()) >= 2:
                    tail_at, start = j, j
        if start is not None:  # 기준 뒤 새 문장 수에 맞춰 '처음 나타난 시각'을 늘리거나 줄인다
            n_new = len(sents) - start
            self.new_born = (self.new_born + [now] * n_new)[:n_new]
        for i, x in enumerate(sents):
            alive.add(x)
            self.seen.setdefault(x, now)
            key = keys[i]
            head = key[:12]
            b = self.born.get(head)
            if b is None or now - b[1] > 10:  # 10초 넘게 안 보이던 머리글이 다시 나오면 새로 들린 것 (되감기)
                b = self.born[head] = [now, now]
            b[1] = now
            if not key:
                continue
            if start is not None and i < start:
                continue               # 기준 문장까지는 이미 읽음
            last = i == len(sents) - 1
            age = now - self.seen[x]
            # 짧은 마지막 문장은 더 기다린다 — 'For over a decade.' 가 곧 'For over a decade, our Zoom Phone…' 으로 늘어난다
            hold = self.LAST_STABLE if len(x.split()) > 4 else self.LAST_STABLE_SHORT
            if x.split()[-1].strip(".,?!").lower() in self.WAIT_WORDS:
                hold = self.LAST_STABLE_OPEN
            ok = (x.endswith((".", "?", "!")) and age >= hold) if last else age >= self.STABLE
            if not ok:
                break                  # 차례대로만 내보낸다 (뒤 문장이 먼저 나가면 앞 문장이 기준 앞으로 밀려 안 읽힘)
            born = b[0]
            if start is not None and self.new_born:
                born = self.new_born.pop(0)   # 기준 바로 뒤 문장 = 새 문장 목록의 첫째
            self.emitted.append([key, now])
            self.last_key = key
            self.last_raw = x.strip()
            start = i + 1
            if i == tail_at:
                tail_at = None
                out += self._emit(tail[:1].upper() + tail[1:], now, now)
                continue
            out += self._emit(x, now, born)
        # 오래된 기록 정리 (자막 칸에서 사라진 문장)
        self.seen = {k: t for k, t in self.seen.items() if k in alive}
        if len(self.born) > 400:
            self.born = {h: v for h, v in self.born.items() if now - v[1] < 30}
        if len(self.emitted) > 200:
            self.emitted = self.emitted[-100:]
        if self.carry and now - self.carry[1] > self.CARRY_MAX:
            self.carry = None
        return out

    def _emit(self, s, now, born):
        # 11/11: 실시간 자막이 고쳐 쓰다 남긴 한 단어('Crockett.', 'Benefit.')가 다음 문장 앞에 붙어 '크로켓, …'으로 번역됐다.
        # → 한 단어짜리는 버린다. 다음 문장이 붙여 둔 말로 시작하면('Let's dive. Let's dive Your team…') 붙이지 않는다.
        if len(s.split()) == 1:
            return []   # 11/13: 붙여 둔 말이 있어도 한 단어는 안 붙인다 ('So you didn't.' + 'PPX.' 가 읽혔다)
        if self._fragment(s):
            self.carry = None   # 조각 앞에 붙여 두려던 말도 같은 찌꺼기다 ('The year that.' + 'Are integral to your.')
            return []
        out = self._emit2(s, now, born)
        if out:
            self.prev_out = norm(out[-1][0])
        return out

    def _emit2(self, s, now, born):
        if self.carry and norm(s).startswith(norm(self.carry[0])):
            self.carry = None
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


# 실시간 자막이 자주 잘못 알아듣는 고유명사 — 모델한테 부탁해도 무시해서(11/11 E12) 번역 전에 영어에서 바꾼다
_CAPTION_FIX = [
    (re.compile(r"\bE-?12(?=\b|zendesk|\.)", re.I), "Eatwell"),   # Eatwell(데모용 가상 음식 배달 회사)을 E12 로
    (re.compile(r"\bEat well\b", re.I), "Eatwell"),
    (re.compile(r"\bZenda? ?Desk\b", re.I), "Zendesk"),
    (re.compile(r"\b(?:Work ?)?Vivo\b", re.I), "Workvivo"),   # 줌 강의에서만 (11/12 'Vivo isn't just a platform.')
]


# 번역이 제품 이름을 한글로 바꿨을 때 되돌린다 (규칙으로 부탁해도 가끔 무시한다, 11/11 'Zoom Meetings' → '줌 미팅').
# 영어 원문에 그 이름이 있을 때만. 긴 이름부터.
_NAMES = [
    ("Zoom Revenue Accelerator", r"(?:Zoom|줌)\s?[가-힣]{0,6}\s?(?:액셀러레이터|엑셀러레이터|가속화\s?도구|가속화|가속기)"),  # 'Zoom 리테일러 가속기'로도 옮겼다
    ("Revenue Accelerator", r"(?:레비뉴|레버뉴|수익|매출)\s?(?:액셀러레이터|엑셀러레이터|가속기)"),
    ("Zoom Contact Center", r"줌\s?(?:컨택|콘택트|컨택트)\s?센터"),
    ("Zoom Virtual Agent", r"줌\s?(?:버추얼|가상)\s?(?:에이전트|상담원)"),
    ("Zoom Meetings", r"줌\s?(?:미팅스?|회의)"),
    ("Zoom Phone", r"줌\s?(?:폰|전화)"),
    ("Zoom Workplace", r"줌\s?워크플레이스"),
    ("Zoom Spaces", r"줌\s?스페이스(?:스|즈)?"),
    ("AI Companion", r"AI\s?(?:컴패니언|동반자)"),
    ("Zoom IQ", r"줌\s?(?:IQ|아이큐)"),
    ("Workvivo", r"워크비보"),
    ("Zoom", r"줌(?=$|[^가-힣]|[은는이가을를의에과와도로만으])"),  # '해 줌니다' 같은 낱말 속 '줌'은 그대로
    ("Zendesk", r"젠데스크|젠디스크"),
    ("Copilot", r"코파일럿|부조종사"),
]


def restore_names(en, ko):
    low = en.lower()
    for name, pat in _NAMES:
        if name.lower() in low and name.lower() not in ko.lower():
            ko = re.sub(r"(?<![가-힣])(?:" + pat + r")", name, ko)
    return ko


def fix_caption(en, topic="zendesk"):
    for pat, rep in _CAPTION_FIX:
        if rep == "Eatwell" and topic != "zendesk":
            continue  # Eatwell 은 Zendesk 데모 회사 — 다른 강의의 'E12' 는 건드리지 않는다
        if rep == "Workvivo" and topic != "zoom":
            continue
        en = pat.sub(rep, en)
    return en


class LocalTranslator:
    """Ollama 로 번역. 앞 문장 2개를 맥락으로 준다."""

    def __init__(self, model):
        self.model = model
        self.ctx = []
        self.seen_en = []      # 강의 주제를 고르려고 최근 영어 문장을 모아 둔다
        self.names = {}        # 들린 이름 → 횟수 (11/12)
        self.banned = set()    # 앱 창에서 ✕ 로 뺀 이름 (이 강의 동안)
        self.title = ""        # 브라우저 창 제목
        self.use_title = True
        self.topic = "general"  # 11/12: 처음부터 Zendesk 로 잡으면 아직 이름이 안 나온 줌 강의에 'Zendesk' 를 넣는다
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

    def auto_terms(self, limit=15):
        """두 번 넘게 들린 이름, 많이 들린 순으로."""
        got = sorted((n for n, c in self.names.items() if c >= 2 and n not in self.banned), key=lambda n: -self.names[n])
        return got[:limit]

    def lesson(self):
        """앱 창에 보여 줄 것: 지금 잡힌 주제·창 제목·잡은 이름."""
        return {"topic": self.topic, "title": self.title, "names": self.auto_terms()}

    def warm(self):
        """모델을 그래픽 메모리에 올려 둔다 (첫 문장이 늦지 않게). 실패하면 예외."""
        self._call([{"role": "user", "content": "안녕"}], num_predict=1)

    def on_cpu_only(self):
        """모델이 그래픽카드를 하나도 안 쓰고 CPU 로만 올라가 있나 (NVIDIA 가 있을 때만 따짐).
        11/11: PC 를 켤 때 Ollama 가 자동 업데이트 뒤 그래픽카드가 준비되기 전에 떠서 GPU 를 못 찾았다
        → 100% CPU, 번역이 문장당 2초 → 4~10초. Ollama 를 다시 켜면 다시 찾는다."""
        import shutil
        if not shutil.which("nvidia-smi"):
            return False
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as r:
                for m in json.load(r).get("models", []):
                    if self.model in (m.get("name"), m.get("model")):
                        return int(m.get("size_vram", 0)) == 0
        except Exception:
            pass
        return False

    def unload(self):
        try:
            body = json.dumps({"model": self.model, "keep_alive": 0}).encode()
            urllib.request.urlopen(urllib.request.Request(
                "http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"}), timeout=10)
        except Exception:
            pass

    @staticmethod
    def _clean(ko):
        ko = ko.strip().strip('"“”').strip()
        # 11/12: 모델이 강조한다고 **낱말** 처럼 별표를 붙일 때가 있다(마크다운 굵은 글씨 표시). 자막엔 별표째 나온다 → 뗀다
        ko = re.sub(r"\*+|`+|(?<![A-Za-z0-9])__(?=\S)|(?<=\S)__(?![A-Za-z0-9])", "", ko).strip()
        if ko.startswith("- "):
            ko = ko[2:].strip()  # "- 어떻게…" 처럼 '-' 를 앞에 붙여 답할 때가 있다
        return strip_notes(strip_meta(ko))

    def translate(self, en):
        self.seen_en = (self.seen_en + [en])[-30:]
        self.topic = guess_topic(self.seen_en, self.topic)
        en = fix_caption(en, self.topic)
        # 창 제목은 강의와 상관있을 때만 쓴다 (다른 탭을 보고 있으면 엉뚱한 제목이 온다):
        # 제목의 낱말이 강의에서 들렸거나, 제목 덕에 잘못 적힌 이름을 바로잡았을 때
        title = browser_title() if self.use_title else ""
        if title:
            fixed = fix_by_title(en, title)
            heard = " ".join(self.seen_en + [en]).lower()  # en 은 이미 바로잡은 문장
            if fixed != en or any(w.lower() in heard for w in re.findall(r"[A-Za-z]{4,}", title)):
                self._title_ok = title
            if getattr(self, "_title_ok", "") == title:
                en = fixed
        self.title = title if getattr(self, "_title_ok", "") == title else ""
        known = set(self.names) | {w for n in self.names for w in n.split()} | set(re.findall(r"[A-Z][A-Za-z0-9]+", self.title))
        for n in name_candidates(en, known):
            self.names[n] = self.names.get(n, 0) + 1
        sysmsg = system_prompt(self.topic, self.auto_terms(), self.title)
        user = ""
        if self.ctx:
            user += "앞 문장(참고만):\n" + "\n".join(self.ctx[-2:]) + "\n\n"
        user += "옮길 문장:\n" + en
        t = time.time()
        # 답 길이를 원문에 맞춰 묶는다 — 번역 뒤에 설명을 길게 늘어놓지 못하게 (11/10)
        cap = 40 + 5 * len(en.split())
        ko = self._call([{"role": "system", "content": sysmsg}, {"role": "user", "content": user}], num_predict=cap)
        ko = self._clean(ko)
        # 11/11: 앞 문장(맥락) 내용을 끌어다 붙여 번역이 원문보다 훨씬 길어질 때가 있다
        #   'Using Zoom Meetings and Zoom phone.' → '판매 팀이 비디오를 활용해 … Zoom Meetings와 Zoom Phone을 활용하고 있을 가능성이 높습니다.'
        # 한국어는 보통 영어보다 글자 수가 적다 → 영어의 0.9배+12자를 넘으면 맥락 없이 한 번 더 옮겨 짧은 쪽을 쓴다.
        if self.ctx and len(ko) > len(en) * 0.9 + 12:
            ko2 = self._clean(self._call([{"role": "system", "content": sysmsg},
                                          {"role": "user", "content": "옮길 문장:\n" + en}], num_predict=cap))
            if ko2 and len(ko2) < len(ko):
                ko = ko2
        # 11/12: 원문에 없는 'Zendesk' 를 넣었으면 (줌 Workvivo 강의 'Vivo isn't just a platform.' → 'Zendesk는…')
        # 맥락·주제 없이 한 번 더 옮긴다. Zendesk 만이 아니라 원문에 없는 영어 이름은 다 (창 제목·용어집에서 끌어다 넣는 것 포함)
        def bad(k):
            return len(foreign_names(en, k)) + (1 if re.search(r"젠데스크|젠디스크", k) and "zendesk" not in en.lower() else 0)
        if bad(ko):
            ko2 = self._clean(self._call([{"role": "system", "content": system_prompt("general")},
                                          {"role": "user", "content": "옮길 문장:\n" + en}], num_predict=cap))
            if ko2 and bad(ko2) < bad(ko):
                ko = ko2
        if is_reply(en, ko):  # 번역 대신 대답했으면 맥락 없이 한 번 더, 그래도 대답이면 읽지 않는다
            ko = self._clean(self._call([{"role": "system", "content": system_prompt("general")},
                                         {"role": "user", "content": "옮길 문장:\n" + en}], num_predict=cap))
            if is_reply(en, ko):
                ko = ""
        ko = restore_names(en, ko)
        self.last_sec = time.time() - t
        self.ctx.append(en)
        self.ctx = self.ctx[-4:]
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

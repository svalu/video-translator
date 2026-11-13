"""문장 자르기·번역 뒤처리 자동 시험 (11/11). 모델(Ollama)·실시간 자막 없이 돈다.

    .venv\Scripts\python.exe -m unittest discover tests -v

11/11 하루에 문장 자르기를 네 번 고쳤다(되감기·시작 직후·중복·찌꺼기 단어). 고칠 때마다 여기 경우들이 그대로인지 본다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import local_pipeline as lp  # noqa: E402

A = "This demo will show you an example of what the experience can look like for an end user."
B = "The customer writes that they want to know more about the status of their pizza delivery."
C = "We can see that Facebook Messenger has been configured with an autoresponder."
D = "End users then have the ability to respond using text, images, files and emojis."
OLD = "And that's one example of an end user experience with social messaging."


class Scroll:
    """실시간 자막 칸 흉내: 새 말이 끝에 한 단어씩 붙고, 앞은 밀려난다."""

    def __init__(self, start=()):
        self.t = 1000.0
        self._real_time = lp.time.time
        lp.time.time = lambda: self.t
        self.seg = lp.CaptionSegmenter()
        self.box = list(start)
        self.out, self.lat = [], []

    def close(self):
        lp.time.time = self._real_time

    def text(self):
        return " ".join(self.box)[-600:]

    def feed(self, secs):
        for _ in range(int(secs / 0.3)):
            for en, born in self.seg.feed(self.text(), self.t):
                self.out.append(en)
                self.lat.append(round(self.t - born, 1))
            self.t += 0.3

    def speak(self, sent):
        words = sent.split()
        self.box.append("")
        for k in range(1, len(words) + 1):
            self.box[-1] = " ".join(words[:k]).rstrip(".") + ("" if k < len(words) else ".")
            self.feed(0.3)
        self.feed(1.5)


class SegmenterTest(unittest.TestCase):
    def setUp(self):
        self.s = Scroll([OLD])
        self.addCleanup(self.s.close)

    def heads(self):
        return [o[:20] for o in self.s.out]

    def test_reads_each_sentence_once(self):
        self.s.seg.prime(self.s.text())
        for x in (A, B, C):
            self.s.speak(x)
        self.s.feed(30)  # 계속 보여도 다시 읽지 않는다
        self.assertEqual(self.s.out, [A, B, C])

    def test_start_in_the_middle_of_a_sentence(self):
        """읽기 시작 때 말하던 중인 문장도 끝나면 읽는다 (v0.4.5)."""
        self.s.box.append("This demo will show you")
        self.s.seg.prime(self.s.text())
        self.s.box.pop()
        self.s.speak(A)
        self.assertEqual(self.s.out, [A])

    def test_old_sentences_are_not_read_at_start(self):
        self.s.seg.prime(self.s.text() + " Half a")
        self.s.feed(5)
        self.assertEqual(self.s.out, [])

    def test_rewind_reads_again(self):
        """되감아 같은 문장이 다시 나오면 다시 읽는다 (v0.4.4~v0.4.6)."""
        self.s.seg.prime(self.s.text())
        for x in (A, B, C):
            self.s.speak(x)
        for x in (A, B, C, D):
            self.s.speak(x)
        self.assertEqual(self.s.out, [A, B, C, A, B, C, D])

    def test_latency_is_speaking_time_even_after_rewind(self):
        """'처음 들린 시각'이 옛 문장 것이 아니어야 한다 (v0.4.6: 100초로 찍혀 말이 빨라졌다)."""
        self.s.seg.prime(self.s.text())
        for x in (A, B, A, B):
            self.s.speak(x)
        self.assertTrue(all(v < 12 for v in self.s.lat), self.s.lat)

    def test_revised_caption_does_not_flood_old_sentences(self):
        """자막이 마지막으로 읽은 문장을 고쳐 써도 옛 문장을 뭉치로 다시 내보내지 않는다 (v0.5.2: 지연 100초·세 번씩 읽음)."""
        self.s.seg.prime(self.s.text())
        for x in (A, B, C, D):
            self.s.speak(x)
        self.s.feed(30)                                   # 20초 넘게 지남
        self.s.box[-1] = "People can then reply with text, pictures and more."  # D 를 전혀 다르게 고쳐 씀
        self.s.speak("Now let us look at the next topic together.")
        for x in (A, B, C):
            self.assertEqual(self.s.out.count(x), 1, self.s.out)
        self.assertEqual(self.s.out[-1], "Now let us look at the next topic together.")
        self.assertLessEqual(len(self.s.out), 6)

    def test_box_replaced_starts_fresh_without_flood(self):
        self.s.seg.prime(self.s.text())
        for x in (A, B):
            self.s.speak(x)
        self.s.box[:] = ["Old one here now.", "Old two is here.", "Old three sits here.", "Old four stays here."]
        self.s.feed(5)
        self.s.speak(C)
        self.assertEqual(self.s.out, [A, B, C])

    def test_one_word_junk_is_dropped(self):
        """자막이 고쳐 쓰다 남긴 한 단어는 다음 문장 앞에 붙이지 않는다 (v0.5.1)."""
        self.s.seg.prime(self.s.text())
        self.s.speak("Crockett.")
        self.s.speak("You won't think you're buying something outright.")
        self.assertEqual(self.s.out, ["You won't think you're buying something outright."])

    def test_repeated_lead_in_is_not_doubled(self):
        self.s.seg.prime(self.s.text())
        self.s.speak("Let's dive.")
        self.s.speak("Let's dive into how your team of sellers is using video.")
        self.assertEqual(self.s.out, ["Let's dive into how your team of sellers is using video."])

    def test_short_sentence_joins_the_next(self):
        self.s.seg.prime(self.s.text())
        self.s.speak("And then.")
        self.s.speak("We assign the ticket to the billing group.")
        self.assertEqual(self.s.out, ["And then. We assign the ticket to the billing group."])


class ExtendTest(unittest.TestCase):
    """v0.5.12: 자막이 이미 내보낸 문장을 뒤로 늘리면 늘어난 부분도 읽는다."""

    def setUp(self):
        self.s = Scroll([OLD])
        self.s.seg.prime(self.s.text())
        self.addCleanup(self.s.close)

    def test_extended_sentence_tail_is_read(self):
        s = self.s
        s.feed(1)
        s.speak("You can filter the list at the top.")
        s.box.append("Open the menu and choose a line.")            # 자막이 여기서 마침표를 찍고 잠깐 멈춘다
        s.feed(3)
        s.box[-1] = "Open the menu and choose a line you are assigned to see only those calls."   # 말이 이어져 문장이 늘어남
        s.feed(3)
        s.speak("Then close the window when you are done.")
        self.assertEqual(s.out[1], "Open the menu and choose a line.", s.out)
        self.assertEqual(s.out[2], "You are assigned to see only those calls.")
        self.assertEqual(len(s.out), 4)

    def test_open_ended_sentence_waits(self):
        s = self.s
        s.feed(1)
        s.box.append("From the Zoom app, tap on.")                  # 'on' 으로 끝남 = 아직 말하는 중
        s.feed(3)
        self.assertEqual(s.out, [])
        s.box[-1] = "From the Zoom app, tap on the phone icon to see the subsections."
        s.feed(3)
        self.assertEqual(s.out, ["From the Zoom app, tap on the phone icon to see the subsections."])

    def test_same_sentence_not_reread(self):
        s = self.s
        s.feed(1)
        s.speak(A)
        s.box[-1] = A.replace("an end user.", "an end user")       # 마침표만 바뀌는 건 늘어난 게 아니다
        s.feed(3)
        s.box[-1] = A
        s.feed(3)
        self.assertEqual(len(s.out), 1)


class CleanupTest(unittest.TestCase):
    def test_strip_meta(self):
        """번역 뒤에 붙는 모델 설명을 뗀다 (v0.3.7)."""
        a = ("티켓 인터페이스에서 그룹과 담당자 필드가 표시되지만, 기본적으로 6개의 필드만 보이는 것을 확인하세요. "
             "정확한 이해를 위해 조금 더 명확한 표현이 필요할 수 있으나, 주어진 정보 내에서 가장 적합한 번역을 제공하였습니다.")
        self.assertEqual(lp.strip_meta(a), "티켓 인터페이스에서 그룹과 담당자 필드가 표시되지만, 기본적으로 6개의 필드만 보이는 것을 확인하세요.")
        b = '가장 적절한 번역은 다음과 같습니다: "드롭다운 필드를 사용하세요.'
        self.assertEqual(lp.strip_meta(b), "드롭다운 필드를 사용하세요.")
        c = "티켓 필드를 만들 준비가 됐습니다. 이제 하나 만들어 볼게요."
        self.assertEqual(lp.strip_meta(c), c)

    def test_fix_caption(self):
        self.assertEqual(lp.fix_caption("dial the E12 number"), "dial the Eatwell number")
        self.assertEqual(lp.fix_caption("from E12zendesk.com"), "from Eatwellzendesk.com")
        self.assertEqual(lp.fix_caption("Zenda Desk agent"), "Zendesk agent")
        self.assertEqual(lp.fix_caption("Step E120 here"), "Step E120 here")
        self.assertEqual(lp.fix_caption("dial the E12 number", "zoom"), "dial the E12 number")  # Eatwell 은 Zendesk 강의만

    def test_guess_topic(self):
        self.assertEqual(lp.guess_topic(["Welcome to Zoom Revenue Accelerator."]), "zoom")
        self.assertEqual(lp.guess_topic(["Open the ticket in Zendesk."], "zoom"), "zendesk")
        self.assertEqual(lp.guess_topic(["Wouldn't that be frustrating?"], "zoom"), "zoom")  # 안 나오면 하던 대로
        self.assertNotIn("Zendesk", lp.system_prompt("zoom"))
        # 11/12: Workvivo 강의는 Zoom 을 안 말해도 줌 강의로 (자막은 'Vivo' 로 적는다)
        self.assertEqual(lp.guess_topic(["Vivo isn't just a platform."], "general"), "zoom")
        self.assertEqual(lp.fix_caption("Vivo isn't just a platform.", "zoom"), "Workvivo isn't just a platform.")
        self.assertEqual(lp.fix_caption("Work Vivo helps.", "zoom"), "Workvivo helps.")
        self.assertEqual(lp.fix_caption("Viva la vivo", "zendesk"), "Viva la vivo")
        self.assertNotIn("Zendesk", lp.system_prompt("general"))

    def test_auto_terms(self):
        """v0.5.5: 용어를 강의에서 저절로 끌어온다."""
        c = lp.name_candidates
        self.assertEqual(c("We use Figma and Amazon Web Services every day."), ["Figma", "Amazon Web Services"])
        self.assertEqual(c("Then I opened it on Monday."), [])                 # 문장 첫 낱말·I·요일은 이름이 아니다
        self.assertEqual(c("Figma is great.", known=["Figma"]), ["Figma"])     # 아는 이름이면 문장 처음이어도
        self.assertEqual(c("Great. Tools are here."), [])
        t = "ZSA - Workvivo 2026 - Zoom Up Partner Learning Center"
        self.assertEqual(lp.fix_by_title("Vivo isn't just a platform.", t), "Workvivo isn't just a platform.")
        self.assertEqual(lp.fix_by_title("With Work Vivo you can.", t), "With Workvivo you can.")
        self.assertEqual(lp.fix_by_title("Work hard and Learn more at the Center.", t), "Work hard and Learn more at the Center.")
        self.assertEqual(lp.fix_by_title("Partners love it.", t), "Partners love it.")
        self.assertEqual(lp.foreign_names("Vivo isn't just a platform.", "Zendesk는 플랫폼이 아닙니다."), ["Zendesk"])
        self.assertEqual(lp.foreign_names("Open AI agents in Co-pilot.", "Copilot에서 AI agent를 엽니다."), [])
        p = lp.system_prompt("general", ["Figma"], "Figma 101")
        self.assertIn("Figma 101", p)
        self.assertIn("들린 이름: Figma", p)
        b = lp._BROWSER_TAIL
        self.assertEqual(b.sub("", "ZSA - Workvivo 2026 - Google Chrome"), "ZSA - Workvivo 2026")
        self.assertEqual(b.sub("", "Course - 프로필 1 - Microsoft Edge"), "Course")
        self.assertIsNone(b.search("동영상 동시 통역기"))
        tr = lp.LocalTranslator("x")
        tr.names = {"Workvivo": 3, "Activity Feed": 2, "Once": 1}
        self.assertEqual(tr.auto_terms(), ["Workvivo", "Activity Feed"])
        tr.banned.add("Activity Feed")                                          # 앱 창에서 ✕
        self.assertEqual(tr.lesson(), {"topic": "general", "title": "", "names": ["Workvivo"]})
        self.assertEqual(lp.LocalTranslator._clean("**Workvivo**는 `플랫폼`입니다."), "Workvivo는 플랫폼입니다.")

    def test_caption_fragments(self):
        """v0.5.8: 자막이 고쳐 쓰는 도중의 조각은 읽지 않는다."""
        seg = lp.CaptionSegmenter()
        got = [e for e, _ in seg._emit("Zoom has a strong history of audio technology for over a decade.", 0, 0)]
        self.assertEqual(len(got), 1)
        self.assertEqual(seg._emit("For over a decade.", 1, 1), [])                 # 앞 문장 끝을 되풀이
        self.assertEqual(seg._emit("The year that.", 2, 2), [])                     # 짧아서 붙여 둠
        self.assertEqual(seg._emit("Are integral to your.", 3, 3), [])              # 소유격으로 끝난 조각 → 붙여 둔 것도 버림
        self.assertIsNone(seg.carry)
        self.assertEqual(seg._emit("She wants to be a.", 4, 4), [])
        self.assertEqual([e for e, _ in seg._emit("I'm glad to hear that.", 5, 5)], ["I'm glad to hear that."])
        self.assertEqual([e for e, _ in seg._emit("It could be a deal breaker for us.", 6, 6)], ["It could be a deal breaker for us."])
        self.assertEqual(seg._emit("So you didn't.", 8, 8), [])
        self.assertEqual(seg._emit("PPX.", 8.5, 8.5), [])                           # 한 단어는 붙이지도 않는다
        self.assertTrue(lp.is_reply("The total cost of India.", "이해가 되지 않아요. 다시 말씀해 주시겠어요?"))
        self.assertFalse(lp.is_reply("Sorry, could you repeat that?", "죄송하지만 다시 말씀해 주시겠어요?"))
        self.assertFalse(lp.is_reply("The total cost of ownership is lower.", "총 소유 비용이 더 낮습니다."))
        seg.carry = None
        long = "Our Zoom Phone inherits the same reliable technology that has been integral to the success of."
        self.assertEqual([e for e, _ in seg._emit(long, 7, 7)], [long])            # 긴 문장은 끝이 끊겨도 읽는다

    def test_restore_names(self):
        """한글로 바뀐 제품 이름을 되돌린다 (v0.5.1). 원문에 있을 때만."""
        r = lp.restore_names
        self.assertEqual(r("Using Zoom Meetings and Zoom phone.", "줌 미팅과 줌 폰을 활용합니다."),
                         "Zoom Meetings과 Zoom Phone을 활용합니다.")
        self.assertEqual(r("Zoom Revenue Accelerator gives leaders insights.", "Zoom 리테일러 가속기는 리더들에게 통찰력을 제공합니다."),
                         "Zoom Revenue Accelerator는 리더들에게 통찰력을 제공합니다.")
        self.assertEqual(r("Zendesk Copilot helps agents.", "젠데스크 부조종사가 상담원을 돕습니다."),
                         "Zendesk Copilot가 상담원을 돕습니다.")
        self.assertEqual(r("Give them a call.", "전화를 줌으로 연결하세요."), "전화를 줌으로 연결하세요.")  # 원문에 Zoom 없음
        self.assertEqual(r("Zoom in on the chart.", "차트를 확대해 줌니다."), "차트를 확대해 줌니다.")  # 낱말 속 '줌'은 그대로


if __name__ == "__main__":
    unittest.main()

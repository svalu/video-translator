"""1단계: 윈도우 한국어 음성(Microsoft Heami)이 문장을 읽는지 확인.

실행: .venv\\Scripts\\python.exe step1_speak.py
"""
import pyttsx3

engine = pyttsx3.init()

print("설치된 음성:")
for v in engine.getProperty("voices"):
    print(" -", v.name)
    if "Heami" in v.name:
        engine.setProperty("voice", v.id)

engine.setProperty("rate", 190)  # 말 빠르기. 기본 200 근처, 클수록 빠름

engine.say("안녕, 희랑. 이제부터 영어 강의를 한국어로 읽어 줄게.")
engine.say("티켓은 고객 문의 한 건이고, 트리거는 조건에 맞으면 바로 실행되는 규칙이야.")
engine.runAndWait()
print("끝")

"""어디에 무엇을 두는지.
  RES  = 읽기만 하는 것 (화면 ui/, 아이콘). exe 로 묶이면 exe 안쪽 폴더.
  DATA = 쓰는 것 (설정, 기록, 임시 소리). exe 로 묶이면 %LOCALAPPDATA%\\VideoTranslator — Program Files 엔 못 쓰니까.
묶이지 않았을 땐 둘 다 이 폴더 (예전과 같음).
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)
_SRC = os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", _SRC) if FROZEN else _SRC
DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VideoTranslator") if FROZEN else _SRC
os.makedirs(DATA, exist_ok=True)

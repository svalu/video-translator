# 드롱용: 회사 PC 동영상 번역기를 최신판으로 (처음 한 번)

> 희랑이 "동영상 번역기 업데이트해줘"라고 하면 이 문서대로 **드롱이 직접** 한다.
> 희랑에게 명령어를 넘기지 않는다 ("파워쉘은 알아서 해줘" — 2026-11-08 다시 들음).
> 되돌릴 수 있는 건 묻지 말고 하고, 결과만 짧게 말한다.

## 왜 '처음 한 번'이 필요한가
v0.3(2026-11-08) 부터 앱이 켜질 때 GitHub 최신과 비교해서 **'새 버전 나왔어 · 업데이트' 띠**를 띄우고,
누르면 `업데이트.bat` 이 알아서 받고·빌드하고·다시 켠다. 그런데 회사 PC 에 있는 예전 판(v0.2 이전)엔
이 알림도 `업데이트.bat` 도 없다. 그래서 **딱 한 번만** 드롱이 손으로 받아 와야 한다. 그 뒤론 앱이 알아서 한다.

## 0. 폴더 찾기
README 기록상 회사 PC 는 `E:\ai heerang\Video_Translator`. 다르면 `git remote -v` 가
`github.com/svalu/video-translator` 인 폴더를 찾는다. 아래에서 `$repo` 로 부른다.

```powershell
$repo = "E:\ai heerang\Video_Translator"
git -C $repo remote -v
```

## 1. 떠 있는 앱 끄기 (exe 를 덮어써야 하니까)
```powershell
taskkill /im VideoTranslator.exe /f 2>$null
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object CommandLine -match 'app.py' | ForEach-Object { Stop-Process -Id $_.ProcessId }
```

## 2. 회사 PC 에만 있는 것 지키기 (지우지 말고 보관)
```powershell
git -C $repo status --short
git -C $repo fetch origin
git -C $repo log --oneline origin/main..main   # 회사 PC 에만 있는(안 올린) 커밋
```
- **커밋 안 한 수정**이 있으면 → `git -C $repo stash push -u -m "회사PC 업데이트 전 보관 (날짜)"`
- **안 올린 커밋**이 있으면 → `git -C $repo branch 회사-보관-YYMMDD` 로 빼 두고 `git -C $repo reset --hard origin/main`
  (커밋은 그 브랜치에 남는다). 나중에 희랑한테 "회사 PC 에만 있던 수정 N개를 `회사-보관-…` 에 빼 뒀어"라고 알린다.
- 둘 다 없으면 그냥 다음으로.

## 3. 받기 + 빌드 + 바로가기 + 다시 켜기
```powershell
git -C $repo pull --ff-only
if (-not (Test-Path "$repo\.venv\Scripts\python.exe")) { py -3.14 -m venv "$repo\.venv" }
Set-Location $repo
.venv\Scripts\python.exe update.py
```
`update.py` 가 패키지 맞추기 → 앱 끄기 → exe 빌드(1분쯤) → 바탕화면·시작 메뉴 바로가기 → 새 exe 켜기 를 한다.

⚠ 드롱 셸에서 하지 말 것:
- `cmd /c 업데이트.bat` — PowerShell → cmd 로 넘기면 **한글 파일 이름이 깨져서** 못 찾는다. 그래서 `update.py` 를 직접 부른다.
- `Start-Process ... -Wait` — PowerShell 이 새로 켜진 앱까지 '자식'으로 보고 기다리거나 같이 정리해서 **앱이 사라진 것처럼 보인다**.
  실제로는 멀쩡하다. 확인은 4번처럼 한다.

## 4. 확인 (완료라고 말하기 전에)
```powershell
"빌드 커밋: " + (Get-Content "$repo\dist\VideoTranslator\build_commit.txt") + " / HEAD: " + (git -C $repo rev-parse HEAD)
Get-Process VideoTranslator | Where-Object MainWindowTitle | Select-Object Id, MainWindowTitle
$r = Invoke-WebRequest http://127.0.0.1:8765/api/state -UseBasicParsing
([Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json) | Select-Object version, ear, @{n='새버전띠';e={$_.update.available}}
```
- 빌드 커밋 = HEAD (뒤에 `dirty` 없음)
- 창 제목 '동영상 번역기' 가 떠 있음
- `version` 이 GitHub 의 `version.py` 와 같음, `ear` = **captions** (회사 PC 는 윈11 → 실시간 자막을 귀로), 새버전띠 = False

## 5. 설정 옮기기 (필요할 때만)
exe 는 설정을 `%LOCALAPPDATA%\VideoTranslator\settings.json` 에 둔다. 예전에 `python app.py` 로만 쓰던 PC 라
설정이 저장소 폴더(`$repo\settings.json`, `$repo\.overlay.json`)에만 있으면, LOCALAPPDATA 쪽에 **없을 때만** 복사한다.
```powershell
$data = "$env:LOCALAPPDATA\VideoTranslator"; New-Item -ItemType Directory -Force $data | Out-Null
foreach ($f in "settings.json", ".overlay.json") { if ((Test-Path "$repo\$f") -and -not (Test-Path "$data\$f")) { Copy-Item "$repo\$f" "$data\$f" } }
```

## 6. 기록하고 희랑에게 한두 줄
```powershell
powershell -NoProfile -File "H:\AI by heerang\mdlog.ps1" -Who "회사드롱" -Title "회사 PC 동영상 번역기 최신판으로" -Body "버전·커밋, 보관한 것(있으면), 확인 결과"
```
(회사 PC 에 mdlog.ps1 경로가 다르면 그 PC 의 CLAUDE.md 에 적힌 경로로.)

희랑에게: "회사 PC 동영상 번역기 vX 로 올렸어. 이제부터 새 버전 나오면 앱 위에 띠가 뜨니까 누르기만 하면 돼." + 보관한 게 있으면 그 사실.

## 회사 PC 에서 알아 둘 것
- 윈11 이라 귀는 '자동' = 윈도우 실시간 자막(예전과 같은 방식). Whisper·GPU 부품(2GB)은 필요 없다.
- 준비 점검의 'Whisper' 줄이 주황(!)으로 보여도 정상 — 실시간 자막이 있으니 안 쓴다.
- 화면 번역(Ctrl+Shift+T), 새 아이콘(말풍선 A→가), '만든 사람 · 희랑' 꼬리말은 그대로 같이 온다.
- 막히면 이 문서와 README '실험 기록'(11/08 항목들)에 원인·함정이 있다.

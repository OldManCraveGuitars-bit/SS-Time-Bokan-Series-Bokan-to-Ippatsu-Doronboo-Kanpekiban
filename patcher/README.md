# Windows 패처

배포 페이지의 `Bokan-KR-Patcher-V1.0.exe`를 실행하면 Python 설치 없이 사용할 수 있습니다. 패치와 원본 트랙 해시 목록은 EXE 안에 포함되어 있습니다.

소스로 실행하려면 Python 3.10 이상과 Tkinter가 필요합니다. 아래 명령은 이 저장소의 루트에서 실행합니다.

```powershell
python patcher/bokan_patcher.py
```

명령행 적용:

```powershell
python patcher/bokan_patcher.py --cue "D:\ROM\original.cue" --output "D:\ROM\Bokan-KR"
```

`--output`은 **아직 존재하지 않는 새 폴더**여야 합니다. 원본 40개 트랙을 검증하고, 한글판 데이터 트랙과 원본 그대로인 39개 음악 트랙을 복사합니다. 결과 CUE는 상대 경로를 사용하므로 출력 폴더 전체를 함께 이동할 수 있습니다.

`--verify-only`는 파일을 만들지 않고 원본·패치·결과를 검증합니다. `--report result.json`은 UTF-8 JSON 결과를 저장합니다. EXE도 같은 명령행 인수를 지원합니다.

EXE 빌드에는 PyInstaller가 필요합니다. PowerShell에서 저장소 루트 기준으로:

```powershell
python -m pip install pyinstaller
$patchRoot = (Resolve-Path patch).Path
python -m PyInstaller --noconfirm --onefile --windowed --name Bokan-KR-Patcher-V1.0 --add-data "$patchRoot\Bokan-KR-V1.0.bps;." --add-data "$patchRoot\manifest.json;." patcher/bokan_patcher.py
```

직접 빌드한 EXE는 빌드 환경에 따라 배포 EXE와 해시가 달라질 수 있습니다. 게임 결과의 해시는 동일해야 합니다.

BPS 구현은 [byuu의 공개 사양](https://github.com/Sir-Walrus/Flips/blob/master/bps_spec.md)을 따릅니다. 배포 패치를 Flips v198로 적용한 결과도 동일한 최종 Track 01 해시로 확인했습니다.

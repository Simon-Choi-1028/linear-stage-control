<p align="center">
  <a href="https://www.kitech.re.kr/" title="KITECH">
    <img src="https://www.kitech.re.kr/images/sub/signature_v.svg" width="128" height="90" alt="KITECH">
  </a>
</p>

<p align="center">
  <a href="https://www.baslerweb.com/en-us/software/pylon-software-suite/" title="Basler pylon Software Suite">
    <img src="https://www.baslerweb.com/favicon.svg" width="36" height="36" alt="Basler">
  </a>
  &nbsp;&nbsp;
  <a href="https://www.zaber.com/software" title="Zaber Software">
    <img src="https://www.zaber.com/favicon.ico" width="36" height="36" alt="Zaber">
  </a>
</p>

<h1 align="center">Basler ace 2 + Zaber XY Stage Control</h1>

<p align="center">
  Basler ace 2 카메라와 Zaber Ultra Precision Linear Motor XY 스테이지를 연동하는 optical calibration capture GUI
</p>

<p align="center">
  <a href="https://github.com/Simon-Choi-1028/linear-stage-control/releases/latest">
    <img src="https://img.shields.io/github/v/release/Simon-Choi-1028/linear-stage-control?label=release" alt="latest release">
  </a>
  <a href="https://github.com/Simon-Choi-1028/linear-stage-control/releases/latest/download/LinearStageControl-Portable.zip">
    <img src="https://img.shields.io/badge/download-portable%20zip-2f8f68" alt="download portable zip">
  </a>
</p>

## 빠른 다운로드

| 구분 | 다운로드 | 용도 | 포함 항목 |
| --- | --- | --- | --- |
| Online installer | [LinearStageControlSetup.exe](https://github.com/Simon-Choi-1028/linear-stage-control/releases/latest/download/LinearStageControlSetup.exe) | 일반 Windows 설치 및 자동 업데이트 | 앱, Zaber Motion Library, Zaber Device Database |
| Update manifest | [update_manifest.json](https://github.com/Simon-Choi-1028/linear-stage-control/releases/latest/download/update_manifest.json) | 설치기 SHA256 검증 | 설치기 hash, 크기, 버전 |
| Portable ZIP | [LinearStageControl-Portable.zip](https://github.com/Simon-Choi-1028/linear-stage-control/releases/latest/download/LinearStageControl-Portable.zip) | 설치기 EXE 없이 압축 해제 후 실행 | 앱, Zaber Motion Library, Zaber Device Database |
| Portable manifest | [portable_manifest.json](https://github.com/Simon-Choi-1028/linear-stage-control/releases/latest/download/portable_manifest.json) | portable ZIP SHA256 검증 | ZIP 파일 hash, 크기, 진입점 |

- Latest published release: [GitHub Releases](https://github.com/Simon-Choi-1028/linear-stage-control/releases/latest)
- Current source candidate: `v0.1.13`
- Release notes: [CHANGELOG.md](CHANGELOG.md)
- Basler pylon 다운로드: [pylon Software Suite](https://www.baslerweb.com/en-us/software/pylon-software-suite/)
- Zaber SDK/도구 다운로드: [Zaber Software](https://www.zaber.com/software), [Zaber Motion Library Docs](https://software.zaber.com/motion-library/docs)

Basler ace 2 `a2A2464-115g5mBAS` 카메라와 Zaber XY 스테이지를 연동해, 사전에 입력한 위치마다 이동 완료 후 카메라 캡처를 수행하고 데이터셋으로 저장하는 기본 환경입니다.

## 실행 흐름

1. `config.yaml`에서 Zaber COM 포트, X/Y 축 매핑, 카메라 설정, 위치 목록을 지정합니다.
2. 스크립트가 XY 스테이지를 지정 위치로 이동시킵니다.
3. 두 축이 idle 상태가 되고 `settle_s`만큼 안정화 대기합니다.
4. Basler 카메라에 software trigger 캡처 명령을 보냅니다.
5. 캡처가 완료되면 원본 이미지를 저장하고 metadata를 기록합니다.
6. 다음 위치로 반복합니다.

## 개발환경

- Python 3.13 가상환경: `.venv`
- Basler Python SDK: `pypylon==26.4.1`
- Zaber Motion Library: `zaber-motion==9.3.0`
- Zaber official Device Database: `sdk_downloads/zaber/devices-public-v2.sqlite`
- Basler pylon Runtime은 기본 slim installer에 포함하지 않습니다. 앱 실행 후 장비 연결 실패 시 pylon Runtime 다운로드 안내 버튼을 사용합니다.
- 오프라인 배포가 필요할 때만 `packaging\build_windows.ps1 -IncludePylonRuntime`으로 Runtime 포함 빌드를 만듭니다.

개발용 검사 도구는 optional dependency로 분리되어 있습니다.

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format .
python -m pyright
```

`pyright`는 처음부터 strict가 아니라 `camera.py`, `stage.py`, `dataset.py`, `scan.py`, `position_validation.py` 같은 핵심 로직과 공통 예외/로그 모듈을 basic mode로 검사합니다.

## 기본 명령

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\launch_gui.py
python scripts/list_devices.py
python scripts/xy_scan_capture.py --config config.yaml --dry-run
python scripts/xy_scan_capture.py --config config.yaml
python scripts/capture_manual_screenshots.py
```

장비 연결 후 `python scripts/list_devices.py`에서 Basler 카메라와 Zaber COM 포트가 보이는지 먼저 확인하세요.
매뉴얼용 UI 이미지는 `python scripts/capture_manual_screenshots.py`로 `%USERPROFILE%/Downloads/LinearStageControl_UI_Screenshots/`에 일괄 저장할 수 있습니다.

## GUI

GUI 실행은 아래 명령을 사용합니다.

```powershell
python scripts\launch_gui.py
```

GUI는 한국어 UI로 구성되어 있으며 장비 새로고침, 위치 직접 입력, CSV/TSV/TXT/JSON/JSONL/YAML/XLSX 위치 import, CSV export, 데이터셋 저장 위치 선택, 촬영 시작/중지, 촬영 이미지 미리보기, 캡처 목록 확인을 할 수 있습니다. 촬영 전 점검에는 준비, 원점 복귀, 이동, 위치 확인, 안정화, 촬영, 저장/UI를 포함한 예상 소요시간이 표시되고, 촬영 중에는 `진행` 영역에 연결, 원점 복귀, 위치 이동, 안정화 대기, 촬영, 저장 같은 현재 단계와 완료 개수, 경과 시간, 남은 시간, 예상 종료시각이 표시됩니다. 오른쪽 `촬영 목록`은 장시간 run 중 메모리를 보호하기 위해 최근 1,000개만 표시하며, 전체 촬영 기록은 run 폴더의 `captures.csv` 하나에 연속 저장됩니다. 촬영된 이미지는 `촬영 목록`을 클릭하면 다시 볼 수 있고, 선택한 사진의 target/actual 위치와 um 단위 오차는 미리보기 아래의 표 칸에 나뉘어 표시됩니다.

위치 테이블은 Zaber 210 mm XY 스테이지 이동 범위 기준으로 `0-210 mm`를 즉시 검사합니다. 각 행에는 `X`, `Y`뿐 아니라 선택 입력인 `속도 mm/s`, `캡쳐 수`를 넣을 수 있습니다. 속도나 캡쳐 수를 비우면 촬영 설정의 이동속도와 기본 캡쳐 수를 사용합니다. 범위를 벗어난 X/Y 셀은 빨간색, 중복 좌표나 빈 라벨 같은 확인 항목은 노란색으로 표시되며, 테이블 아래 상태 줄에 오류/경고 요약이 표시됩니다.

`시작`을 누르면 촬영 전 점검 창이 먼저 열립니다. 이 창은 위치 범위, Basler 카메라 감지 여부, Zaber COM 포트, X/Y 축 매핑 중복, 저장 폴더 생성 가능 여부, 이동 속도, 촬영 설정, 고정 오차 기준을 한 번에 확인합니다. 오류가 있으면 시작 버튼이 비활성화되고, 경고만 있을 때는 사용자가 내용을 확인한 뒤 계속 진행할 수 있습니다.

촬영 로그 형식은 `captures.csv`로 고정되어 GUI에서 다른 형식을 선택하지 않습니다. 각 캡처 행에는 위치·오차·시간 정보와 함께 카메라 모델/serial, PixelFormat, 노출, Gain, FrameRate, ROI, Gamma, Black Level, Binning/Decimation, trigger, 회전/반전 등 해당 프레임에 적용된 카메라 파라미터가 기록됩니다. 실행 옵션에는 `소프트웨어 트리거`, `NPY 저장`, `원점 복귀 생략`, `카메라 180도 반전`, `좌우 반전`, `상하 반전`이 들어갑니다.

화면에 보이는 숫자는 불필요한 0을 줄여 표시합니다. mm 위치값은 최대 4자리, um 오차값은 최대 2자리까지만 보여주며, `captures.csv`에는 계산 원본 값이 그대로 저장됩니다.

창 폭이 좁아지면 좌우 분할 레이아웃이 위아래 배치로 자동 전환되며, 왼쪽 제어 영역은 스크롤 가능합니다. 이미지 미리보기는 `전체화면 보기` 버튼 또는 이미지 더블클릭으로 확대 창을 열 수 있고, 확대 창에서는 화면 맞춤, 100%, 확대, 축소를 사용할 수 있습니다.

노출, 안정화, 이동속도, 기본 캡쳐 수는 키보드로 직접 입력할 수 있는 값 입력칸을 유지하면서, 오른쪽에 빠른 증감 버튼을 둔 가로형 조정 UI를 사용합니다. 안정화 시간은 GUI에서 `ms` 또는 `s` 단위로 입력할 수 있고, config에는 기존 호환을 위해 `stage.settle_s` 초 단위로 저장합니다. 노출은 `-1000 -100 -10 +10 +100 +1000`, 안정화와 이동속도는 `-100 -10 -5 +5 +10 +100`, 기본 캡쳐 수는 `-100 -10 -1 +1 +10 +100` 단위로 조정할 수 있습니다.

`카메라 파라미터` 영역에서는 노출 외에 Gain, FrameRate, Width/Height, Offset X/Y, Gamma, Black Level, Binning X/Y, Decimation X/Y를 선택 입력할 수 있습니다. 빈 항목은 카메라 현재값을 유지하고, 연결된 Basler 모델이 지원하지 않는 GenICam 항목은 촬영 로그와 preflight 경고로 남깁니다.

주요 조작 버튼과 값 입력칸에는 툴팁을 붙여 파일 열기/저장, 장비 새로고침, CSV import/export, 실행/중지, 전체화면 확대, 각 파라미터의 의미와 기본값을 빠르게 확인할 수 있게 했습니다.

`장비` 영역의 `자동검색` 버튼을 누르면 LAN/GigE Basler 카메라를 한 번 검색합니다. 검색 상태는 별도 배지로 `탐색중`, `성공`, `실패`가 표시되고 상태별 색상으로 구분됩니다. `a2A2464-115g5mBAS`처럼 LAN/GigE 포트에 연결되는 카메라가 감지되면 `카메라` 드롭다운에 모델명, serial, IP, device class가 추가되어 선택할 수 있습니다. GUI가 주기적으로 카메라 검색을 반복하지 않으므로 촬영 중 pypylon 장치 열기와 충돌할 가능성을 줄입니다.

`수동 스테이지` 영역에서는 촬영 run을 시작하기 전에 현재 위치 읽기, 활성 축 원점 복귀, 절대 좌표 이동, X/Y jog 이동, 수동 정지를 실행할 수 있습니다. 원점 복귀 버튼은 home 완료 후 기본으로 X=105 mm, Y=105 mm 위치까지 50 mm/s로 이동합니다. 촬영 run 중에는 별도 serial connection 충돌을 막기 위해 수동 명령 버튼이 비활성화됩니다.

오른쪽 `진단` 탭은 pypylon import, Basler 카메라 탐색, Zaber COM 포트, Zaber Device Database, 저장 폴더 쓰기 권한, GitHub 업데이트 접근성을 한 번에 점검합니다. 현장 PC에서 live가 뜨지 않거나 장비 연결이 애매할 때 먼저 실행해 결과를 로그와 표로 확인하면 됩니다.

GUI의 실행 상태는 `AppRunState` enum으로 관리합니다. 촬영, 취소, 카메라 탐색, live preview, 진단, 수동 스테이지, 업데이트 다운로드 상태에 따라 버튼 활성화는 `apply_state()`에서 한 번에 정리합니다. 하드웨어/파서/저장/업데이트 오류는 공통 예외 계층을 거쳐 GUI에는 사용자용 메시지만 표시하고, 개발자용 상세 정보는 로그에 남깁니다.

현장 디버깅 로그는 JSONL 형식으로 아래 경로에 저장됩니다.

```text
%USERPROFILE%/Documents/LinearStageControl/logs/
  app_YYYYMMDD.log
  run_<run_id>.log
```

v0.1.4부터 카메라 연결 성공 후 `TriggerMode Off` 상태의 continuous live grabbing 세션이 자동으로 시작됩니다. Live worker는 Basler `GrabStrategy_LatestImageOnly` 방식으로 첫 프레임 대기, 수신 중, 오류 상태를 구분 표시합니다. v0.1.5부터 Live 상태에는 설정값이 아니라 실제 rolling FPS가 표시되고, 노출/Gain/Gamma/Black Level/FrameRate는 Live 중 debounce update로 즉시 반영됩니다. 실험 장치의 장착 방향을 보정하기 위해 `camera.rotate_180` 기본값은 `true`이며, 필요하면 `camera.flip_horizontal`, `camera.flip_vertical`로 좌우/상하 반전을 추가할 수 있습니다. Live preview와 scan run 저장 PNG/NPY에는 같은 회전/반전 보정이 적용되지만, 무수정 단일 `Live 캡처` 파일에는 software rotation/flip을 적용하지 않습니다. `Live 캡처`는 미리보기 위젯 크기, 줌, 격자, 중앙선과 무관하게 현재 전체 센서 프레임을 원본 가로·세로 크기로 `live_captures` 폴더에 PNG로 저장하고 촬영 목록에 추가합니다. 저장된 이미지를 보고 있는 중에도 `Live 보기`로 즉시 실시간 영상으로 돌아갈 수 있고, 이때 preview zoom/center는 전체 frame fit으로 초기화됩니다. 미리보기 화면은 기본 4:3 frame으로 표시되며, 우하단 resize handle을 대각선으로 끌어 가로/세로 크기를 조정하고 더블클릭으로 기본 4:3 크기로 되돌릴 수 있습니다. 짧은 창에서는 preview panel 내부 스크롤을 사용해 Live 컨트롤과 검사 도구가 카메라 화면 위로 겹치지 않도록 합니다. Live 시작과 첫 프레임의 원본 크기, preview 크기, 렌더 target 크기는 JSONL 로그에 남습니다. 미리보기에는 100-800% 디지털 확대, 클릭 지점 중심 이동, 얇은 흰색 4x4 격자 오버레이, 얇은 흰색 중앙 가로/세로선 표시를 적용할 수 있습니다. 촬영 run을 시작할 때는 live worker를 먼저 정지해 실제 software trigger 캡처와 카메라 점유가 충돌하지 않도록 합니다.

단일 `Live 캡처` 저장은 `grab_result.GetArray()`를 SDK buffer 해제 전에 분리한 센서 순서의 NumPy 배열을 lossless PNG로 기록합니다. 따라서 원본 shape, dtype, 픽셀 값이 유지되며 software rotation/flip과 미리보기의 8-bit 명암 변환, resize, zoom/crop, grid/cross overlay는 저장 파일에 적용되지 않습니다.

Zaber 축은 `X축 사용`, `Y축 사용` 체크박스로 독립 제어할 수 있습니다. X만 또는 Y만 연결된 현장에서는 비활성 축에 대해 home/move/position 명령을 보내지 않으며, 비활성 축 좌표가 위치 목록 안에서 여러 값으로 바뀌면 preflight 오류로 막습니다. 단일축 run의 비활성 축 actual/error metadata는 빈 값으로 저장됩니다.

체인 연결된 단축 X/Y 스테이지는 `X=device_index 0, axis_number 1`, `Y=device_index 1, axis_number 1`로 설정합니다. 하나의 2축 컨트롤러를 쓰는 구성만 `Y=device_index 0, axis_number 2`를 사용합니다.

## Calibration Error

이 장비는 optical calibration 용도이므로 각 캡처마다 um 단위 오차 예측치를 저장하고 GUI에 표시합니다. Zaber 210 mm LDM/X-LDM-AE crossed XY 스테이지의 제조사 스펙을 고정값으로 사용하며, GUI에서 임의로 수정하지 않습니다.

Zaber 공식 스펙 기준값:

```text
unidirectional accuracy = 1.00 um
repeatability           = < 0.08 um
horizontal runout       = < 5.00 um
vertical runout         = < 8.00 um

xy_axis_worst_case_um   = 1.00 + 0.08 + 5.00 = 6.08 um
xy_radial_worst_case_um = sqrt(6.08^2 + 6.08^2) = 8.60 um
```

```text
predicted_max_error_um =
  max(measured_radial_error_um, xy_radial_worst_case_um)

predicted_min_error_um =
  max(0, measured_radial_error_um - xy_radial_worst_case_um)
```

`measured_radial_error_um`은 Zaber가 보고한 실제 위치와 목표 위치의 차이를 um로 환산한 값입니다. `config.yaml`의 `calibration` 항목은 저장용 스냅샷이며 계산은 코드의 고정 제조사 스펙을 사용합니다.

```yaml
calibration:
  stage_model: Zaber LDM/X-LDM-AE 210 mm crossed XY
  source_url: https://www.zaber.com/products/linear-stages/X-LDM-AE/specs
  travel_range_mm: 210.0
  stage_accuracy_um: 1.0
  stage_repeatability_um: 0.08
  horizontal_runout_um: 5.0
  vertical_runout_um: 8.0
  xy_axis_worst_case_um: 6.08
  xy_radial_worst_case_um: 8.598418459
  max_allowed_um: 8.598418459
```

GUI의 `Error` 탭에는 캡처별 오차 예측 범위가 캔들차트로 표시됩니다. 캔들의 wick은 `predicted_min_error_um`부터 `predicted_max_error_um`까지의 범위이고, body는 측정 radial error에서 최대 예측 오차까지의 구간입니다. 전체 최대/평균, 허용 한계, 제한값 초과 개수는 요약 표로 표시됩니다. `captures.csv`에도 `measured_error_x_um`, `measured_error_y_um`, `measured_radial_error_um`, `predicted_min_error_um`, `predicted_max_error_um`, `predicted_x_min_um`, `predicted_x_max_um`, `predicted_y_min_um`, `predicted_y_max_um`, `within_error_threshold`가 저장됩니다.

제조사 스펙은 사용자가 매번 조작할 값이 아니므로 왼쪽 제어 패널에는 상시 표시하지 않습니다. 필요한 경우 `오차` 탭의 `스펙 보기` 버튼에서 고정값 표를 확인할 수 있습니다.

## 위치 입력

`config.yaml`의 `scan.positions`에 직접 입력할 수 있습니다.

```yaml
scan:
  default_capture_count: 1
  estimated_setup_s: 2.0
  estimated_home_s: 8.0
  estimated_default_move_velocity_mm_s: 10.0
  estimated_move_overhead_s: 0.10
  estimated_position_overhead_s: 0.10
  estimated_capture_trigger_overhead_s: 0.05
  estimated_capture_overhead_s: 0.35
  estimated_npy_save_overhead_s: 0.15
  estimated_ui_capture_overhead_s: 0.03
  positions:
    - label: origin
      x_mm: 0
      y_mm: 0
    - label: sample_a
      x_mm: 0.5
      y_mm: 0
      move_velocity_mm_s: 25
      capture_count: 3
```

파일로 위치를 넘기려면 `scan.positions_file`에 경로를 넣습니다. 지원 포맷은 CSV, TSV, TXT, JSON, JSONL, YAML, XLSX입니다. 컬럼명은 엄격하게 하나로 고정하지 않고 `label/name/id`, `x/x_mm/X mm/target_x_mm`, `y/y_mm/Y mm/target_y_mm`, `velocity/move_velocity_mm_s/speed_mm_s`, `capture_count/captures/frames` 같은 형태를 인식합니다.

```yaml
scan:
  positions_file: positions.example.csv
```

GUI의 `선형 경로` 버튼은 시작 X/Y, 끝 X/Y를 받아 직선 보간 위치 목록을 생성합니다. 생성 기준은 `간격/캡쳐` 또는 `위치 수` 중 선택할 수 있고, 간격은 `mm` 또는 `μm` 단위로 입력할 수 있습니다. 간격 기준은 경로를 따라 지정한 거리마다 한 위치를 만들고 끝점은 항상 포함합니다. 모달 안의 2D 미니맵은 생성될 점, 진행 방향, 총 거리, 간격, 위치 수, 예상 캡쳐 수를 입력 변경 즉시 보여줍니다. 생성된 행에도 위치별 이동속도와 캡쳐 수를 넣을 수 있고, 비워 두면 기본 촬영 설정을 따릅니다.

`positions`와 `positions_file`이 모두 비어 있고 `scan.linear_path`가 설정되어 있으면 선형 경로가 생성됩니다. `scan.linear_path.spacing_mm` 또는 `spacing_um`이 있으면 간격 기준을 사용하고, 없으면 `count` 기준을 사용합니다. 두 간격 키가 모두 있으면 `spacing_mm`을 우선합니다. 세 항목이 모두 비어 있으면 기존 grid 설정이 fallback으로 사용됩니다.

GUI에서 위치 파일을 불러오면 같은 범위 검사가 즉시 적용됩니다. 촬영 run은 `0-210 mm` 범위를 벗어난 위치가 하나라도 있으면 시작되지 않습니다. 파일이 비어 있거나 X/Y 컬럼을 찾을 수 없거나 숫자로 해석할 수 없는 값이 있으면 해당 행 번호와 함께 오류를 냅니다.

## 저장 구조

기본 저장 위치는 `%USERPROFILE%/Documents/LinearStageControl/datasets/<run_timestamp>/`입니다.

```text
%USERPROFILE%/Documents/LinearStageControl/datasets/20260526T142233_123456+0900/
  dataset_manifest.json
  manifest.json
  config.yaml
  captures.csv
  images/
    X000.000_Y000.000.png
```

`captures.csv`에는 target 위치, actual 위치, 위치 오차, 위치별/실제 적용 이동속도, 위치 내 캡쳐 순번, um 단위 오차 예측, 이동 시작/완료 timestamp, settle 완료 timestamp, capture 명령/완료 timestamp, 카메라 timestamp, 이미지 파일명과 경로가 기록됩니다. 각 행의 `camera_*` 열에는 캡처 직전에 실제 카메라에서 읽은 파라미터와 앱에서 적용한 trigger·회전·반전 설정이 함께 저장됩니다. 이미지 파일명은 기본적으로 `X000.000_Y000.000.png` 형태로 저장됩니다. X/Y는 소수점 3자리까지 반올림 없이 절삭되며, 같은 좌표에서 여러 장을 촬영하거나 중복 좌표가 있으면 `_C02`, `_P0002` 같은 suffix가 필요한 경우에만 붙습니다. `dataset_manifest.json`에는 앱 버전, record 수와 주요 산출물 경로가 저장되며, `dataset.manifest_detail: full`일 때는 파일별 크기와 SHA256 hash도 메모리에 누적하지 않고 순차 기록합니다. 기존 자동화와 호환되도록 같은 내용의 `manifest.json`도 함께 생성합니다.

## 원본 이미지

캡처는 `grab_result.GetArray().copy()`로 받은 변환 전 배열을 lossless PNG로 저장합니다. 완전한 numpy 배열도 함께 보존하려면 `config.yaml`에서 아래처럼 설정합니다.

```yaml
dataset:
  save_numpy: true
```

## 주요 설정

```yaml
camera:
  # 여러 Basler 카메라가 연결된 경우 선택 필터로 사용할 수 있습니다.
  model_name:
  device_class:
  # Auto로 두면 현재 카메라 픽셀 포맷을 유지합니다.
  pixel_format: Mono8
  pixel_format_candidates: [Mono8, Mono10, Mono12, Mono16, BayerRG8, BayerGB8, BayerGR8, BayerBG8, RGB8, BGR8]
  use_software_trigger: true
  trigger_selector: FrameStart
  trigger_source: Software
  exposure_us: 5000
  timeout_ms: 5000
  rotate_180: true
  flip_horizontal: false
  flip_vertical: false
  live_preview:
    enabled: true
    fps: 10

stage:
  serial_port: COM3
  settle_s: 0.2
  move_velocity_mm_s:
  use_bundled_device_db: true
  device_db_path:
  axes:
    x: {enabled: true, device_index: 0, axis_number: 1}
    y: {enabled: true, device_index: 1, axis_number: 1}

updates:
  enabled: true
  repo: Simon-Choi-1028/linear-stage-control
```

`a2A2464-115g5mBAS`는 LAN/GigE 카메라이므로 NIC IP 대역, jumbo frame, packet size 설정이 중요합니다. pylon IP Configurator와 GigE Configurator는 `C:\Program Files\Basler\pylon` 아래에 설치되어 있습니다.

구현 중 정리한 제어/저장 규칙은 [rules.md](rules.md)에 기록했습니다.

## Windows 빌드와 설치

Python이 없는 다른 PC에서 실행할 수 있도록 PyInstaller portable 빌드를 만들 수 있습니다. PyInstaller는 실행 환경이 아니라 `build` optional dependency이며, 아래 스크립트가 자동으로 설치합니다.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

Zaber 공식 Motion Library wheel과 Device Database를 명시적으로 내려받아 로컬 SDK cache를 만들려면 아래 명령을 먼저 실행합니다. 기본 build는 Device DB가 없을 때 이 다운로드를 자동으로 시도합니다.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\download_zaber_sdk.ps1
```

빌드가 끝나면 `LinearStageControl.exe --smoke-test`를 실행해 PyInstaller 패키지 안의 Python 모듈, pypylon, Zaber native DLL 로딩을 실제로 확인합니다. 이 검증을 임시로 건너뛰려면 `-SkipSmoke`를 붙입니다.

빌드 결과:

```text
dist/LinearStageControl/LinearStageControl.exe
```

Release portable ZIP build:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_release.ps1
```

Release portable ZIP output:

```text
dist/LinearStageControl-Portable.zip
dist/portable_manifest.json
```

설치 프로그램이 필요하면 Inno Setup 6을 설치한 뒤 아래 명령을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

`build_installer.ps1`는 slim installer를 기본으로 만들며, 이전 offline build의 pylon Runtime payload가 `dist`에 남아 있으면 실수로 1GB 이상 설치본이 만들어지지 않도록 중단합니다. offline installer를 의도적으로 만들 때는 `packaging\build_release_installers.ps1`를 사용합니다.
slim build는 Qt QML/Quick/PDF/VirtualKeyboard, Qt translations, pypylon DataProcessing처럼 현재 GUI 촬영 흐름에서 사용하지 않는 payload를 제거한 뒤 packaged smoke test로 검증합니다.

설치 프로그램 결과:

```text
dist/LinearStageControlSetup.exe
```

기본 빌드는 online/slim installer입니다. `sdk_downloads/installers/pylon_Runtime_26.04.1.exe`가 있어도 기본 Setup에는 포함하지 않으며, 새 PC에 pylon Runtime이 없으면 앱에서 장비 연결 실패 안내와 다운로드 버튼을 표시합니다. 기본 릴리즈용 online 설치본은 아래 명령으로 만듭니다.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_release_installers.ps1 -SkipOffline
powershell -ExecutionPolicy Bypass -File packaging\build_portable_release.ps1 -SkipAppBuild
```

두 번째 명령의 `-SkipAppBuild`는 installer 단계에서 smoke test를 통과한 `dist\LinearStageControl`의 버전과 전체 소스 fingerprint를 현재 worktree와 대조한 뒤 재사용하므로 동일한 PyInstaller 전체 빌드를 반복하지 않습니다. portable만 단독으로 만들 때는 옵션 없이 실행합니다.

릴리즈 빌드 결과:

```text
dist/LinearStageControlSetup.exe
dist/LinearStageControlSetup-Online.exe
dist/update_manifest.json
dist/LinearStageControl-Portable.zip
dist/portable_manifest.json
```

`LinearStageControlSetup.exe`는 자동 업데이트가 사용하는 기본 온라인 설치본입니다. 인터넷이 없는 현장용 offline 설치본이 필요할 때만 `-SkipOffline` 없이 installer 빌드를 실행합니다. 앱의 `업데이트 확인` 버튼은 public GitHub Release의 latest 버전을 확인하며, 앱은 Setup을 다운로드한 뒤 SHA256 검증이 통과할 때만 설치 파일을 실행합니다.

GitHub Release 업로드 전에는 main을 push하고 동일 커밋에 `vX.Y.Z` 태그를 만들어 push합니다. 그 다음 dry-run에서 두 manifest의 버전·크기·SHA256을 확인하고 실제 업로드를 실행합니다.

```powershell
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main
git push origin vX.Y.Z
powershell -ExecutionPolicy Bypass -File packaging\release_github.ps1 -Tag vX.Y.Z -IncludePortable -DryRun
powershell -ExecutionPolicy Bypass -File packaging\release_github.ps1 -Tag vX.Y.Z -IncludePortable
```

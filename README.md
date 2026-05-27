# Basler ace 2 + Zaber XY 스테이지 캡처

## 배포 버전

- Current release: `v0.1.0`
- Windows installer: `LinearStageControlSetup.exe`
- SHA256: `1E2071C0EC100E0DEE41CF16FDAB61BB59465C88151D7C250AF31F96C6F6EF1C`
- Release notes: [CHANGELOG.md](CHANGELOG.md)

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
- Basler pylon runtime installer: `sdk_downloads/installers/pylon_Runtime_26.04.1.exe`
- 이 PC에는 `C:\Program Files\Basler\pylon`에 pylon `26.04.1.18174` 런타임이 설치되어 있습니다.

## 기본 명령

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\launch_gui.py
python scripts/list_devices.py
python scripts/xy_scan_capture.py --config config.yaml --dry-run
python scripts/xy_scan_capture.py --config config.yaml
```

장비 연결 후 `python scripts/list_devices.py`에서 Basler 카메라와 Zaber COM 포트가 보이는지 먼저 확인하세요.

## GUI

GUI 실행은 아래 명령을 사용합니다.

```powershell
python scripts\launch_gui.py
```

GUI는 한국어 UI로 구성되어 있으며 장비 새로고침, 위치 직접 입력, CSV/TSV/TXT/JSON/JSONL/YAML/XLSX 위치 import, CSV export, 데이터셋 저장 위치 선택, 촬영 시작/중지, 촬영 이미지 미리보기, 캡처 목록 확인을 할 수 있습니다. 촬영 중에는 `진행` 영역에 연결, 원점 복귀, 위치 이동, 안정화 대기, 촬영, 저장 같은 현재 단계와 완료 개수가 표시됩니다. 촬영된 이미지는 오른쪽 `촬영 목록`을 클릭하면 다시 볼 수 있고, 선택한 사진의 target/actual 위치와 um 단위 오차는 미리보기 아래의 표 칸에 나뉘어 표시됩니다.

위치 테이블은 Zaber 210 mm XY 스테이지 이동 범위 기준으로 `0-210 mm`를 즉시 검사합니다. 각 행에는 `X`, `Y`뿐 아니라 선택 입력인 `속도 mm/s`, `캡쳐 수`를 넣을 수 있습니다. 속도나 캡쳐 수를 비우면 촬영 설정의 이동속도와 기본 캡쳐 수를 사용합니다. 범위를 벗어난 X/Y 셀은 빨간색, 중복 좌표나 빈 라벨 같은 확인 항목은 노란색으로 표시되며, 테이블 아래 상태 줄에 오류/경고 요약이 표시됩니다.

`시작`을 누르면 촬영 전 점검 창이 먼저 열립니다. 이 창은 위치 범위, Basler 카메라 감지 여부, Zaber COM 포트, X/Y 축 매핑 중복, 저장 폴더 생성 가능 여부, 이동 속도, 촬영 설정, 고정 오차 기준을 한 번에 확인합니다. 오류가 있으면 시작 버튼이 비활성화되고, 경고만 있을 때는 사용자가 내용을 확인한 뒤 계속 진행할 수 있습니다.

촬영 설정의 `메타데이터`, `요약`, `실행 옵션` 체크박스는 각각 별도 박스 안에 표시되어 다른 촬영 파라미터와 구분됩니다. 실행 옵션에는 `소프트웨어 트리거`, `NPY 저장`, `원점 복귀 생략`이 들어갑니다. 기본 메타데이터 출력은 `captures.csv`, `captures.jsonl`, `captures.json`, `captures.tsv`, `captures.yaml`, `captures.xlsx`이고, 기본 요약 출력은 `summary.json`, `summary.yaml`, `summary.md`입니다.

화면에 보이는 숫자는 불필요한 0을 줄여 표시합니다. mm 위치값은 최대 4자리, um 오차값은 최대 2자리까지만 보여주며, `captures.csv`와 `captures.jsonl`에는 계산 원본 값이 그대로 저장됩니다.

창 폭이 좁아지면 좌우 분할 레이아웃이 위아래 배치로 자동 전환되며, 왼쪽 제어 영역은 스크롤 가능합니다. 이미지 미리보기는 `전체화면 보기` 버튼 또는 이미지 더블클릭으로 확대 창을 열 수 있고, 확대 창에서는 화면 맞춤, 100%, 확대, 축소를 사용할 수 있습니다.

노출, 안정화, 이동속도, 기본 캡쳐 수는 키보드로 직접 입력할 수 있는 값 입력칸을 유지하면서, 오른쪽에 빠른 증감 버튼을 둔 가로형 조정 UI를 사용합니다. 노출은 `-1000 -100 -10 +10 +100 +1000`, 안정화와 이동속도는 `-100 -10 -5 +5 +10 +100`, 기본 캡쳐 수는 `-100 -10 -1 +1 +10 +100` 단위로 조정할 수 있습니다.

주요 조작 버튼과 값 입력칸에는 툴팁을 붙여 파일 열기/저장, 장비 새로고침, CSV import/export, 실행/중지, 전체화면 확대, 각 파라미터의 의미와 기본값을 빠르게 확인할 수 있게 했습니다.

`장비` 영역의 `자동검색` 버튼을 누르면 LAN/GigE Basler 카메라를 한 번 검색합니다. 검색 상태는 별도 배지로 `탐색중`, `성공`, `실패`가 표시되고 상태별 색상으로 구분됩니다. `a2A2464-115g5mBAS`처럼 LAN/GigE 포트에 연결되는 카메라가 감지되면 `카메라` 드롭다운에 모델명, serial, IP, device class가 추가되어 선택할 수 있습니다. GUI가 주기적으로 카메라 검색을 반복하지 않으므로 촬영 중 pypylon 장치 열기와 충돌할 가능성을 줄입니다.

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
  max(measured_radial_stage_error_um, xy_radial_worst_case_um)

predicted_min_error_um =
  max(0, measured_radial_stage_error_um - xy_radial_worst_case_um)
```

`measured_radial_stage_error_um`은 Zaber가 보고한 실제 위치와 목표 위치의 차이를 um로 환산한 값입니다. `config.yaml`의 `calibration` 항목은 저장용 스냅샷이며 계산은 코드의 고정 제조사 스펙을 사용합니다.

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

GUI의 `Error` 탭에는 캡처별 오차 예측 범위가 캔들차트로 표시됩니다. 캔들의 wick은 `predicted_min_error_um`부터 `predicted_max_error_um`까지의 범위이고, body는 측정 radial error에서 최대 예측 오차까지의 구간입니다. 전체 최대/평균, 허용 한계, 제한값 초과 개수는 요약 표로 표시됩니다. `captures.csv`와 `captures.jsonl`에도 `measured_error_x_um`, `measured_error_y_um`, `measured_radial_error_um`, `predicted_min_error_um`, `predicted_max_error_um`, `predicted_x_min_um`, `predicted_x_max_um`, `predicted_y_min_um`, `predicted_y_max_um`, `within_error_threshold`가 저장됩니다.

제조사 스펙은 사용자가 매번 조작할 값이 아니므로 왼쪽 제어 패널에는 상시 표시하지 않습니다. 필요한 경우 `오차` 탭의 `스펙 보기` 버튼에서 고정값 표를 확인할 수 있습니다.

## 위치 입력

`config.yaml`의 `scan.positions`에 직접 입력할 수 있습니다.

```yaml
scan:
  default_capture_count: 1
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

GUI의 `선형 경로` 버튼은 시작 X/Y, 끝 X/Y를 받아 직선 보간 위치 목록을 생성합니다. 생성 기준은 `간격 mm/캡쳐` 또는 `위치 수` 중 선택할 수 있습니다. 간격 기준은 경로를 따라 지정한 mm마다 한 위치를 만들고 끝점은 항상 포함합니다. 생성된 행에도 위치별 이동속도와 캡쳐 수를 넣을 수 있고, 비워 두면 기본 촬영 설정을 따릅니다.

`positions`와 `positions_file`이 모두 비어 있고 `scan.linear_path`가 설정되어 있으면 선형 경로가 생성됩니다. `scan.linear_path.spacing_mm`이 있으면 간격 기준을 사용하고, 없으면 `count` 기준을 사용합니다. 세 항목이 모두 비어 있으면 기존 grid 설정이 fallback으로 사용됩니다.

GUI에서 위치 파일을 불러오면 같은 범위 검사가 즉시 적용됩니다. 촬영 run은 `0-210 mm` 범위를 벗어난 위치가 하나라도 있으면 시작되지 않습니다. 파일이 비어 있거나 X/Y 컬럼을 찾을 수 없거나 숫자로 해석할 수 없는 값이 있으면 해당 행 번호와 함께 오류를 냅니다.

## 저장 구조

기본 저장 위치는 `%USERPROFILE%/Documents/LinearStageControl/datasets/<run_timestamp>/`입니다.

```text
%USERPROFILE%/Documents/LinearStageControl/datasets/20260526T142233_123456+0900/
  manifest.json
  config.yaml
  captures.csv
  captures.jsonl
  captures.json
  captures.tsv
  captures.yaml
  captures.xlsx
  summary.json
  summary.yaml
  summary.md
  images/
    000000_..._x0.000000_y0.000000_origin.tiff
```

`captures.*`에는 target 위치, actual 위치, 위치 오차, 위치별/실제 적용 이동속도, 위치 내 캡쳐 순번, um 단위 오차 예측, 이동 시작/완료 timestamp, settle 완료 timestamp, capture 명령/완료 timestamp, 카메라 timestamp, 이미지 경로가 기록됩니다. `summary.*`에는 run 전체 성공/오류 개수, 최대/평균 오차, 한계 초과 개수, 오류 메시지가 저장되어 논문용 통계 처리나 실험 로그 정리에 바로 쓸 수 있습니다.

## 원본 이미지

캡처는 `grab_result.GetArray().copy()`로 받은 변환 전 배열을 lossless TIFF로 저장합니다. 완전한 numpy 배열도 함께 보존하려면 `config.yaml`에서 아래처럼 설정합니다.

```yaml
dataset:
  save_numpy: true
```

## 주요 설정

```yaml
camera:
  pixel_format: Mono8
  use_software_trigger: true
  trigger_selector: FrameStart
  trigger_source: Software
  exposure_us: 5000
  timeout_ms: 5000

stage:
  serial_port: COM3
  settle_s: 0.2
  move_velocity_mm_s:
```

`a2A2464-115g5mBAS`는 LAN/GigE 카메라이므로 NIC IP 대역, jumbo frame, packet size 설정이 중요합니다. pylon IP Configurator와 GigE Configurator는 `C:\Program Files\Basler\pylon` 아래에 설치되어 있습니다.

구현 중 정리한 제어/저장 규칙은 [rules.md](rules.md)에 기록했습니다.

## Windows 빌드와 설치

Python이 없는 다른 PC에서 실행할 수 있도록 PyInstaller portable 빌드를 만들 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

빌드 결과:

```text
dist/LinearStageControl/LinearStageControl.exe
```

설치 프로그램이 필요하면 Inno Setup 6을 설치한 뒤 아래 명령을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

설치 프로그램 결과:

```text
dist/LinearStageControlSetup.exe
```

빌드 폴더에는 Basler pylon runtime installer도 포함됩니다. 새 PC에서 Basler 카메라가 감지되지 않으면 설치 프로그램의 post-install 옵션으로 pylon runtime을 설치하세요. 설치 후 수동 실행이 필요하면 앱 설치 폴더의 `_internal/sdk_downloads/installers/pylon_Runtime_26.04.1.exe`를 실행하면 됩니다.

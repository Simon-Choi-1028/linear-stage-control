# Linear Stage Control 상세 개발 기록

작성일: 2026-05-29
최종 갱신: 2026-08-07
현재 기준 버전: `v0.1.13`
GitHub 저장소: `Simon-Choi-1028/linear-stage-control`
최신 공개 릴리즈: https://github.com/Simon-Choi-1028/linear-stage-control/releases/latest

이 문서는 Basler ace 2 카메라와 Zaber Ultra Precision Linear Motor XY 스테이지를 연동하는 optical calibration capture GUI의 현재 개발 상태, 설계 의도, 기능, 빌드/배포 방식, 데이터 저장 구조, 주요 제어 원칙을 한 곳에 기록하기 위한 상세 문서입니다.

## v0.1.5 Hotfix 검증 기록 (과거 기록)

아래 내용은 2026-06-01 당시의 hotfix 검증 기록입니다.

- Zaber Device DB: `devices-public-v2.sqlite.lzma`를 직접 DB로 넘기던 문제를 제거했습니다. 빌드 스크립트는 공식 `.sqlite.lzma`를 다운로드한 뒤 `devices-public-v2.sqlite`로 해제하고 `SQLite format 3` header를 검증합니다. 앱 런타임은 `.sqlite`를 우선 사용하며, legacy `.sqlite.lzma`만 있으면 `%LOCALAPPDATA%/LinearStageControl/zaber/devices-public-v2.sqlite`로 해제해 검증합니다. 로컬 DB가 잘못된 경우 Zaber Web Service fallback을 설정하고 원인을 로그에 남깁니다.
- Live preview: `LivePreviewWorker`에 thread-safe pending settings update를 추가했습니다. 노출, Gain, Gamma, Black Level, AcquisitionFrameRate는 Live worker가 열린 카메라 세션에 debounce update로 적용합니다. Width/Height/Offset/Binning/Decimation/PixelFormat은 grabbing 중 변경 위험이 있으므로 Live worker를 짧게 재시작합니다.
- Live FPS: UI 상태에는 설정 FPS가 아니라 최근 프레임 timestamp 기반 rolling FPS를 `Live 9.8 FPS` 형식으로 표시합니다.
- Live 전체 frame 표시: Live 시작, Live 복귀, 카메라 재연결 시 preview zoom/center를 100% full-frame fit으로 초기화합니다. 저장 이미지 preview에서 쓰던 확대/crop 상태가 Live로 넘어오지 않게 했습니다.
- Live 크기 조정: `Live 크기` 슬라이더를 제거하고 preview 우하단 resize handle을 추가했습니다. 사용자는 대각선 핸들을 끌어 미리보기 높이를 바꾸고, `기본 크기` 버튼 또는 핸들 더블클릭으로 기본값으로 되돌릴 수 있습니다.
- 반응형 UI: preview label의 고정 maximum height 의존을 줄이고 `QSizePolicy.Expanding` 기반으로 조정했습니다. 창 최대화/복원/좁은 폭 전환 시 splitter와 scroll area 높이를 deferred update로 재계산합니다.
- 검증 결과: `ruff check .`, `ruff format .`, `pyright`, `python -m unittest tests.test_core_behaviour -v`, `python scripts/launch_gui.py --smoke-test`, `python scripts/xy_scan_capture.py --config config.yaml --dry-run`, packaged `LinearStageControl.exe --smoke-test`가 통과했습니다.
- 빌드 산출물: `dist/LinearStageControlSetup.exe` 및 `dist/LinearStageControlSetup-Online.exe`는 64,791,044 bytes(약 61.8 MB), `dist/LinearStageControlSetup-Offline.exe`는 1,454,579,383 bytes(약 1387.2 MB)입니다. Offline installer가 큰 이유는 Basler pylon Runtime installer를 포함하기 때문입니다.
- 추가 QC에서 좁은 폭/낮은 높이 창 전환이 기존 최소 레이아웃 폭과 높이에 막히는 문제, 빈 position label이 빈 문자열로 남는 문제를 발견해 수정했습니다.
- dist 검증: `dist/LinearStageControl/_internal/sdk_downloads/zaber/devices-public-v2.sqlite`가 포함되어 있고 크기는 53,075,968 bytes이며 header는 `SQLite format 3`입니다.

## 1. 개발 목표

본 프로그램은 Basler ace 2 `a2A2464-115g5mBAS` 카메라와 Zaber Ultra Precision Linear Motor 210 mm XY 교차형 스테이지를 연동해, 사용자가 사전에 지정한 위치 목록을 따라 스테이지를 이동시키고 각 위치에서 카메라 원본 이미지를 캡처하는 도구입니다.

핵심 목표는 다음과 같습니다.

- LAN/GigE Basler 카메라 자동 감지와 선택.
- Zaber XY 스테이지 제어 및 단일축 운용 지원.
- 위치 목록을 GUI 직접 입력 또는 CSV/TSV/TXT/JSON/JSONL/YAML/XLSX 파일로 입력.
- 각 위치 이동 완료 후 안정화 대기, software trigger 또는 trigger off 방식으로 이미지 캡처.
- 모든 캡처에 timestamp, target 위치, actual 위치, 위치 오차, 촬영 설정, 이미지 파일명을 기록.
- 캡처별 실제 카메라 파라미터를 포함한 단일 `captures.csv` 촬영 로그 생성.
- 실시간 live preview, 저장 이미지 확인, 확대/격자/중앙선 overlay 지원.
- optical calibration 목적에 맞춘 um 단위 오차 예측 및 캔들차트 표시.
- 다른 PC에서 설치 가능한 Windows installer 제공.
- online slim installer와 offline pylon Runtime 포함 installer를 모두 릴리즈.

## 2. 현재 릴리즈 상태

`v0.1.13`은 단일 Live 캡처를 화면 렌더 결과가 아닌 SDK 센서 배열에서 저장해 원본 크기·비트 심도·픽셀 값을 보존하고, 미리보기 회전·반전·리사이즈·정규화·오버레이가 저장 파일에 섞이지 않도록 수정합니다. 기본 공개 asset은 online Setup, update manifest, portable ZIP, portable manifest이며 offline Setup은 현장에서 별도로 요구할 때만 만듭니다.

`update_manifest.json`의 기본 채널은 online/slim 설치본입니다. 앱의 자동 업데이트 기능은 `LinearStageControlSetup.exe`를 기본 설치 asset으로 사용하고, 다운로드 후 SHA256을 검증한 뒤 사용자 승인 시 설치 파일을 실행합니다.

## 3. 개발 환경과 의존성

프로젝트는 Python 3.13 기반입니다.

주요 의존성:

| 패키지 | 버전 | 역할 |
| --- | --- | --- |
| `numpy` | `2.4.6` | 이미지 배열, 계산 |
| `pillow` | `12.2.0` | lossless 이미지 저장 |
| `pypylon` | `26.4.1` | Basler Python SDK |
| `PySide6` | `6.10.1` | GUI |
| `pyinstaller` | `6.17.0` | Windows 패키징 전용 `build` extra |
| `pyserial` | `3.5` | COM 포트 탐색 보조 |
| `PyYAML` | `6.0.3` | YAML config |
| `rich` | `15.0.0` | CLI 출력 |
| `zaber-motion` | `9.3.0` | Zaber Motion Library |

Basler pylon Runtime은 online/slim installer에 포함하지 않습니다. pylon Runtime이 없는 PC에서는 앱 실행 후 장비 연결 실패 또는 진단 결과를 통해 다운로드/설치 안내를 제공합니다. 현장 PC가 인터넷을 사용할 수 없으면 offline installer를 사용합니다.

Zaber는 공식 Motion Library Python package와 공식 Device Database를 사용합니다. Device Database는 검증된 uncompressed `devices-public-v2.sqlite`를 우선 사용하며, legacy `.sqlite.lzma`만 있으면 런타임 cache에 해제 후 `Library.set_device_db_source(DeviceDbSourceType.FILE, ...)`로 지정합니다.

## 4. 주요 하드웨어 대상

### Basler 카메라

기본 대상은 Basler ace 2 `a2A2464-115g5mBAS`입니다. 이 모델은 LAN/GigE 기반 monochrome 카메라로 취급합니다.

다른 Basler 모델도 연결할 수 있게 다음 호환성을 열어두었습니다.

- `serial_number`, `user_defined_name` 기반 선택.
- `model_name`, `device_class` 부분 필터.
- `pixel_format: Auto`로 현재 카메라 포맷 유지.
- `pixel_format_candidates`를 순서대로 fallback.
- `BaslerGigE`, `BaslerUsb` 등 device class 정보 표시.
- 지원되지 않는 GenICam feature는 즉시 실패시키지 않고 warning/log 처리.

### Zaber 스테이지

기준 장비는 Zaber 210 mm LDM/X-LDM-AE crossed XY 스테이지 구성입니다.

기본 축 매핑:

```yaml
stage:
  axes:
    x:
      enabled: true
      device_index: 0
      axis_number: 1
    y:
      enabled: true
      device_index: 0
      axis_number: 2
```

단일 2축 컨트롤러 기준으로 X/Y가 같은 device index를 사용하고 axis number만 1/2로 나뉘는 구성을 기본값으로 둡니다. X축 또는 Y축 하나만 연결된 현장도 고려해 GUI와 config에서 축별 활성화가 가능합니다.

## 5. 전체 촬영 제어 흐름

기본 run 흐름:

1. `config.yaml` 또는 GUI 입력에서 카메라, 스테이지, 데이터셋, 위치 목록을 읽습니다.
2. preflight에서 위치 범위, 축 활성화, 카메라 감지, COM 포트, 저장 폴더, 오차 모델을 점검합니다.
3. 촬영 run이 시작되면 live preview worker를 정지해 카메라 점유 충돌을 방지합니다.
4. acquisition worker가 Zaber connection을 단독 소유합니다.
5. 위치 목록을 순회합니다.
6. 각 위치에서 활성 축에만 move 명령을 보냅니다.
7. 이동은 `wait_until_idle=False`로 시작하고 `is_busy()` polling loop에서 완료/취소를 감지합니다.
8. 이동 완료 후 `stage.settle_s`만큼 안정화 대기합니다.
9. 지정된 `capture_count`만큼 카메라 캡처를 반복합니다.
10. 캡처 원본 배열을 lossless image로 저장하고 metadata record를 생성합니다.
11. 전체 run 종료 후 CSV를 flush하고 manifest를 갱신합니다.
12. live preview 설정이 켜져 있으면 run 종료 후 live preview를 재시작합니다.

중지/취소:

- stop/cancel은 새로운 serial connection을 열지 않고 acquisition worker 내부 플래그로 전달합니다.
- 이동 중 cancel이 감지되면 같은 connection에서 `axis.stop(wait_until_idle=False)`를 호출합니다.
- 이후 idle 상태까지 정리한 뒤 run을 종료합니다.
- serial 명령 중복으로 인한 `SerialPortBusyException` 가능성을 줄이기 위해 worker ownership을 유지합니다.

## 6. GUI 구성

GUI는 한국어 UI를 기본으로 합니다. 현재 white mode 기반의 modernized stylesheet를 `linear_stage_control/gui_style.py`에 분리했습니다.

주요 UI 영역:

- 이동 위치 및 진행.
- 장비 및 촬영 설정.
- live/image preview.
- 촬영 목록.
- Error 캔들차트.
- 진단 탭.
- 수동 스테이지 제어.

### 이동 위치 테이블

위치 테이블은 다음 입력을 지원합니다.

- `label`
- `x_mm`
- `y_mm`
- `move_velocity_mm_s`
- `capture_count`

속도와 캡처 수가 비어 있으면 전역 촬영 설정의 기본값을 따릅니다. GUI에서는 비어 있는 셀을 단순 공백으로 두지 않고 회색 placeholder 형태로 기본값 의미를 보여줍니다.

위치 검증:

- X/Y 범위는 Zaber 210 mm 이동 범위 기준 `0-210 mm`.
- 범위 밖 좌표는 오류.
- 중복 좌표, 빈 라벨 등은 경고.
- 비활성 축 좌표가 run 내부에서 여러 값으로 변하면 오류.
- X/Y가 같은 Zaber device/axis를 가리키면 오류.

### 선형 경로 생성

`선형 경로` 버튼은 즉시 구동이 아니라 경로 생성 모달을 여는 기능입니다. 재생 아이콘 대신 설정/경로 성격의 아이콘으로 사용성을 개선했습니다.

선형 경로 모달 기능:

- 시작 X/Y, 끝 X/Y 입력.
- `간격 mm/캡쳐` 기준 생성.
- 필요 시 `위치 수` 기준 생성.
- 끝점은 항상 포함.
- 이동속도와 캡처 수를 선택적으로 지정.
- 비워 두면 기본 이동속도/캡처 수 사용.
- 잘못된 입력은 모달 내부 경고로 표시하고 생성 버튼 비활성화.
- 2D 미니맵에서 시작점, 끝점, 보간 점, 진행 방향, 총 거리, 위치 수, 예상 캡처 수 표시.

### 수치 조정 UI

노출, 안정화, 이동속도, 기본 캡처 수는 키보드 직접 입력을 유지하면서 빠른 증감 버튼을 제공합니다.

증감 단위:

| 항목 | 단위 | 빠른 조정 |
| --- | --- | --- |
| 노출 | us | `-1000`, `-100`, `-10`, `+10`, `+100`, `+1000` |
| 안정화 | ms 또는 s | `-100`, `-10`, `-5`, `+5`, `+10`, `+100` |
| 이동속도 | mm/s | `-100`, `-10`, `-5`, `+5`, `+10`, `+100` |
| 기본 캡처 수 | 장/회 | `-100`, `-10`, `-1`, `+1`, `+10`, `+100` |

안정화 시간은 GUI에서 `ms`와 `s`를 선택할 수 있지만 내부 저장값은 기존 호환을 위해 `stage.settle_s` 초 단위를 canonical 값으로 유지합니다. config 입력에서는 `settle_s`를 우선하고, 없을 때만 `settle_ms`를 alias로 읽습니다.

### 카메라 파라미터

초기에는 노출 중심이었으나 현재는 `카메라 파라미터` 섹션으로 확장했습니다.

선택 입력 항목:

- Exposure.
- Gain.
- Acquisition Frame Rate.
- ROI Width/Height.
- ROI Offset X/Y.
- Gamma.
- Black Level.
- Binning X/Y.
- Decimation X/Y.

빈 값은 카메라 현재값 유지로 처리합니다. GenICam feature가 존재하고 writable일 때만 적용하며, 미지원 항목은 run log와 preflight warning에 남깁니다.

Live FPS와 camera acquisition frame rate는 분리했습니다.

- `camera.live_preview.fps`: GUI live 화면 갱신 목표.
- `camera.acquisition_frame_rate`: 카메라 취득률 설정.

### live preview

카메라 연결 성공 후 live preview가 자동 시작됩니다.

구현 원칙:

- live는 capture용 single grab과 분리.
- live session은 `TriggerMode Off`.
- continuous grabbing 방식 사용.
- 가능하면 `GrabStrategy_LatestImageOnly` 사용.
- 촬영 run 시작 전 live worker 정지.
- run 종료 후 자동 live 재시작.
- UI에 첫 프레임 대기, 수신 중, 오류 상태를 구분 표시.

Preview 기능:

- `Live 보기`, `Live 정지`, `Live 재연결`.
- 저장 이미지 선택 중에도 `Live 보기`로 즉시 live 복귀.
- preview 우하단 resize handle 기반 live/image 미리보기 높이 조절.
- 디지털 확대 100-800%.
- 클릭 지점 중심 확대.
- 4x4 기준 얇은 흰색 격자 overlay.
- 얇은 흰색 중앙 가로/세로선 overlay.
- 전체화면 이미지 보기.

Overlay는 render-only입니다. 원본 이미지 파일과 저장 metadata에는 격자/크로스라인이 합성되지 않습니다.

### 장비 검색

카메라 자동검색은 주기적으로 계속 돌지 않습니다. 사용자가 `자동검색` 버튼을 누를 때 단발 worker로 실행합니다.

검색 상태:

- `탐색중`
- `성공`
- `실패`

상태별 색상 배지를 별도로 표시합니다. 실패 시 `LAN/USB에서 Basler 카메라가 감지되지 않음` 같은 핵심 메시지는 경고 성격으로 눈에 띄게 표시합니다.

### 옵션 박스

촬영 로그 형식은 CSV로 고정하고, GUI에는 카메라 파라미터가 포함된다는 안내를 표시합니다. 실행 옵션은 촬영 파라미터와 섞이지 않도록 별도 박스로 나누었습니다.

실행 옵션:

- 소프트웨어 트리거.
- NPY 저장.
- 원점복귀 생략.

### 진단 탭

현장 문제를 빠르게 좁히기 위해 진단 탭을 추가했습니다.

진단 항목:

- `pypylon` import 가능 여부.
- Basler 카메라 탐색.
- Zaber COM 포트 목록.
- Zaber Device Database 존재 여부.
- output folder 쓰기 권한.
- GitHub update 접근성.

진단 탭은 비파괴 점검만 수행합니다. 진단만으로 스테이지 home/move를 실행하지 않습니다.

### 수동 스테이지 제어

촬영 run 이전에 스테이지를 직접 확인할 수 있게 수동 제어 영역을 추가했습니다.

기능:

- 현재 위치 읽기.
- 활성 축 원점 복귀.
- 절대 좌표 이동.
- X/Y jog 이동.
- 수동 정지.

촬영 run 중에는 serial connection 충돌을 줄이기 위해 수동 명령 버튼을 비활성화합니다.

## 7. 위치 입력과 파일 파서

지원 입력:

- GUI 테이블 직접 입력.
- `config.yaml`의 `scan.positions`.
- `scan.positions_file`.
- `scan.linear_path`.
- 마지막 fallback으로 grid scan.

지원 파일 포맷:

- CSV.
- TSV.
- TXT.
- JSON.
- JSONL.
- YAML.
- XLSX.

허용 컬럼 alias:

| 의미 | 허용 예시 |
| --- | --- |
| 라벨 | `label`, `name`, `id` |
| X 위치 | `x`, `x_mm`, `X mm`, `target_x_mm` |
| Y 위치 | `y`, `y_mm`, `Y mm`, `target_y_mm` |
| 이동속도 | `velocity`, `move_velocity_mm_s`, `speed_mm_s` |
| 캡처 수 | `capture_count`, `captures`, `frames` |

예외 처리:

- 빈 파일.
- 지원하지 않는 확장자.
- X/Y 컬럼 누락.
- 숫자 변환 실패.
- 캡처 수가 1 미만.
- 이동속도가 0 이하.
- 위치 범위 초과.
- 비활성 축 좌표 변화.

파일 파싱 오류는 가능하면 행 번호와 원인을 함께 표시합니다.

## 8. 데이터셋 저장 구조

기본 output root:

```text
%USERPROFILE%/Documents/LinearStageControl/datasets
```

run별 timestamp 디렉터리:

```text
%USERPROFILE%/Documents/LinearStageControl/datasets/<run_timestamp>/
  dataset_manifest.json
  manifest.json
  config.yaml
  captures.csv
  images/
    X000.500_Y001.250.png
```

촬영 metadata는 `captures.csv` 하나에 스트리밍 저장합니다. 각 행에는 target/actual 위치, 오차, timing, 이미지 경로와 함께 카메라 모델/serial, PixelFormat, 노출, Gain, FrameRate, ROI, Gamma, Black Level, Binning/Decimation, trigger, 회전/반전 파라미터가 포함됩니다. 실제 카메라에서 읽을 수 없는 항목은 캡처를 중단하지 않고 빈 칸으로 둡니다.

이미지 파일명 규칙:

```text
X{X:07.3f}_Y{Y:07.3f}[_Cxx][_Pxxxx].png
```

예:

```text
X033.333_Y000.000_C02.png
```

좌표는 소수점 셋째 자리까지 절삭합니다. 같은 위치의 추가 캡처에는 `_Cxx`, 중복 좌표 포인트에는 `_Pxxxx`가 붙고, 정확한 촬영 시각과 포인트 정보는 같은 run의 `captures.csv`에서 추적합니다.

## 9. 저장 metadata 주요 필드

각 capture record에는 다음 계열의 정보가 들어갑니다.

대상/위치:

- `run_id`
- `index`
- `label`
- `target_x_mm`
- `target_y_mm`
- `point_move_velocity_mm_s`
- `effective_move_velocity_mm_s`
- `capture_count`
- `capture_index`

축 활성화:

- `axis_x_active`
- `axis_y_active`

실제 위치:

- `actual_x_mm`
- `actual_y_mm`
- 비활성 축의 actual/error는 빈 값.

오차:

- `measured_error_x_um`
- `measured_error_y_um`
- `measured_radial_error_um`
- `predicted_min_error_um`
- `predicted_max_error_um`
- `predicted_x_min_um`
- `predicted_x_max_um`
- `predicted_y_min_um`
- `predicted_y_max_um`
- `within_error_threshold`

시간:

- 이동 시작 timestamp.
- 이동 완료 timestamp.
- settle 완료 timestamp.
- capture 명령 timestamp.
- capture 완료 timestamp.
- 카메라 timestamp.

이미지:

- `image_filename`
- `image_path`
- optional `npy_path`

Run manifest:

- 앱 버전.
- record 수.
- 산출물 상대 경로.
- `manifest_detail: full`일 때 파일 크기와 SHA256 hash.

`dataset_manifest.json`이 기준 manifest이고, 기존 자동화 호환을 위해 `manifest.json`도 함께 생성합니다. `full` manifest의 파일 항목은 대형 run에서도 목록 전체를 메모리에 올리지 않고 순차 기록합니다.

## 10. 원본 이미지 저장 정책

원본 보존 기준:

```python
grab_result.GetArray().copy()
```

Scan run은 이 배열에 설정된 방향 보정을 적용해 lossless PNG로 저장합니다. 단일 `Live 캡처`는 예외로, SDK buffer에서 분리한 sensor-order 배열을 회전·반전·비트 심도 변환·리사이즈 없이 lossless PNG로 저장합니다. 설정에 따라 TIFF/BMP도 사용할 수 있으며, scan에서 완전한 NumPy 배열 보존이 필요하면 NPY 저장을 함께 켭니다.

NPY 저장:

```yaml
dataset:
  save_numpy: true
```

`save_numpy: true`일 때만 완전한 numpy 배열 복원을 위한 `.npy` 파일도 함께 저장합니다. 기본값은 저장 용량을 줄이기 위해 false입니다.

## 11. Optical calibration 오차 모델

오차 모델은 사용자가 임의로 조정하는 값이 아니라 Zaber 210 mm LDM/X-LDM-AE crossed XY stage 제조사 스펙을 고정값으로 사용합니다.

고정 기준:

```text
unidirectional accuracy = 1.00 um
repeatability           = 0.08 um
horizontal runout       = 5.00 um
vertical runout         = 8.00 um
```

XY 평면 계산:

```text
xy_axis_worst_case_um   = 1.00 + 0.08 + 5.00 = 6.08 um
xy_radial_worst_case_um = sqrt(6.08^2 + 6.08^2) = 8.60 um
```

예측 범위:

```text
predicted_max_error_um =
  max(measured_radial_error_um, xy_radial_worst_case_um)

predicted_min_error_um =
  max(0, measured_radial_error_um - xy_radial_worst_case_um)
```

단일축 run에서는 활성 축 오차만 radial error 기준으로 사용합니다. 비활성 축 actual/error metadata는 빈 값으로 저장합니다.

GUI Error 탭:

- 캡처별 오차 범위를 캔들차트로 표시.
- wick: `predicted_min_error_um`부터 `predicted_max_error_um`.
- body: measured radial error부터 max predicted error.
- 전체 최대/평균 오차.
- 허용 한계.
- 제한값 초과 개수.
- `스펙 보기` 버튼으로 제조사 고정값 확인.

메인 제어 화면에는 제조사 스펙을 상시 노출하지 않습니다. 사용자가 자주 조작할 값이 아니기 때문입니다.

## 12. Zaber 제어 상세

스테이지 설정은 `StageSettings`와 `AxisAddress`로 분리되어 있습니다.

주요 원칙:

- acquisition worker가 serial connection을 단독 소유.
- run 중 stop/cancel/다음 이동 요청은 같은 worker에 플래그로 전달.
- 새 serial connection을 열어 덮어쓰는 방식 금지.
- 이동 시작은 `wait_until_idle=False`.
- busy polling 중 cancel 감지.
- cancel 시 같은 connection에서 `axis.stop(wait_until_idle=False)`.
- 이후 idle 상태까지 정리.

단일축 운용:

- `stage.axes.x.enabled`
- `stage.axes.y.enabled`

비활성 축에는 다음 호출을 하지 않습니다.

- resolve.
- home.
- move.
- position read.

비활성 축 좌표가 위치 목록 내부에서 여러 값으로 변하면 preflight 오류로 차단합니다.

## 13. Basler 카메라 제어 상세

카메라 설정은 `CameraSettings`로 파싱합니다.

Capture:

- 기본은 software trigger.
- `TriggerSelector=FrameStart`.
- `TriggerSource=Software`.
- `ExecuteSoftwareTrigger()`.
- trigger를 쓰지 않을 경우 `TriggerMode Off`로 단일 frame grab.

Live:

- capture session과 별도.
- `TriggerMode Off`.
- continuous grabbing.
- `GrabStrategy_LatestImageOnly`.
- GUI 갱신률은 `camera.live_preview.fps`.

카메라 파라미터 적용:

- feature 존재 확인.
- writable 확인.
- 값이 비어 있으면 유지.
- 실패/미지원은 경고로 남기고 가능한 항목은 계속 적용.

카메라 탐색:

- `enumerate_cameras()`로 device info 목록 반환.
- model, serial, IP, device class를 GUI에 표시.
- 자동검색은 단발 실행.

## 14. 업데이트 기능

업데이트 설정:

```yaml
updates:
  enabled: true
  repo: Simon-Choi-1028/linear-stage-control
```

동작:

1. public GitHub latest Release 조회.
2. `update_manifest.json` asset 다운로드.
3. 현재 앱 버전과 latest version 비교.
4. 기본 asset `LinearStageControlSetup.exe` 선택.
5. 설치 파일 다운로드.
6. SHA256 검증.
7. 사용자 승인 시 Setup 실행 후 앱 종료.

검증 실패 시 설치 파일은 실행하지 않습니다.

Release upload:

- `gh` CLI가 있으면 `gh release create` 사용.
- `gh` CLI가 없으면 GitHub REST API fallback 사용.
- `GITHUB_TOKEN`, `GH_TOKEN`, 또는 `git credential fill`의 저장 credential을 사용.
- 큰 offline installer 업로드는 `curl.exe` retry 옵션으로 안정화.
- 같은 이름/같은 크기 asset은 재업로드하지 않고 skip.
- 크기가 다르면 기존 asset 삭제 후 재업로드.

## 15. 상태 머신, 예외, 로그

GUI worker가 늘어나면서 상태 꼬임을 줄이기 위해 `linear_stage_control/app_state.py`에 `AppRunState` enum을 추가했습니다.

상태 값:

- `IDLE`
- `DISCOVERING_CAMERA`
- `LIVE_PREVIEW`
- `ACQUIRING`
- `CANCELLING`
- `DIAGNOSTICS`
- `MANUAL_STAGE`
- `UPDATE_CHECKING`
- `UPDATE_DOWNLOADING`
- `ERROR`

버튼 활성화는 각 slot에서 직접 흩어져 처리하지 않고 `MainWindow.apply_state()`에서 중앙 관리합니다. 카메라 탐색, live, 촬영, 취소, 진단, 수동 스테이지, 업데이트 작업이 동시에 꼬이는 상황을 줄이기 위해 worker 실행 상태도 함께 확인합니다. 상태가 끝나면 `apply_ambient_state()`가 현재 살아 있는 worker를 기준으로 적절한 idle/live/update/diagnostics 상태를 다시 적용합니다.

오류 타입은 `linear_stage_control/exceptions.py`에 공통 계층으로 분리했습니다.

```text
LinearStageControlError
  CameraConnectionError
  StageConnectionError
  DatasetWriteError
  UpdateVerificationError
```

`LinearStageControlError`는 `user_message`와 `developer_message`를 분리합니다. GUI worker는 사용자에게 `user_message`를 표시하고, 원본 예외와 traceback은 로그에 남깁니다. Basler 연결/캡처, Zaber 연결/축 해석, 데이터셋 파일 저장, 업데이트 manifest/hash 검증은 가능한 범위에서 이 예외 계층을 사용합니다.

Structured logging은 `linear_stage_control/logging_setup.py`에 추가했습니다.

기본 로그 위치:

```text
%USERPROFILE%/Documents/LinearStageControl/logs/
  app_YYYYMMDD.log
  run_<run_id>.log
```

로그는 JSONL 형식입니다. 각 줄에는 timestamp, level, logger, message, event, 추가 field, exception traceback이 들어갈 수 있습니다. GUI의 `log()` 메시지는 화면 로그와 app log에 동시에 기록됩니다. Acquisition run이 시작되면 `run_<run_id>.log` handler를 추가해 stage move, capture save, run finish/failure 같은 run 전용 event를 남기고 run 종료 후 handler를 닫습니다.

정적 검사 도구도 `pyproject.toml`에 추가했습니다.

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format .
python -m pyright
```

`ruff`는 import/기본 오류 검사를 전체 repo에 적용하고, `pyright`는 strict가 아닌 basic mode로 `camera.py`, `stage.py`, `dataset.py`, `scan.py`, `position_validation.py`, 공통 예외/로그/상태 모듈부터 검사합니다. `pyright[nodejs]` extra를 사용해 nodeenv SSL 이슈가 있는 PC에서도 Node runtime 의존성을 더 안정적으로 확보합니다.
패키지에는 `py.typed` marker를 포함해 타입 힌트가 배포본에도 유지되도록 했습니다.

## 16. 빌드와 패키징

개발 실행:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\launch_gui.py
```

CLI dry-run:

```powershell
python scripts\xy_scan_capture.py --config config.yaml --dry-run
```

PyInstaller portable build:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

PyInstaller는 `build` optional dependency로 분리되어 있고 build script가 자동으로 설치합니다. installer 빌드 직후 portable ZIP도 만들 때는 `build_portable_release.ps1 -SkipAppBuild`로 버전과 소스 fingerprint가 일치하는 `dist\LinearStageControl`만 재사용해 전체 앱 빌드를 반복하지 않습니다.

Slim installer:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

Dual release installer:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_release_installers.ps1
```

GitHub Release upload:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\release_github.ps1 -Tag v0.1.13 -IncludePortable
```

Packaging safeguards:

- 기본 Setup에는 pylon Runtime installer를 포함하지 않음.
- 이전 offline build의 pylon payload가 `dist`에 남아 있으면 slim installer build 중단.
- PyInstaller 후 optional Qt/Pylon payload prune.
- installer와 portable 산출물을 연속 생성할 때 검증된 앱 build 재사용.
- prune 이후 `LinearStageControl.exe --smoke-test` 실행.
- Zaber native binding DLL 포함 여부 확인.

Prune 대상 예:

- Qt QML/Quick/PDF/VirtualKeyboard.
- Qt translations.
- pypylon DataProcessing payload.

## 17. 모듈 구조

현재 주요 Python 모듈:

| 파일 | 역할 |
| --- | --- |
| `app_state.py` | GUI run state enum and state names |
| `camera.py` | Basler camera settings, device enumeration, capture/live support, image save |
| `config.py` | YAML config load, blank value normalize |
| `dataset.py` | DatasetRun, path naming, capture record, manifest/hash |
| `run_stats.py` | GUI 실행 중 촬영 통계 집계 |
| `diagnostics.py` | pylon, camera, serial, Zaber DB, output, update diagnostics |
| `error_model.py` | fixed Zaber manufacturer specs and um error estimate |
| `exceptions.py` | user-facing custom exception hierarchy |
| `gui_app.py` | MainWindow, GUI layout, event wiring, preflight |
| `gui_style.py` | shared white-mode Qt stylesheet |
| `gui_support.py` | GUI helper functions, icons, resource paths |
| `gui_widgets.py` | preview label, parameter row, path minimap, fullscreen image, error chart |
| `gui_workers.py` | acquisition/live/discovery/update/diagnostics/manual stage QThreads |
| `linear_path_dialog.py` | linear path generation modal |
| `logging_setup.py` | JSONL app/run structured logging setup |
| `position_validation.py` | GUI row parsing and scan point validation |
| `preview_rendering.py` | NumPy/QImage/QPixmap conversion, crop, grid/cross overlay |
| `scan.py` | ScanPoint, positions/config/file parser, linear path, grid fallback |
| `stage.py` | Zaber settings, serial ports, XY stage wrapper, device DB |
| `text_formatting.py` | compact numeric display, unit text, tooltips |
| `updater.py` | GitHub update check, manifest parse, SHA256 verify, download |

Scripts:

| 파일 | 역할 |
| --- | --- |
| `launch_gui.py` | GUI entrypoint and smoke-test entry |
| `xy_scan_capture.py` | CLI acquisition and dry-run |
| `list_devices.py` | Basler/Zaber device listing |
| `grab_frame.py` | single camera frame grab helper |
| `home_stage.py` | stage homing helper |
| `capture_manual_screenshots.py` | manual UI screenshot generator |

Packaging:

| 파일 | 역할 |
| --- | --- |
| `build_windows.ps1` | PyInstaller build, SDK/payload setup, smoke |
| `build_provenance.ps1` | app build version/source fingerprint |
| `build_installer.ps1` | Inno Setup installer build |
| `build_release_installers.ps1` | online/offline installers and manifest |
| `build_portable_release.ps1` | portable ZIP/manifest, verified app build reuse |
| `download_zaber_sdk.ps1` | Zaber Motion Library/Device DB download helper |
| `release_github.ps1` | Release creation/upload with gh or REST fallback |
| `run_packaged_smoke.ps1` | packaged exe smoke helper |
| `LinearStageControl.iss` | Inno Setup definition |

## 18. UI 현대화 작업 내용

`v0.1.5`에서 GUI는 기존 기본 박스 느낌을 줄이고 white mode 기반으로 정리했습니다.

주요 변경:

- 공통 stylesheet를 `gui_style.py`로 분리.
- card-like panel, 가벼운 border, restrained color 사용.
- 테이블 alternating row 적용.
- 버튼 role별 스타일: primary, danger, quiet.
- left control area spacing 정리.
- camera parameter section을 한 필드당 한 줄 중심으로 재배치.
- 빠른 증감 버튼을 2행 3열 grid로 정리.
- 좁은 폭에서 좌우 split을 상하 split으로 변경.
- preview 높이와 size policy를 조정해 작은 창에서도 겹침 감소.
- dialog에도 stylesheet 적용.
- 수동 스테이지 버튼과 위치 편집 버튼에 아이콘/툴팁 적용.

스크린샷 점검은 `scripts/capture_manual_screenshots.py`를 반복 실행하며 진행했습니다. 최종 매뉴얼용 기본 출력 경로는 다음입니다.

```text
%USERPROFILE%/Downloads/LinearStageControl_UI_Screenshots
```

## 19. 테스트 현황

릴리즈 전 확인한 명령:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_core_behaviour -v
.\.venv\Scripts\python.exe scripts\launch_gui.py --smoke-test
.\.venv\Scripts\python.exe scripts\xy_scan_capture.py --config config.yaml --dry-run
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format .
.\.venv\Scripts\python.exe -m pyright
dist\LinearStageControl\LinearStageControl.exe --smoke-test
git diff --check
```

통과한 테스트 범위:

- camera optional filters and pixel format candidates.
- extended Basler camera parameter parsing.
- output pixel format alias mapping.
- dataset filename with label/position/timestamp/capture index.
- dataset manifest with version, record count, hash.
- diagnostics without hardware checks.
- single-axis error calculation.
- CSV-only capture metadata and per-frame camera readbacks.
- GUI smoke components.
- centralized `AppRunState` button enablement.
- live fake frame preview update.
- live preview resize handle and bounded rendering.
- thin white overlay rendering.
- linear path preview widget rendering.
- linear spacing endpoint inclusion.
- flexible position input aliases.
- disabled axis preflight errors.
- stage axis setting parse.
- `settle_s` / `settle_ms` alias parse.
- Zaber Device DB optional path.
- updater version compare and SHA256 verify.

현재 단위 테스트 파일:

```text
tests/test_core_behaviour.py
```

## 20. 설정 예시 요약

핵심 config:

```yaml
camera:
  model_name:
  device_class:
  pixel_format: Mono8
  pixel_format_candidates: [Mono8, Mono10, Mono12, Mono16, BayerRG8, BayerGB8, BayerGR8, BayerBG8, RGB8, BGR8]
  exposure_us: 5000
  gain: 0
  acquisition_frame_rate:
  width:
  height:
  offset_x:
  offset_y:
  gamma:
  black_level:
  binning_x:
  binning_y:
  decimation_x:
  decimation_y:
  use_software_trigger: true
  trigger_selector: FrameStart
  trigger_source: Software
  live_preview:
    enabled: true
    fps: 10

stage:
  serial_port: COM3
  settle_s: 0.2
  move_velocity_mm_s:
  use_bundled_device_db: true
  axes:
    x: {enabled: true, device_index: 0, axis_number: 1}
    y: {enabled: true, device_index: 0, axis_number: 2}

dataset:
  image_format: png
  save_numpy: false
  # Capture metadata is always written to captures.csv.

updates:
  enabled: true
  repo: Simon-Choi-1028/linear-stage-control
```

## 21. 차후 개선 후보

현재 기능은 `v0.1.13` 기준으로 단위 테스트, 정적 검사, release verification, packaged smoke를 수행합니다. 실제 장비 운용이 늘어나면 다음 항목을 우선 검토하면 좋습니다.

- Basler GigE packet size, inter-packet delay, NIC IP 대역 상태를 진단 탭에서 더 직접적으로 표시.
- live preview 프레임 드롭률 진단.
- 장시간 run 중 camera reconnect recovery 정책 추가.
- Zaber 이동 중 latest command wins queue를 GUI 수동 이동에도 더 명확히 노출.
- 위치 테이블 대량 편집 기능 강화.
- run 완료 후 report PDF 또는 HTML 자동 생성.
- 실험 조건 template 저장/불러오기.
- 캘리브레이션 결과 비교용 multi-run summary view.
- hardware-in-the-loop 테스트 절차 문서화.
- Windows installer code signing.

## 22. 참고 문서

- `README.md`: 사용자용 실행/설치/기능 설명.
- `CHANGELOG.md`: 버전별 변경사항.
- `rules.md`: 제어 원칙과 개발 규칙.
- `config.example.yaml`: 설정 예시.
- `requirements.txt`: 실행 의존성 고정 버전. 빌드 전용 도구는 `pyproject.toml`의 `build` extra.
- `packaging/*.ps1`: 빌드/릴리즈 자동화.

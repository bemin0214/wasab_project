# Jetcobot Manipulation

Jetcobot 조작 프로젝트를 실행 위치 기준으로 정리한 최상위 폴더입니다.

```text
Jetcobot-manipulation/
├── Jetcobot-manipulation_server/  # 노트북에서 실행: YOLO 검출 + 2D->3D 파지 계획 서버
└── Jetcobot-manipulation_arm/     # 라즈베리파이/Jetcobot에서 실행: 카메라 캡처 + 로봇팔 제어 클라이언트
```

## 전체 동작 구조

1. 노트북에서 `Jetcobot-manipulation_server`를 실행합니다.
2. 서버는 `0.0.0.0:8000`에서 요청을 기다립니다.
3. 라즈베리파이/Jetcobot에서 `Jetcobot-manipulation_arm`을 실행합니다.
4. arm 클라이언트가 카메라 이미지와 현재 로봇 Flange pose를 노트북 서버로 보냅니다.
5. 노트북 서버가 YOLO 검출과 파지 좌표 계산을 수행한 뒤 `flange_command`를 돌려줍니다.
6. 라즈베리파이/Jetcobot 쪽에서 안전 범위를 확인한 뒤 로봇팔과 그리퍼를 움직입니다.

## 어디에서 무엇을 실행하는가

| 위치 | 폴더 | 역할 | 실행 파일 |
| --- | --- | --- | --- |
| 노트북 | `Jetcobot-manipulation_server` | YOLO 모델 실행, 파지 계획 계산, HTTP 서버 제공 | `run_server.py` |
| 라즈베리파이/Jetcobot | `Jetcobot-manipulation_arm` | 카메라 캡처, 서버 요청, 로봇팔/그리퍼 제어 | `run_client.py` |
| 라즈베리파이/Jetcobot | `Jetcobot-manipulation_arm` | 노트북 서버 연결 확인만 수행 | `check_server_connection.py` |

## 1. 노트북 서버 설치 및 실행

노트북에서 실행합니다.

```bash
cd /home/ane/dev_ws/Jetcobot-manipulation/Jetcobot-manipulation_server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_server.py
```

정상 실행 시 아래와 비슷하게 표시됩니다.

```text
[LAPTOP] YOLO + grasp-plan service
[LAPTOP] listening on http://0.0.0.0:8000
```

노트북에서 다른 터미널을 열어 서버 상태를 확인할 수 있습니다.

```bash
curl http://127.0.0.1:8000/health
```

### 노트북 서버 설정 파일

설정은 아래 파일에서 수정합니다.

```text
Jetcobot-manipulation_server/config/server_config.ini
```

주요 항목:

- `[server] host = 0.0.0.0`: 라즈베리파이가 노트북 LAN IP로 접속할 수 있게 유지합니다.
- `[server] port = 8000`: 서버 포트입니다.
- `[model] model_path = models/best.pt`: YOLO 모델 위치입니다.
- `[model] device = cuda:0`: GPU 사용 설정입니다. CPU만 사용할 경우 `cpu`로 바꿉니다.
- `[calibration] intrinsic_file`: 카메라 intrinsic 파일입니다.
- `[calibration] handeye_result_json`: hand-eye 보정 결과 파일입니다.
- `[request_validation] expected_image_width/height`: 라즈베리파이 카메라 해상도와 반드시 같아야 합니다.

`models/best.pt`가 없으면 서버가 정상 동작하지 않습니다. 모델 파일은 아래 위치에 둡니다.

```text
Jetcobot-manipulation_server/models/best.pt
```

## 2. 라즈베리파이/Jetcobot 클라이언트 설치 및 실행

라즈베리파이 또는 Jetcobot 제어 장치에서 실행합니다.

```bash
cd /home/ane/dev_ws/Jetcobot-manipulation/Jetcobot-manipulation_arm
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

먼저 노트북의 LAN IP를 확인합니다. 예를 들어 노트북 IP가 `192.168.0.20`이면 아래 설정 파일을 수정합니다.

```text
Jetcobot-manipulation_arm/config/client_config.ini
```

수정할 항목:

```ini
[network]
grasp_server_url = http://192.168.0.20:8000/v1/grasp-plan
```

주의: 라즈베리파이에서 `127.0.0.1`은 노트북이 아니라 라즈베리파이 자신입니다. 반드시 노트북의 LAN IP를 넣습니다.

연결만 먼저 확인합니다.

```bash
python3 check_server_connection.py
```

정상 연결이 확인되면 클라이언트를 실행합니다.

```bash
python3 run_client.py
```

### 실행 중 키 조작

`run_client.py` 실행 화면에서 사용합니다.

| 키 | 동작 |
| --- | --- |
| `g` | 현재 카메라 이미지와 로봇 pose를 노트북 서버로 보내고, 파지 계획을 받아 grasp 동작 수행 |
| `t` | grasp 이후 throw 동작 수행 |
| `w` | 물체를 잡은 상태에서 home 위치로 이동 |
| `q` | 종료 |


## 3. 노트북에서 로봇팔 카메라 보기

노트북 서버와 라즈베리파이/Jetcobot 클라이언트가 모두 실행 중이면, 노트북 브라우저에서 아래 주소를 엽니다.

```text
http://127.0.0.1:8000/camera-view
```

다른 PC나 태블릿에서 같은 화면을 보려면 노트북 LAN IP를 사용합니다.

```text
http://<노트북_LAN_IP>:8000/camera-view
```

예:

```text
http://192.168.0.20:8000/camera-view
```

이 화면은 로봇팔 카메라의 최신 프레임을 주기적으로 보여줍니다. YOLO 추론을 계속 돌리는 기능은 아니며, 미리보기 이미지만 노트북 서버로 전송합니다.

미리보기 전송 설정은 라즈베리파이/Jetcobot 쪽 아래 파일에서 바꿉니다.

```text
Jetcobot-manipulation_arm/config/client_config.ini
```

```ini
[camera_stream]
enabled = true
fps = 2.0
timeout_sec = 0.4
jpeg_quality = 70
```

`fps`를 높이면 화면은 더 부드러워지지만 라즈베리파이와 네트워크 부하가 늘어납니다.

## 4. 처음 테스트할 때 권장 순서

1. 노트북과 라즈베리파이/Jetcobot을 같은 LAN에 연결합니다.
2. 노트북에서 `Jetcobot-manipulation_server/config/server_config.ini`를 확인합니다.
3. 노트북에서 `python run_server.py`를 실행합니다.
4. 라즈베리파이/Jetcobot에서 `Jetcobot-manipulation_arm/config/client_config.ini`의 `grasp_server_url`을 노트북 IP로 수정합니다.
5. 라즈베리파이/Jetcobot에서 `python3 check_server_connection.py`로 연결을 확인합니다.
6. 처음에는 `Jetcobot-manipulation_arm/config/client_config.ini`의 `[safety] dry_run = true`로 두고 `python3 run_client.py`를 실행합니다.
7. 서버 응답 좌표와 화면 표시가 정상인지 확인합니다.
8. 충분히 확인한 뒤에만 `[safety] dry_run = false`로 바꾸고 실제 로봇 동작을 테스트합니다.

## 5. 캘리브레이션

캘리브레이션 스크립트는 라즈베리파이/Jetcobot 쪽 `Jetcobot-manipulation_arm` 폴더에서 실행합니다.

수동 캘리브레이션:

```bash
cd /home/ane/dev_ws/Jetcobot-manipulation/Jetcobot-manipulation_arm
source .venv/bin/activate
python3 marker.py
```

자동 캘리브레이션:

```bash
cd /home/ane/dev_ws/Jetcobot-manipulation/Jetcobot-manipulation_arm
source .venv/bin/activate
python3 auto_marker.py
```

생성되는 주요 결과 파일:

- `camera_intrinsic_charuco.npz`
- `auto_handeye_result_*.json`
- `auto_handeye_result_*.npz`
- `auto_handeye_charuco_samples_*.npz`

노트북 서버가 사용할 캘리브레이션 파일은 아래 서버 폴더에 맞게 복사하거나 `server_config.ini`의 경로를 수정합니다.

```text
Jetcobot-manipulation_server/calibration/
```

## 6. 자주 확인할 문제

- 노트북 서버가 켜져 있는지: `curl http://127.0.0.1:8000/health`
- 라즈베리파이에서 노트북 서버가 보이는지: `python3 check_server_connection.py`
- 노트북 방화벽에서 TCP 8000 포트가 허용되어 있는지
- `client_config.ini`의 `grasp_server_url`이 노트북 LAN IP인지
- `server_config.ini`의 `models/best.pt`가 실제 존재하는지
- 라즈베리파이 카메라 해상도와 서버 캘리브레이션 해상도가 같은지
- 노트북 카메라 보기 페이지: `http://<노트북_LAN_IP>:8000/camera-view`
- 실제 동작 전 `dry_run = true`로 응답 좌표를 먼저 확인했는지

## 7. 기존 개별 README

더 자세한 내부 설명은 각 폴더의 README를 참고합니다.

- `Jetcobot-manipulation_server/README.md`
- `Jetcobot-manipulation_server/README_LAPTOP.md`
- `Jetcobot-manipulation_arm/README.md`

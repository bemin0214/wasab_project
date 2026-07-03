# 노트북 로컬 YOLO + 로봇팔 파지계획

이 프로젝트는 기존 원격 딥러닝 서버가 하던 일을 **노트북에서 직접** 실행합니다.
라즈베리파이의 기존 HTTP 요청 형식은 변경하지 않아도 됩니다.

## 변경된 데이터 흐름

기존:

```text
Pi + 로봇팔 → (SSH 터널/노트북 경유) → 원격 딥러닝 서버
원격 딥러닝 서버 → YOLO + 2D→3D 파지계획 → 노트북/Pi → 로봇팔
```

변경 후:

```text
Pi + 로봇팔 → 노트북 LAN IP:8000 → YOLO + 2D→3D 파지계획 → Pi → 로봇팔
```

노트북은 다음을 한 프로세스에서 처리합니다.

1. Pi가 보낸 이미지와 현재 Flange pose `[x, y, z, rx, ry, rz]` 수신
2. YOLO 검출 및 최고 confidence 객체 선택
3. 카메라 intrinsic + Eye-in-Hand hand-eye 보정으로 객체 중심 픽셀을 Base 좌표계의 물체 평면과 교차
4. TCP offset을 반영해 MyCobot Flange command `[x, y, z, rx, ry, rz]` 생성
5. JSON 응답으로 Pi에 반환. 실제 모터 명령은 Pi의 기존 제어 코드가 수행

## 1. 노트북 준비

`models/best.pt`, `calibration/`이 포함된 이 폴더 전체를 노트북에 둡니다.

### Windows PowerShell

```powershell
cd laptop_yolo_robot_controller
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_server.py
```

### Linux/macOS

```bash
cd laptop_yolo_robot_controller
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_server.py
```

기본 설정은 CPU 추론입니다. NVIDIA GPU를 노트북에서 쓰려면 해당 환경에 맞는 PyTorch를 설치한 뒤 `config/server_config.ini`의 `device = cuda:0`으로 변경합니다.

서버가 시작되면 다음과 비슷한 메시지가 나와야 합니다.

```text
[LAPTOP] listening on http://0.0.0.0:8000
```

## 2. 네트워크 설정

1. 노트북과 Pi를 같은 사설 LAN에 연결합니다.
2. 노트북 IP를 확인합니다. 예: `192.168.0.20`.
3. Windows 방화벽을 사용하는 경우 Python 또는 TCP 8000을 **사설 네트워크**에서 허용합니다.
4. Pi의 기존 원격 서버 URL을 아래처럼 노트북 IP로만 변경합니다.

```text
기존: http://127.0.0.1:8000/v1/grasp-plan   # SSH 터널 종단 또는 원격 서버 주소
변경: http://192.168.0.20:8000/v1/grasp-plan
```

`/grasp-plan`과 `/v1/grasp-plan` 모두 유지되어 있으므로, Pi의 multipart field 이름도 그대로 사용합니다.

- file field: `image`
- form field: `robot_state`
- `robot_state` 예시: `{"request_id":"...","flange_coords":[x,y,z,rx,ry,rz]}`

## 3. 노트북 단독 확인

서버를 실행한 노트북에서 새 터미널을 열고 확인합니다.

```bash
curl http://127.0.0.1:8000/health
```

응답의 `runtime`이 `laptop-local`이고 `device`가 설정값과 같은지 확인합니다.

이미지 요청 예시는 다음과 같습니다.

```bash
curl -X POST "http://127.0.0.1:8000/v1/grasp-plan" \
  -F "image=@frame.jpg" \
  -F 'robot_state={"request_id":"test-001","flange_coords":[196.26,92.43,219.19,-177.46,4.93,-94.65]}'
```

Pi에서 HTTP만 검증하려면 다음 예제를 사용합니다. 이 예제는 **출력만 하고 로봇을 움직이지 않습니다.**

```bash
python3 examples/pi_http_client_example.py \
  --server http://192.168.0.20:8000 \
  --image /tmp/frame.jpg \
  --pose 196.26,92.43,219.19,-177.46,4.93,-94.65
```

## 4. 반드시 유지할 조건

- `calibration/camera_intrinsic_charuco.npz`의 보정 이미지 크기는 **640×480**입니다. Pi 카메라 캡처도 정확히 640×480으로 고정해야 합니다.
- Eye-in-Hand 구성에서는 **이미지를 찍은 순간의** 로봇 Flange pose를 함께 보내야 합니다. 촬영 후 로봇이 움직인 pose를 보내면 좌표가 틀어집니다.
- 손/물체 충돌 가능성이 있으므로 최초 검증에서는 Pi의 실제 `send_coords` 실행을 막고, 응답의 `plan.flange_command`만 기록하여 기존 원격 서버 결과와 비교합니다.
- 비교가 끝난 뒤에도 저속, 충분한 접근 높이, 비상정지 가능 상태에서 테스트합니다.

## 주요 수정 파일

- `config/server_config.ini`: 원격 절대경로/SSH 설정 제거, 노트북 LAN 수신 및 CPU 기본값 설정
- `run_server.py`: 노트북 실행용 메시지와 주소 안내
- `grasp_server.py`: 이전 직접 실행 명령을 새 구현으로 연결하는 호환 래퍼
- `app/main.py`: 서비스 명칭과 health 응답에 `runtime: laptop-local` 추가
- `scripts/start_laptop_server.*`: Windows, PowerShell, Linux/macOS 실행 스크립트
- `examples/pi_http_client_example.py`: Pi 통신 확인용 비구동 예제

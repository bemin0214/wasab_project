# Manipulation README

Jetcobot manipulation 실행 방법과 주요 기능 요약입니다. 작업 위치는 아래 폴더입니다.

```bash
cd /home/ane/dev_ws/Jetcobot-manipulation
```

## 실행 방법

노트북 서버:

```bash
cd /home/ane/dev_ws/Jetcobot-manipulation/Jetcobot-manipulation_server
source .venv/bin/activate
python run_server.py
```

라즈베리파이/Jetcobot 클라이언트:

```bash
cd /home/ane/dev_ws/Jetcobot-manipulation/Jetcobot-manipulation_arm
source .venv/bin/activate
python3 check_server_connection.py
python3 run_client.py
```

`check_server_connection.py`가 실패하면 `Jetcobot-manipulation_arm/config/client_config.ini`의 `grasp_server_url`을 노트북 LAN IP로 수정합니다.

```ini
[network]
grasp_server_url = http://<노트북_LAN_IP>:8000/v1/grasp-plan
```

## 주요 기능

| 키/명령 | 기능 |
| --- | --- |
| `g` / `grasp` | 현재 카메라 이미지와 로봇 pose를 서버로 보내고 grasp 동작 수행 |
| `a` / `find-marker` | April marker가 보일 때까지 J1을 좌우로 계속 스캔하고, marker를 찾으면 정지 |
| `t` / `throw` | grasp 이후 throw 동작 수행 |
| `w` / `home` | 물체를 잡은 상태에서 home 위치로 이동 |
| `q` | 클라이언트 종료 |

노트북 AdminGUI에서는 `Find Marker` 버튼으로 marker 찾기 명령을 보낼 수 있습니다.

## April Marker 찾기 설정

marker 찾기 기능은 라즈베리파이/Jetcobot 클라이언트에서 카메라 프레임을 확인하면서 J1을 좌우로 움직입니다. 설정 파일:

```text
Jetcobot-manipulation_arm/config/client_config.ini
```

```ini
[marker_search]
enabled = true
dictionary = DICT_APRILTAG_36h11
target_ids =
pan_joint = 1
pan_range_deg = 50.0
search_step_deg = 3.0
speed = 25
hz = 8.0
max_duration_sec = 0.0
```

- `target_ids`를 비워두면 모든 April marker를 찾습니다.
- `target_ids = 1,2,3`처럼 입력하면 지정한 ID만 찾습니다.
- `max_duration_sec = 0.0`이면 marker를 찾을 때까지 계속 탐색합니다.
- 실제 로봇을 움직이기 전에는 `[safety] dry_run = true`로 먼저 동작을 확인합니다.

# WaSaB 통합 시스템

배포 코드 구조 · 구성요소 역할 · 데이터 및 명령 흐름

## 시스템 개요

Service

### AI 및 통합 서버

객체·얼굴·화재 인식, 로봇팔 명령 API, 인증, 로봇 상태와 이벤트를 처리합니다.

Device

### Robot & Robot Arm

Pinky 이동·순찰·도킹과 JetCobot 팔 제어, 카메라, 그리퍼 동작을 수행합니다.

UI

### 통합 Web GUI

수업 보조, 순찰, 로봇암 현황, 알림과 비상정지를 한 화면에서 제공합니다.

## 실행 및 데이터 흐름

**PyQt 통합 실행기** - 선택 항목·IP·Domain

→

**AI Server :8000** - 검출·팔 API·카메라

↔

**WebService :8100** - 인증·프록시·ROS bridge

↔

**Web GUI** - 사용자 명령·상태·알림

**Left / Right Arm** - HTTP/UDP·카메라·관절 제어

↔

**AI Server** - 명령 전달 및 비전 결과

↔

**WebService** - 웹 API와 ESTOP 중계

↔

**Pinky ROS 2** - Domain 51~54 / Console 50

## 최상위 코드 구조

```
roscamp-repo-3/
├── Device/
│   └── WasabBot/
│       ├── WasabArmController/    # 왼팔·오른팔 클라이언트, 보정값, mimic 기능
│       ├── WasabController/       # 장치 제어 구성
│       └── WasabMoveController/   # Pinky bringup, 센서, 이동 및 navigation
│           ├── wasab_navigation/  # localization·docking·patrol
│           └── wasab_robot_agent/ # heartbeat·명령 relay
├── Service/
│   ├── WasabAIServer/
│   │   ├── AIService/ai_service/  # FastAPI AI/로봇팔 서버 (:8000)
│   │   ├── AIService/face-recog/  # 얼굴인식·화재감지·추종
│   │   └── FaceDB/                # 등록 얼굴과 encoding
│   └── WasabServer/
│       ├── Launcher/              # PyQt6 통합 실행기
│       ├── WebService/            # 통합 웹 API (:8100), ROS/팔 프록시
│       ├── scripts/               # 통합 실행·종료·순찰 보조 스크립트
│       ├── OpService/             # 운영 서비스
│       └── DB/                    # 서비스 데이터
├── UI/
│   ├── mobile/user_gui/frontend/  # HTML/CSS/JavaScript 통합 웹앱
│   └── pc/                        # Desktop/Admin GUI 및 실행 항목
```

## 핵심 파일

| 파일 | 역할 |
| --- | --- |
| `Launcher/wasab_launcher.py` | 선택한 서버, 팔, Pinky, GUI를 시작·종료하는 PyQt 실행기 |
| `ai_service/run_server.py` | YOLO, grasp plan과 로봇팔 API가 포함된 AI 서버 시작점 |
| `WasabArmController/run_client.py` | 각 JetCobot의 카메라·관절·그리퍼 명령 클라이언트 |
| `WebService/wasab_web_service/main.py` | 웹 API 조립, 인증, ROS bridge와 정적 GUI 서빙 |
| `WebService/wasab_web_service/server.py` | 명령·상태·이벤트·로봇팔 프록시 API 라우트 |
| `frontend/index.html` | 통합 GUI 화면과 메뉴 구조 |
| `frontend/app.js` | GUI 상태, API 통신, 기능 매핑, 알림과 ESTOP 처리 |
| `frontend/styles.css` | 모바일/데스크톱 반응형 디자인 |
| `config/robots.yaml` | Pinky ID, 주소, 역할 등 WebService 로봇 구성 |

## 웹 기능 매핑

| 사용자 기능 | 대상 | 내부 명령 | 동작 |
| --- | --- | --- | --- |
| 선물주기 | Dual Arm | `gift-giving` | 양팔 선물 전달 시나리오 |
| 교보재 올리기 | Left Arm | `help` | AprilTag 0 교보재 Pick & Place |
| 분리수거 | Left Arm | `recycle` | trash/water를 빨강/파랑 수거함으로 분류 |
| 비상정지 | Pinky / Left / Right / Dual | `stop` | 선택 장치 또는 전체 장치 즉시 정지 |

## 운영 시 주의사항

로봇을 실행하기 전에 작업 반경을 비우고 카메라·시리얼 장치·IP·ROS Domain을 확인하세요. 배포 전 SSH 인증 정보와 웹 관리자 데이터를 운영 환경에 맞게 변경해야 합니다.

WaSaB Integrated Deployment · 2026-08-03

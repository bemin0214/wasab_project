#!/usr/bin/env python3
# encoding: utf-8
"""노트북 로컬 YOLO 검출 + 3D 파지계획 FastAPI 서비스.

라즈베리파이는 SSH 터널 없이 노트북의 LAN IP로 프레임과 현재 Flange pose를
전송한다. ``/detect``, ``/grasp-plan``, ``/v1/grasp-plan`` 요청 형식과
응답 형식은 기존 원격 딥러닝 서버와 호환된다.
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Condition, Lock
from typing import Any, Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel
from ultralytics import YOLO

from .geometry import Calibration, PlanError, compute_grasp_plan, load_calibration
from .settings import settings


# ============================================================
# 1. 응답 형식: 기존 /detect API 호환
# ============================================================

class Detection(BaseModel):
    label: str
    class_id: int
    confidence: float
    bbox: list[float]      # [x1, y1, x2, y2]
    center: list[float]    # [u, v]
    width: float
    height: float


class InferResponse(BaseModel):
    status: str
    image_width: int
    image_height: int
    inference_ms: float
    detections: list[Detection]
    saved_dir: Optional[str] = None
    raw_image_path: Optional[str] = None
    annotated_image_path: Optional[str] = None
    result_json_path: Optional[str] = None


# ============================================================
# 2. 서버 상태
# ============================================================

class ServerState:
    model: YOLO
    calibration: Calibration
    inference_lock: Lock
    latest_frame_lock: Lock
    latest_frame_jpeg: bytes | None
    latest_frame_meta: dict[str, Any] | None
    detection_overlay_until: float
    detection_overlay_detections: list[Detection]
    detection_overlay_summary: str | None
    command_lock: Lock
    command_condition: Condition
    command_queue: list[dict[str, Any]]
    command_seq: int


state = ServerState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.euler_order != "zyx":
        raise RuntimeError("This project currently supports only EULER_ORDER=zyx")
    if not settings.model_path.exists():
        raise FileNotFoundError(f"YOLO model not found: {settings.model_path}")

    # 모델과 calibration은 서버 시작 시 한 번만 로드합니다.
    state.model = YOLO(str(settings.model_path))
    state.calibration = load_calibration(settings)
    state.inference_lock = Lock()
    state.latest_frame_lock = Lock()
    state.latest_frame_jpeg = None
    state.latest_frame_meta = None
    state.detection_overlay_until = 0.0
    state.detection_overlay_detections = []
    state.detection_overlay_summary = None
    state.command_lock = Lock()
    state.command_condition = Condition(state.command_lock)
    state.command_queue = []
    state.command_seq = 0

    print("[STARTUP] YOLO model:", settings.model_path)
    print("[STARTUP] device:", settings.device)
    print("[STARTUP] intrinsic:", settings.intrinsic_file)
    print("[STARTUP] hand-eye:", settings.handeye_result_json)
    print("[STARTUP] hand-eye method:", state.calibration.selected_method)
    yield


app = FastAPI(title="Laptop YOLO + Robot Grasp Planning Service", version="2.1.0", lifespan=lifespan)


# ============================================================
# 3. 기본 API
# ============================================================

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "runtime": "laptop-local",
        "model_path": str(settings.model_path),
        "device": settings.device,
        "default_conf": settings.default_conf,
        "default_target_label": settings.default_target_label,
        "euler_order": settings.euler_order,
        "calibration_method": state.calibration.selected_method,
    }


@app.get("/camera-view", response_class=HTMLResponse)
def camera_view() -> str:
    """브라우저에서 로봇팔 카메라와 원격 동작 버튼을 확인합니다."""
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Jetcobot Camera View</title>
  <style>
    :root { color-scheme: dark; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
      background: #111;
      color: #eee;
    }
    header {
      display: flex;
      gap: 16px;
      align-items: center;
      justify-content: space-between;
      padding: 12px 16px;
      border-bottom: 1px solid #333;
      background: #181818;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    #status {
      font-size: 14px;
      color: #bbb;
      white-space: nowrap;
    }
    .actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    button {
      border: 1px solid #444;
      border-radius: 6px;
      background: #242424;
      color: #f2f2f2;
      min-width: 42px;
      height: 34px;
      padding: 0 12px;
      font: inherit;
      font-size: 14px;
      cursor: pointer;
    }
    button:hover { background: #303030; }
    button:active { transform: translateY(1px); }
    button.danger {
      border-color: #7a2d2d;
      background: #3a1d1d;
    }
    main {
      min-height: calc(100vh - 50px);
      display: grid;
      place-items: center;
      padding: 16px;
      box-sizing: border-box;
    }
    img {
      max-width: 100%;
      max-height: calc(100vh - 92px);
      background: #050505;
      border: 1px solid #333;
      object-fit: contain;
    }
  </style>
</head>
<body>
  <header>
    <h1>Jetcobot Camera</h1>
    <div class="actions">
      <button id="liveButton" title="show live camera stream">Live</button>
      <button id="detectButton" title="run YOLO on latest frame and show scores">Detect</button>
      <button data-command="g" title="plan and grasp">G</button>
      <button data-command="p" title="print current pose">P</button>
      <button data-command="q" title="toggle gripper">Q</button>
      <button data-command="r" title="safe random pose">R</button>
      <button data-command="s" title="release all servos">S</button>
      <button data-command="f" title="focus all servos">F</button>
      <button data-command="m" title="move to configured pose">M</button>
      <button data-command="t" title="throw">T</button>
      <button data-command="w" title="force home">W</button>
      <button class="danger" data-command="x" title="stop and quit">X</button>
      <div id="status">waiting for frame...</div>
    </div>
  </header>
  <main>
    <img id="frame" alt="latest robot camera frame">
  </main>
  <script>
    const img = document.getElementById("frame");
    const statusEl = document.getElementById("status");

    async function sendCommand(command) {
      statusEl.textContent = `sending ${command.toUpperCase()}...`;
      try {
        const response = await fetch(`/robot-command/${command}`, {
          method: "POST",
          cache: "no-store",
        });
        const data = await response.json();
        if (!response.ok) {
          statusEl.textContent = data.detail || `command ${command} failed`;
          return;
        }
        statusEl.textContent = `queued ${data.command.toUpperCase()} #${data.id}`;
      } catch {
        statusEl.textContent = "command send failed";
      }
    }

    document.querySelectorAll("button[data-command]").forEach((button) => {
      button.addEventListener("click", () => sendCommand(button.dataset.command));
    });

    function showLiveStream() {
      img.src = `/camera-frame/stream.mjpg?t=${Date.now()}`;
      statusEl.textContent = "live stream";
    }

    async function showDetectionPreview() {
      statusEl.textContent = "running YOLO...";
      try {
        const response = await fetch(`/camera-frame/detect?t=${Date.now()}`, {
          method: "POST",
          cache: "no-store",
        });
        const data = await response.json();
        if (!response.ok) {
          statusEl.textContent = data.detail || "detect failed";
          return;
        }
        statusEl.textContent = `YOLO ${data.detection_count} objects | ${data.inference_ms.toFixed(1)}ms`;
      } catch {
        statusEl.textContent = "detect request failed";
      }
    }

    document.getElementById("liveButton").addEventListener("click", showLiveStream);
    document.getElementById("detectButton").addEventListener("click", showDetectionPreview);

    showLiveStream();

    async function refreshStatus() {
      const cacheBust = Date.now();
      try {
        const response = await fetch(`/camera-frame/status?t=${cacheBust}`, { cache: "no-store" });
        if (!response.ok) {
          statusEl.textContent = "waiting for arm camera stream...";
          return;
        }
        const data = await response.json();
        statusEl.textContent = `${data.width}x${data.height} | ${data.source} | ${data.age_sec.toFixed(1)}s ago`;
      } catch {
        statusEl.textContent = "server not reachable";
      }
    }

    refreshStatus();
    setInterval(refreshStatus, 1000);
  </script>
</body>
</html>"""


@app.post("/robot-command/{command}")
def enqueue_robot_command(command: str) -> dict[str, Any]:
    """브라우저 버튼에서 Pi 클라이언트가 실행할 단일 문자 명령을 큐에 넣습니다."""
    normalized = command.lower().strip()
    if normalized not in {"g", "p", "q", "r", "s", "f", "m", "t", "w", "x"}:
        raise HTTPException(status_code=400, detail="command must be one of: g, p, q, r, s, f, m, t, w, x")

    now = time.time()
    with state.command_lock:
        state.command_seq += 1
        item = {
            "id": state.command_seq,
            "command": normalized,
            "timestamp": now,
            "timestamp_iso": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "source": "camera-view",
        }
        state.command_queue.append(item)
        pending = len(state.command_queue)
        state.command_condition.notify_all()
    return {"status": "queued", "pending": pending, **item}


@app.get("/robot-command/stream")
def stream_robot_commands() -> StreamingResponse:
    """Stream queued browser commands to the Pi without polling."""
    def generate():
        while True:
            with state.command_condition:
                while not state.command_queue:
                    state.command_condition.wait(timeout=15.0)
                    if not state.command_queue:
                        yield json.dumps({"status": "heartbeat"}).encode() + b"\n"
                item = state.command_queue.pop(0)
                pending = len(state.command_queue)
            payload = {"status": "ok", "pending": pending, **item}
            yield json.dumps(payload).encode() + b"\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# ============================================================
# 4. 공통 유틸
# ============================================================

def _dump(model: BaseModel) -> dict[str, Any]:
    """Pydantic v1/v2 양쪽에서 동작하도록 직렬화."""
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[attr-defined]
    return model.dict()


def decode_image(file_bytes: bytes) -> np.ndarray:
    np_arr = np.frombuffer(file_bytes, np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("이미지 디코딩 실패")
    return image


def _latest_frame_meta(frame: np.ndarray, source: str) -> dict[str, Any]:
    h, w = frame.shape[:2]
    now = time.time()
    return {
        "timestamp": now,
        "timestamp_iso": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "width": w,
        "height": h,
        "source": source,
    }


def store_latest_frame(frame: np.ndarray, source: str) -> None:
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if not ok:
        raise ValueError("최신 카메라 프레임 JPEG 인코딩 실패")
    with state.latest_frame_lock:
        state.latest_frame_jpeg = encoded.tobytes()
        state.latest_frame_meta = _latest_frame_meta(frame, source)


def _draw_detection_summary(frame: np.ndarray, summary: str | None) -> None:
    if not summary:
        return
    cv2.putText(
        frame,
        summary,
        (18, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )


def store_latest_uploaded_jpeg(raw_jpeg: bytes, frame: np.ndarray, source: str) -> None:
    # Preview frames are already JPEG-encoded on the Pi. Reusing those bytes avoids
    # a decode/re-encode cycle unless a short-lived detection overlay is active.
    with state.latest_frame_lock:
        if time.time() < state.detection_overlay_until:
            annotated = draw_detections(frame, state.detection_overlay_detections)
            _draw_detection_summary(annotated, state.detection_overlay_summary)
            ok, encoded = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if ok:
                state.latest_frame_jpeg = encoded.tobytes()
            else:
                state.latest_frame_jpeg = raw_jpeg
        else:
            state.detection_overlay_detections = []
            state.detection_overlay_summary = None
            state.latest_frame_jpeg = raw_jpeg
        state.latest_frame_meta = _latest_frame_meta(frame, source)


@app.post("/camera-frame")
async def camera_frame(image: UploadFile = File(...)) -> dict[str, Any]:
    """라즈베리파이/Jetcobot 클라이언트가 실시간 보기용 최신 프레임을 업로드합니다."""
    raw = await image.read()
    frame = _read_and_decode_upload(image, raw)
    store_latest_uploaded_jpeg(raw, frame, "stream")
    with state.latest_frame_lock:
        meta = dict(state.latest_frame_meta or {})
    return {"status": "ok", **meta}


@app.get("/camera-frame/latest.jpg")
def latest_camera_frame() -> Response:
    with state.latest_frame_lock:
        jpeg = state.latest_frame_jpeg
    if jpeg is None:
        raise HTTPException(status_code=404, detail="아직 업로드된 카메라 프레임이 없습니다.")
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.post("/camera-frame/detect")
def latest_camera_frame_detect(
    conf: float = Query(settings.default_conf, ge=0.0, le=1.0),
    imgsz: int = Query(settings.default_imgsz, ge=32),
) -> dict[str, Any]:
    """Run YOLO on the latest uploaded frame and publish the annotated result to MJPEG."""
    with state.latest_frame_lock:
        jpeg = state.latest_frame_jpeg
        meta = dict(state.latest_frame_meta) if state.latest_frame_meta else None
    if jpeg is None or meta is None:
        raise HTTPException(status_code=404, detail="아직 업로드된 카메라 프레임이 없습니다.")

    try:
        frame = decode_image(jpeg)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"최신 카메라 프레임 디코딩 실패: {exc}") from exc

    try:
        detections, inference_ms = _run_inference(
            frame=frame,
            conf=conf,
            imgsz=imgsz,
            target_label=None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"YOLO inference failed: {exc}") from exc

    annotated = draw_detections(frame, detections)
    summary = f"detections={len(detections)} inference_ms={inference_ms:.1f}"
    _draw_detection_summary(annotated, summary)
    with state.latest_frame_lock:
        state.detection_overlay_until = time.time() + 3.0
        state.detection_overlay_detections = detections
        state.detection_overlay_summary = summary
    store_latest_frame(annotated, "detect-result")
    return {
        "status": "ok",
        "detection_count": len(detections),
        "inference_ms": inference_ms,
        "detections": [_dump(det) for det in detections],
    }


@app.get("/camera-frame/stream.mjpg")
def stream_camera_frame() -> StreamingResponse:
    def generate():
        last_timestamp = None
        while True:
            with state.latest_frame_lock:
                jpeg = state.latest_frame_jpeg
                meta = dict(state.latest_frame_meta) if state.latest_frame_meta else None
            if jpeg is None or meta is None:
                time.sleep(0.02)
                continue

            timestamp = meta.get("timestamp")
            if timestamp == last_timestamp:
                time.sleep(0.01)
                continue
            last_timestamp = timestamp

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                + jpeg
                + b"\r\n"
            )

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/camera-frame/status")
def latest_camera_frame_status() -> dict[str, Any]:
    with state.latest_frame_lock:
        meta = dict(state.latest_frame_meta) if state.latest_frame_meta else None
    if meta is None:
        raise HTTPException(status_code=404, detail="아직 업로드된 카메라 프레임이 없습니다.")
    meta["age_sec"] = max(0.0, time.time() - float(meta["timestamp"]))
    return {"status": "ok", **meta}


def draw_detections(frame: np.ndarray, detections: list[Detection]) -> np.ndarray:
    annotated = frame.copy()
    for det in detections:
        x1, y1, x2, y2 = [int(round(v)) for v in det.bbox]
        u, v = [int(round(value)) for value in det.center]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            annotated,
            f"{det.label} {det.confidence:.2f}",
            (x1, max(y1 - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.circle(annotated, (u, v), 4, (0, 0, 255), -1)
    return annotated


def save_detection_result(
    *,
    frame: np.ndarray,
    annotated: np.ndarray,
    detections: list[Detection],
    image_width: int,
    image_height: int,
    inference_ms: float,
    conf: float,
    imgsz: int,
    target_label: Optional[str],
    extra_result: dict[str, Any] | None = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """기존 서버의 이미지/JSON 로그 기능을 유지합니다."""
    if not settings.save_results:
        return None, None, None, None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    save_dir = settings.save_root_dir / timestamp
    save_dir.mkdir(parents=True, exist_ok=True)

    raw_path = save_dir / "raw.jpg"
    annotated_path = save_dir / "annotated.jpg"
    result_path = save_dir / "result.json"

    cv2.imwrite(str(raw_path), frame)
    cv2.imwrite(str(annotated_path), annotated)

    result_data: dict[str, Any] = {
        "timestamp": timestamp,
        "model_path": str(settings.model_path),
        "device": settings.device,
        "request": {"conf": conf, "imgsz": imgsz, "target_label": target_label},
        "image": {
            "width": image_width,
            "height": image_height,
            "raw_image_path": str(raw_path),
            "annotated_image_path": str(annotated_path),
        },
        "inference_ms": inference_ms,
        "detections": [_dump(det) for det in detections],
    }
    if extra_result is not None:
        result_data["grasp_plan_result"] = extra_result

    with result_path.open("w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    return str(save_dir), str(raw_path), str(annotated_path), str(result_path)


def _validate_frame_size(frame: np.ndarray) -> None:
    h, w = frame.shape[:2]
    if settings.expected_image_width and w != settings.expected_image_width:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Image width {w} differs from EXPECTED_IMAGE_WIDTH "
                f"{settings.expected_image_width}; camera calibration may be invalid"
            ),
        )
    if settings.expected_image_height and h != settings.expected_image_height:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Image height {h} differs from EXPECTED_IMAGE_HEIGHT "
                f"{settings.expected_image_height}; camera calibration may be invalid"
            ),
        )


def _effective_target_label(requested: str | None) -> str | None:
    # API query가 주어지면 그 값을 사용하되, 없으면 서버 정책의 기본 클래스를 사용합니다.
    return requested if requested is not None else settings.default_target_label


def _run_inference(
    *,
    frame: np.ndarray,
    conf: float,
    imgsz: int,
    target_label: str | None,
) -> tuple[list[Detection], float]:
    start = time.perf_counter()
    # 단일 GPU 모델은 요청 간 동시 접근을 막아 메모리/결과 충돌을 피합니다.
    with state.inference_lock:
        results = state.model.predict(
            source=frame,
            conf=conf,
            imgsz=imgsz,
            device=settings.device,
            verbose=False,
        )
    inference_ms = (time.perf_counter() - start) * 1000.0

    detections: list[Detection] = []
    if not results or results[0].boxes is None:
        return detections, inference_ms

    result = results[0]
    names = result.names
    for box in result.boxes:
        x1, y1, x2, y2 = box.xyxy[0].detach().cpu().numpy().astype(float)
        score = float(box.conf[0].detach().cpu().item())
        class_id = int(box.cls[0].detach().cpu().item())
        label = str(names.get(class_id, class_id))
        if target_label is not None and label != target_label:
            continue

        u = (x1 + x2) / 2.0
        v = (y1 + y2) / 2.0
        detections.append(
            Detection(
                label=label,
                class_id=class_id,
                confidence=score,
                bbox=[x1, y1, x2, y2],
                center=[u, v],
                width=x2 - x1,
                height=y2 - y1,
            )
        )

    detections.sort(key=lambda item: item.confidence, reverse=True)
    return detections, inference_ms


def _read_and_decode_upload(image: UploadFile, raw: bytes) -> np.ndarray:
    if not raw:
        raise HTTPException(status_code=400, detail="빈 이미지 업로드입니다.")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="업로드 이미지가 MAX_UPLOAD_BYTES를 초과했습니다.")
    try:
        frame = decode_image(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _validate_frame_size(frame)
    return frame


def _parse_robot_state(raw: str) -> tuple[list[float], str | None]:
    """Pi가 보내는 `robot_state` form field를 검증합니다.

    형식: {"request_id":"...", "flange_coords":[x,y,z,rx,ry,rz]}
    """
    try:
        robot_state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="robot_state는 JSON 문자열이어야 합니다.") from exc

    coords = robot_state.get("flange_coords") if isinstance(robot_state, dict) else None
    if not isinstance(coords, list) or len(coords) != 6:
        raise HTTPException(status_code=400, detail="robot_state.flange_coords는 6개 숫자여야 합니다.")
    try:
        parsed = [float(value) for value in coords]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="flange_coords에 숫자가 아닌 값이 있습니다.") from exc
    if not np.isfinite(np.asarray(parsed, dtype=np.float64)).all():
        raise HTTPException(status_code=400, detail="flange_coords에 유한하지 않은 값이 있습니다.")

    request_id = robot_state.get("request_id")
    return parsed, str(request_id) if request_id is not None else None


# ============================================================
# 5. 기존 YOLO detect API (호환 유지)
# ============================================================

@app.post("/detect", response_model=InferResponse)
async def detect(
    image: UploadFile = File(...),
    conf: float = Query(settings.default_conf, ge=0.0, le=1.0),
    imgsz: int = Query(settings.default_imgsz, ge=32),
    target_label: Optional[str] = Query(None),
) -> InferResponse:
    raw = await image.read()
    frame = _read_and_decode_upload(image, raw)
    store_latest_frame(frame, "detect")
    h, w = frame.shape[:2]
    effective_target_label = _effective_target_label(target_label)

    try:
        detections, inference_ms = _run_inference(
            frame=frame,
            conf=conf,
            imgsz=imgsz,
            target_label=effective_target_label,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"YOLO inference failed: {exc}") from exc

    annotated = draw_detections(frame, detections)
    save_dir, raw_path, annotated_path, json_path = save_detection_result(
        frame=frame,
        annotated=annotated,
        detections=detections,
        image_width=w,
        image_height=h,
        inference_ms=inference_ms,
        conf=conf,
        imgsz=imgsz,
        target_label=effective_target_label,
    )
    print("[DETECT] raw:", raw_path)
    print("[DETECT] annotated:", annotated_path)
    print("[DETECT] json:", json_path)

    return InferResponse(
        status="ok",
        image_width=w,
        image_height=h,
        inference_ms=inference_ms,
        detections=detections,
        saved_dir=save_dir,
        raw_image_path=raw_path,
        annotated_image_path=annotated_path,
        result_json_path=json_path,
    )


# ============================================================
# 6. 서버측 YOLO + 3D 파지계획 API
# ============================================================

async def _create_grasp_plan(
    *,
    image: UploadFile,
    robot_state: str,
) -> dict[str, Any]:
    """노트북의 고정 정책으로 파지 계획을 생성합니다.

    라즈베리파이는 frame과 촬영 시점의 현재 Flange pose만 보내며,
    YOLO 및 모든 좌표변환은 이 함수 내부에서 완료합니다.
    """
    current_flange_coords, request_id = _parse_robot_state(robot_state)
    raw = await image.read()
    frame = _read_and_decode_upload(image, raw)
    h, w = frame.shape[:2]

    try:
        detections, inference_ms = _run_inference(
            frame=frame,
            conf=settings.default_conf,
            imgsz=settings.default_imgsz,
            target_label=settings.default_target_label,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"YOLO inference failed: {exc}") from exc

    annotated = draw_detections(frame, detections)
    store_latest_frame(annotated, "grasp-plan-result")
    extra_result: dict[str, Any] = {
        "request_id": request_id,
        "current_flange_coords": current_flange_coords,
    }

    if not detections:
        extra_result.update({"status": "not_found", "message": "Target object was not detected."})
        save_dir, raw_path, annotated_path, json_path = save_detection_result(
            frame=frame,
            annotated=annotated,
            detections=detections,
            image_width=w,
            image_height=h,
            inference_ms=inference_ms,
            conf=settings.default_conf,
            imgsz=settings.default_imgsz,
            target_label=settings.default_target_label,
            extra_result=extra_result,
        )
        return {
            "status": "not_found",
            "request_id": request_id,
            "message": "No target detection satisfied the server target-label/confidence policy.",
            "image_width": w,
            "image_height": h,
            "inference_ms": inference_ms,
            "detections": [_dump(det) for det in detections],
            "saved_dir": save_dir,
            "raw_image_path": raw_path,
            "annotated_image_path": annotated_path,
            "result_json_path": json_path,
        }

    # detections는 confidence 내림차순. 가장 높은 대상 하나를 서버가 선택합니다.
    selected = detections[0]
    try:
        result = compute_grasp_plan(
            detection=_dump(selected),
            current_flange_coords=current_flange_coords,
            calibration=state.calibration,
            settings=settings,
        )
    except PlanError as exc:
        raise HTTPException(status_code=422, detail=f"Grasp-plan geometry error: {exc}") from exc

    extra_result.update({"status": "ok", **result})
    save_dir, raw_path, annotated_path, json_path = save_detection_result(
        frame=frame,
        annotated=annotated,
        detections=detections,
        image_width=w,
        image_height=h,
        inference_ms=inference_ms,
        conf=settings.default_conf,
        imgsz=settings.default_imgsz,
        target_label=settings.default_target_label,
        extra_result=extra_result,
    )

    return {
        "status": "ok",
        "request_id": request_id,
        "image_width": w,
        "image_height": h,
        "inference_ms": inference_ms,
        "detections": [_dump(det) for det in detections],
        "server_policy": {
            "target_label": settings.default_target_label,
            "confidence_threshold": settings.default_conf,
            "object_plane_z_base_mm": settings.object_plane_z_base_mm,
            "target_z_offset_mm": settings.toothbrush_target_z_offset_mm,
        },
        **result,
        "saved_dir": save_dir,
        "raw_image_path": raw_path,
        "annotated_image_path": annotated_path,
        "result_json_path": json_path,
    }


@app.post("/grasp-plan")
async def grasp_plan(
    image: UploadFile = File(...),
    robot_state: str = Form(...),
) -> dict[str, Any]:
    return await _create_grasp_plan(image=image, robot_state=robot_state)


# 이전 분리형 클라이언트와 호환되는 alias입니다.
@app.post("/v1/grasp-plan")
async def grasp_plan_v1(
    image: UploadFile = File(...),
    robot_state: str = Form(...),
) -> dict[str, Any]:
    return await _create_grasp_plan(image=image, robot_state=robot_state)

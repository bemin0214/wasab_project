"""Raspberry Pi execution entry point for the laptop-local YOLO grasp service.

Keys:
  g: capture a fresh 640x480 frame + current Flange pose -> laptop plan -> validate -> grasp
  p: print current Flange pose
  q: toggle gripper close/open
  r: move to a safe random pose around home
  s: release all servos so the arm can be moved by hand
  f: focus/enable all servos
  m: move to configured manual Flange pose
  t: run the existing throw motion after a successful grasp
  w: stop current motion and return home
  x: request stop and exit

The laptop performs YOLO and 2D->3D grasp planning. This Pi remains responsible
for camera capture, MyCobot/gripper control, and the final local safety gate.
"""
from __future__ import annotations

import json
import math
import os
import random
import threading
import time
from queue import Empty, Queue
from typing import Any

import cv2

from . import config
from .api_client import (
    GraspServerError,
    check_server_health,
    request_grasp_plan,
    upload_camera_frame,
    stream_robot_commands,
)
from .robot_controller import RobotController


def _is_in_range(value: float, limits: tuple[float, float]) -> bool:
    return limits[0] <= value <= limits[1]


def validate_server_plan(
    payload: dict[str, Any],
) -> tuple[bool, str, list[float] | None]:
    """Run the final Pi-side safety validation before any robot command."""
    if payload.get("status") != "ok":
        return (
            False,
            str(payload.get("message", "target not found")),
            None,
        )

    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return False, "response.plan is missing", None

    command = plan.get("flange_command")
    if not isinstance(command, list) or len(command) != 6:
        return False, "flange_command must contain six values", None

    try:
        command = [float(v) for v in command]
    except (TypeError, ValueError):
        return False, "flange_command contains non-numeric values", None

    if not all(math.isfinite(v) for v in command):
        return False, "flange_command contains non-finite values", None

    if not _is_in_range(command[0], config.SAFE_X_MM):
        return False, f"unsafe X={command[0]:.1f} mm", None
    if not _is_in_range(command[1], config.SAFE_Y_MM):
        return False, f"unsafe Y={command[1]:.1f} mm", None
    if not _is_in_range(command[2], config.SAFE_Z_MM):
        return False, f"unsafe Z={command[2]:.1f} mm", None
    if any(abs(v) > config.SAFE_EULER_ABS_DEG for v in command[3:]):
        return False, "unsafe Euler value", None

    return True, "ok", command



def _clamp(value: float, limits: tuple[float, float]) -> float:
    return max(limits[0], min(limits[1], value))


def make_safe_random_flange_coords() -> list[float]:
    """Generate a conservative random flange pose near HOME and inside safety limits."""
    home = [float(value) for value in config.HOME_FLANGE_COORDS]
    target = [
        _clamp(
            home[0] + random.uniform(-config.RANDOM_X_RADIUS_MM, config.RANDOM_X_RADIUS_MM),
            config.SAFE_X_MM,
        ),
        _clamp(
            home[1] + random.uniform(-config.RANDOM_Y_RADIUS_MM, config.RANDOM_Y_RADIUS_MM),
            config.SAFE_Y_MM,
        ),
        _clamp(
            home[2] + random.uniform(-config.RANDOM_Z_RADIUS_MM, config.RANDOM_Z_RADIUS_MM),
            config.SAFE_Z_MM,
        ),
        home[3],
        home[4],
        home[5],
    ]
    return [round(value, 2) for value in target]

def validate_frame_size(frame) -> None:
    """Reject frames that do not match the laptop's camera-calibration resolution."""
    if frame is None or getattr(frame, "ndim", 0) < 2:
        raise RuntimeError("Camera frame is invalid")

    height, width = frame.shape[:2]
    if (
        width != config.CAMERA_FRAME_WIDTH
        or height != config.CAMERA_FRAME_HEIGHT
    ):
        raise RuntimeError(
            "Camera frame size mismatch: "
            f"got {width}x{height}, expected "
            f"{config.CAMERA_FRAME_WIDTH}x{config.CAMERA_FRAME_HEIGHT}. "
            "The laptop calibration is valid only at the configured resolution."
        )


def open_calibrated_camera():
    """Open the Pi camera and make resolution mismatch fail before robot startup."""
    cap = cv2.VideoCapture(config.CAMERA_ID, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(
            f"Cannot open camera: CAMERA_ID={config.CAMERA_ID!r}. "
            "Check the real capture device with `v4l2-ctl --list-devices` or "
            "`ls -l /dev/video*`, then set [camera] camera_id to that index "
            "or path, for example /dev/video2."
        )

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_FRAME_HEIGHT)

    # Let USB/CSI cameras apply their requested capture format before validation.
    for _ in range(max(2, config.CAMERA_FLUSH_FRAMES)):
        cap.grab()

    ret, probe_frame = cap.read()
    if not ret or probe_frame is None:
        with cap_lock:
            cap.release()
        raise RuntimeError("Cannot read initial camera frame")

    try:
        validate_frame_size(probe_frame)
    except Exception:
        with cap_lock:
            cap.release()
        raise

    print(
        "[CAMERA] calibrated capture size: "
        f"{config.CAMERA_FRAME_WIDTH}x{config.CAMERA_FRAME_HEIGHT}"
    )
    return cap


def capture_fresh_plan_frame(cap):
    """Capture a recent frame when G is pressed, reducing buffered-camera latency."""
    for _ in range(config.CAMERA_FLUSH_FRAMES):
        cap.grab()

    ret, frame = cap.read()
    if not ret or frame is None:
        raise RuntimeError("Cannot capture a current camera frame for grasp planning")

    validate_frame_size(frame)
    return frame


def draw_result(
    frame,
    payload: dict[str, Any] | None,
    error: str | None,
    throw_running: bool,
) -> None:
    text_y = 32

    cv2.putText(
        frame,
        "g grasp | p pose | q grip | r random | s servo off | f servo on | m move | t throw | w home | x exit",
        (18, text_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (0, 0, 255),
        2,
    )
    text_y += 32

    if config.DRY_RUN:
        cv2.putText(
            frame,
            "DRY RUN: robot motion disabled",
            (18, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (0, 165, 255),
            2,
        )
        text_y += 32

    if throw_running:
        cv2.putText(
            frame,
            "THROW MODE RUNNING",
            (18, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (0, 0, 255),
            2,
        )
        text_y += 32

    if payload and payload.get("status") == "ok":
        det = payload.get("detection", {})
        bbox = det.get("bbox")
        midpoint = det.get("midpoint_uv")

        if isinstance(bbox, list) and len(bbox) == 4:
            x1, box_y1, x2, box_y2 = (
                int(round(float(v))) for v in bbox
            )
            cv2.rectangle(
                frame, (x1, box_y1), (x2, box_y2), (0, 255, 0), 2
            )
            label = (
                f"{det.get('label', 'object')} "
                f"{float(det.get('confidence', 0.0)):.2f}"
            )
            cv2.putText(
                frame,
                label,
                (x1, max(24, box_y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.60,
                (0, 255, 0),
                2,
            )

        if isinstance(midpoint, list) and len(midpoint) == 2:
            u, v = (int(round(float(value))) for value in midpoint)
            cv2.drawMarker(
                frame,
                (u, v),
                (0, 0, 255),
                cv2.MARKER_CROSS,
                20,
                2,
            )

        plan = payload.get("plan", {})
        tcp = plan.get("tcp_target_base_mm")
        if isinstance(tcp, list) and len(tcp) == 3:
            text = f"TCP Base: {tcp[0]:.1f}, {tcp[1]:.1f}, {tcp[2]:.1f} mm"
            cv2.putText(
                frame,
                text,
                (18, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (0, 255, 255),
                2,
            )
            text_y += 28

        command = plan.get("flange_command")
        if isinstance(command, list) and len(command) == 6:
            text = (
                f"Flange: {command[0]:.1f}, "
                f"{command[1]:.1f}, {command[2]:.1f} mm"
            )
            cv2.putText(
                frame,
                text,
                (18, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (255, 255, 0),
                2,
            )
            text_y += 28

    if error:
        cv2.putText(
            frame,
            error[:105],
            (18, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (0, 0, 255),
            2,
        )


def main() -> None:
    print("=== Raspberry Pi -> laptop-local robot grasp client ===")
    print("Laptop endpoint:", config.GRASP_SERVER_URL)
    print("Expected runtime:", config.EXPECTED_SERVER_RUNTIME)
    print("DRY_RUN:", config.DRY_RUN)
    print("Camera stream:", config.CAMERA_STREAM_ENABLED)
    print("Remote control:", config.REMOTE_COMMAND_ENABLED)

    if config.CHECK_SERVER_ON_STARTUP:
        try:
            health = check_server_health()
            print(
                "[NETWORK] laptop server reachable: "
                f"runtime={health.get('runtime')}, "
                f"device={health.get('device')}, "
                f"model={health.get('model_path')}"
            )
        except GraspServerError as exc:
            raise RuntimeError(
                "Laptop server preflight failed. Check the [network] "
                "grasp_server_url, laptop firewall, and shared LAN path before "
                f"starting robot control.\n{exc}"
            ) from exc

    cap = open_calibrated_camera()
    cap_lock = threading.Lock()
    robot = RobotController()

    if config.DRY_RUN:
        print("[SAFETY] DRY_RUN=True: Startup home/open is skipped.")
    else:
        robot.move_home_and_open_gripper()

    last_payload: dict[str, Any] | None = None
    last_payload_expires_at = 0.0
    last_error: str | None = None
    gripper_closed_on_target = False
    state_lock = threading.Lock()
    frame_condition = threading.Condition()
    capture_stop = threading.Event()
    stream_stop = threading.Event()
    latest_frame = None
    latest_frame_seq = 0
    last_stream_error_at = 0.0
    last_remote_command_error_at = 0.0
    remote_command_queue: Queue[str] = Queue()
    remote_stop = threading.Event()

    throw_running = threading.Event()
    throw_abort = threading.Event()
    throw_thread: threading.Thread | None = None

    def run_throw_worker() -> None:
        nonlocal gripper_closed_on_target, last_error
        try:
            success, message, released = robot.execute_throw_mode(
                abort_event=throw_abort
            )
            if released:
                gripper_closed_on_target = False
            if success:
                with state_lock:
                    last_error = None
                print("[THROW]", message)
            else:
                with state_lock:
                    last_error = f"THROW failed: {message}"
                print("[THROW]", last_error)
        except Exception as exc:
            with state_lock:
                last_error = f"THROW ERROR: {type(exc).__name__}: {exc}"
            print(last_error)
        finally:
            throw_running.clear()

    show_window = bool(config.SHOW_WINDOW)
    if show_window and not os.environ.get("DISPLAY"):
        show_window = False
        print("[UI] DISPLAY is not set; local OpenCV window disabled.")

    if show_window:
        cv2.namedWindow(config.WINDOW_NAME, cv2.WINDOW_NORMAL)

    def camera_capture_worker() -> None:
        nonlocal cap, latest_frame, latest_frame_seq, last_error, last_stream_error_at
        read_failures = 0
        while not capture_stop.is_set():
            with cap_lock:
                ret, frame = cap.read()
            now = time.monotonic()
            if not ret or frame is None:
                read_failures += 1
                if read_failures >= 30:
                    message = "camera read failed repeatedly; reopening camera"
                    with state_lock:
                        last_error = message
                    if now - last_stream_error_at >= 5.0:
                        print("[CAMERA]", message)
                        last_stream_error_at = now
                    try:
                        with cap_lock:
                            cap.release()
                            cap = open_calibrated_camera()
                        read_failures = 0
                    except Exception as exc:
                        with state_lock:
                            last_error = f"Camera reopen failed: {exc}"
                        time.sleep(1.0)
                else:
                    time.sleep(0.02)
                continue

            read_failures = 0
            try:
                validate_frame_size(frame)
            except RuntimeError as exc:
                with state_lock:
                    last_error = str(exc)
                if now - last_stream_error_at >= 5.0:
                    print("[CAMERA]", exc)
                    last_stream_error_at = now
                time.sleep(0.05)
                continue

            with frame_condition:
                latest_frame = frame
                latest_frame_seq += 1
                frame_condition.notify_all()

    def get_latest_frame(
        *,
        min_seq: int | None = None,
        timeout_sec: float = 0.5,
    ) -> tuple[object | None, int]:
        deadline = time.monotonic() + timeout_sec
        with frame_condition:
            while latest_frame is None or (min_seq is not None and latest_frame_seq <= min_seq):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                frame_condition.wait(timeout=remaining)
            if latest_frame is None:
                return None, latest_frame_seq
            return latest_frame.copy(), latest_frame_seq

    capture_thread = threading.Thread(target=camera_capture_worker, daemon=True)
    capture_thread.start()

    def snapshot_overlay_state() -> tuple[dict[str, Any] | None, str | None, bool]:
        with state_lock:
            payload = last_payload if time.monotonic() < last_payload_expires_at else None
            return payload, last_error, throw_running.is_set()

    def camera_stream_worker() -> None:
        nonlocal last_stream_error_at
        next_frame_at = 0.0
        last_uploaded_seq = 0
        while not stream_stop.is_set():
            now = time.monotonic()
            if now < next_frame_at:
                time.sleep(min(0.01, next_frame_at - now))
                continue
            next_frame_at = now + config.CAMERA_STREAM_INTERVAL_SEC

            stream_frame, seq = get_latest_frame(
                min_seq=last_uploaded_seq,
                timeout_sec=config.CAMERA_STREAM_INTERVAL_SEC,
            )
            if stream_frame is None:
                continue
            last_uploaded_seq = seq

            payload_snapshot, error_snapshot, throw_snapshot = snapshot_overlay_state()
            draw_result(stream_frame, payload_snapshot, error_snapshot, throw_snapshot)
            try:
                upload_camera_frame(stream_frame)
            except GraspServerError as exc:
                if now - last_stream_error_at >= 5.0:
                    print("[CAMERA STREAM]", exc)
                    last_stream_error_at = now

    stream_thread: threading.Thread | None = None
    if config.CAMERA_STREAM_ENABLED:
        stream_thread = threading.Thread(target=camera_stream_worker, daemon=True)
        stream_thread.start()

    def remote_command_stream_worker() -> None:
        nonlocal last_remote_command_error_at
        while not remote_stop.is_set():
            try:
                for remote_command in stream_robot_commands(remote_stop):
                    if remote_stop.is_set():
                        break
                    remote_command_queue.put(remote_command)
            except GraspServerError as exc:
                now = time.monotonic()
                if now - last_remote_command_error_at >= 5.0:
                    print("[REMOTE COMMAND]", exc)
                    last_remote_command_error_at = now
                remote_stop.wait(1.0)

    remote_thread: threading.Thread | None = None
    if config.REMOTE_COMMAND_ENABLED:
        remote_thread = threading.Thread(target=remote_command_stream_worker, daemon=True)
        remote_thread.start()

    try:
        while True:
            frame, frame_seq = get_latest_frame(timeout_sec=0.5)
            if frame is None:
                time.sleep(0.05)
                continue

            payload_snapshot, error_snapshot, throw_snapshot = snapshot_overlay_state()
            annotated = frame.copy()
            draw_result(
                annotated,
                payload_snapshot,
                error_snapshot,
                throw_snapshot,
            )
            if show_window:
                cv2.imshow(config.WINDOW_NAME, annotated)
                key = cv2.waitKey(1) & 0xFF
            else:
                key = 255

            if config.REMOTE_COMMAND_ENABLED:
                try:
                    remote_command = remote_command_queue.get_nowait()
                except Empty:
                    pass
                else:
                    key = ord(remote_command)

            if key == ord("p"):
                try:
                    coords = robot.get_flange_coords()
                    text = (
                        "POSE Flange: "
                        f"x={coords[0]:.1f}, y={coords[1]:.1f}, z={coords[2]:.1f}, "
                        f"rx={coords[3]:.2f}, ry={coords[4]:.2f}, rz={coords[5]:.2f}"
                    )
                    print("[POSE]", coords)
                    with state_lock:
                        last_error = text
                except Exception as exc:
                    with state_lock:
                        last_error = f"POSE ERROR: {type(exc).__name__}: {exc}"
                    print(last_error)
                continue

            if key == ord("q"):
                if throw_running.is_set():
                    with state_lock:
                        last_error = "Q ignored: throw mode is running"
                elif config.DRY_RUN:
                    with state_lock:
                        last_error = "DRY RUN: gripper command not sent"
                else:
                    try:
                        if gripper_closed_on_target:
                            robot.open_gripper()
                            gripper_closed_on_target = False
                            with state_lock:
                                last_error = "Gripper opened"
                        else:
                            robot.close_gripper()
                            gripper_closed_on_target = True
                            with state_lock:
                                last_error = "Gripper closed"
                    except RuntimeError as exc:
                        with state_lock:
                            last_error = f"GRIPPER ERROR: {exc}"
                        print(last_error)
                continue

            if key == ord("x"):
                if throw_running.is_set():
                    print("[THROW] abort requested")
                    throw_abort.set()
                    if not config.DRY_RUN:
                        try:
                            robot.stop_motion()
                        except Exception as exc:
                            print("[THROW] stop error:", exc)
                break

            if key == ord("s"):
                if throw_running.is_set():
                    print("[SERVO] aborting throw before servo release")
                    throw_abort.set()
                    if not config.DRY_RUN:
                        try:
                            robot.stop_motion()
                        except Exception as exc:
                            print("[SERVO] stop error:", exc)
                    if throw_thread is not None and throw_thread.is_alive():
                        throw_thread.join(timeout=1.0)
                    throw_running.clear()

                if config.DRY_RUN:
                    with state_lock:
                        last_error = "DRY RUN: servo release not sent"
                else:
                    try:
                        robot.release_all_servos()
                        with state_lock:
                            last_error = "Servos released"
                    except Exception as exc:
                        with state_lock:
                            last_error = f"SERVO RELEASE ERROR: {type(exc).__name__}: {exc}"
                        print(last_error)
                continue

            if key == ord("f"):
                if config.DRY_RUN:
                    with state_lock:
                        last_error = "DRY RUN: servo focus not sent"
                else:
                    try:
                        robot.focus_all_servos()
                        with state_lock:
                            last_error = "Servos focused"
                    except Exception as exc:
                        with state_lock:
                            last_error = f"SERVO FOCUS ERROR: {type(exc).__name__}: {exc}"
                        print(last_error)
                continue

            if key == ord("m"):
                if throw_running.is_set():
                    with state_lock:
                        last_error = "M ignored: throw mode is running"
                elif not config.MANUAL_MOTION_ENABLED:
                    with state_lock:
                        last_error = "M ignored: manual motion is disabled"
                elif config.DRY_RUN:
                    with state_lock:
                        last_error = f"DRY RUN: manual command not sent {config.MANUAL_FLANGE_COORDS}"
                    print("[DRY RUN] Manual flange command:", config.MANUAL_FLANGE_COORDS)
                else:
                    pseudo_payload = {"status": "ok", "plan": {"flange_command": config.MANUAL_FLANGE_COORDS}}
                    is_safe, reason, safe_command = validate_server_plan(pseudo_payload)
                    if not is_safe or safe_command is None:
                        with state_lock:
                            last_error = f"Manual pose rejected locally: {reason}"
                        print("[SAFETY]", last_error)
                    else:
                        print("[MANUAL] configured flange command:", safe_command)
                        try:
                            reached = robot.move_manual_flange_coords()
                        except Exception as exc:
                            with state_lock:
                                last_error = f"MANUAL MOVE ERROR: {type(exc).__name__}: {exc}"
                            print(last_error)
                        else:
                            with state_lock:
                                last_error = None if reached else "Manual pose timeout"
                continue

            if key == ord("t"):
                if config.DRY_RUN:
                    with state_lock:
                        last_error = "DRY RUN: Throw command not sent"
                elif throw_running.is_set():
                    with state_lock:
                        last_error = "T ignored: throw mode is already running"
                elif not gripper_closed_on_target:
                    with state_lock:
                        last_error = "T ignored: grasp an object first with G"
                else:
                    throw_abort.clear()
                    throw_running.set()
                    throw_thread = threading.Thread(
                        target=run_throw_worker,
                        daemon=False,
                    )
                    throw_thread.start()
                    with state_lock:
                        last_error = None
                    print("[THROW] mode started")
                continue


            if key == ord("r"):
                if throw_running.is_set():
                    with state_lock:
                        last_error = "R ignored: throw mode is running"
                elif not config.RANDOM_MOTION_ENABLED:
                    with state_lock:
                        last_error = "R ignored: random motion is disabled"
                elif config.DRY_RUN:
                    command = make_safe_random_flange_coords()
                    with state_lock:
                        last_error = f"DRY RUN: random command not sent {command}"
                    print("[DRY RUN] Random safe flange command:", command)
                else:
                    command = make_safe_random_flange_coords()
                    pseudo_payload = {"status": "ok", "plan": {"flange_command": command}}
                    is_safe, reason, safe_command = validate_server_plan(pseudo_payload)
                    if not is_safe or safe_command is None:
                        with state_lock:
                            last_error = f"Random pose rejected locally: {reason}"
                        print("[SAFETY]", last_error)
                    else:
                        print("[RANDOM] safe flange command:", safe_command)
                        reached = robot.send_flange_coords_and_wait(safe_command)
                        with state_lock:
                            last_error = None if reached else "Random pose timeout"
                continue

            if key == ord("w"):
                if throw_running.is_set():
                    print("[HOME] aborting throw before home return")
                    throw_abort.set()
                    if not config.DRY_RUN:
                        try:
                            robot.stop_motion()
                        except Exception as exc:
                            print("[HOME] stop error:", exc)
                    if throw_thread is not None and throw_thread.is_alive():
                        throw_thread.join(timeout=1.0)
                    throw_running.clear()

                if config.DRY_RUN:
                    with state_lock:
                        last_error = "DRY RUN: Home command not sent"
                else:
                    reached = robot.move_home_keep_gripper_closed()
                    gripper_closed_on_target = False
                    with state_lock:
                        last_error = None if reached else "HOME return timeout"
                continue

            if key != ord("g"):
                continue

            if throw_running.is_set():
                with state_lock:
                    last_error = "G ignored: throw mode is running"
                continue

            with state_lock:
                last_error = None
            try:
                # The robot is stationary here. Read its current Base-frame pose
                # and immediately capture a fresh calibration-size image.
                current_flange_coords = robot.get_flange_coords()
                plan_frame, _ = get_latest_frame(min_seq=frame_seq, timeout_sec=1.0)
                if plan_frame is None:
                    plan_frame, _ = get_latest_frame(timeout_sec=0.5)
                if plan_frame is None:
                    raise RuntimeError("Cannot capture a current camera frame for grasp planning")
                payload = request_grasp_plan(plan_frame, current_flange_coords)

                print(
                    "[LAPTOP RESPONSE]\n",
                    json.dumps(payload, ensure_ascii=False, indent=2),
                )
                with state_lock:
                    last_payload = payload
                    last_payload_expires_at = time.monotonic() + 3.0

                is_safe, reason, command = validate_server_plan(payload)
                if not is_safe or command is None:
                    with state_lock:
                        last_error = f"Plan rejected locally: {reason}"
                    print("[SAFETY]", last_error)
                    continue

                if config.DRY_RUN:
                    print(
                        "[DRY RUN] Laptop plan validated; no robot command sent:",
                        command,
                    )
                    continue

                reached = robot.send_flange_coords_and_wait(command)
                if reached:
                    try:
                        robot.close_gripper()
                    except RuntimeError as exc:
                        gripper_closed_on_target = False
                        with state_lock:
                            last_error = f"GRIPPER ERROR: {exc}"
                        print(last_error)
                        continue
                    gripper_closed_on_target = True
                    with state_lock:
                        last_error = None
                else:
                    gripper_closed_on_target = False
                    with state_lock:
                        last_error = (
                            "Target pose timeout; gripper remains unchanged"
                        )

            except (GraspServerError, RuntimeError, ValueError) as exc:
                with state_lock:
                    last_error = f"ERROR: {type(exc).__name__}: {exc}"
                print(last_error)

    finally:
        stream_stop.set()
        capture_stop.set()
        remote_stop.set()
        with frame_condition:
            frame_condition.notify_all()
        if remote_thread is not None and remote_thread.is_alive():
            remote_thread.join(timeout=1.0)
        if stream_thread is not None and stream_thread.is_alive():
            stream_thread.join(timeout=1.0)
        if capture_thread.is_alive():
            capture_thread.join(timeout=1.0)

        if throw_running.is_set():
            throw_abort.set()
            if not config.DRY_RUN:
                try:
                    robot.stop_motion()
                except Exception:
                    pass

        if throw_thread is not None and throw_thread.is_alive():
            throw_thread.join(timeout=1.0)

        with cap_lock:
            cap.release()
        if show_window:
            cv2.destroyAllWindows()
        print("Client terminated")


if __name__ == "__main__":
    main()

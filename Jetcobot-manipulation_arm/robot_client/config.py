"""Raspberry Pi-side configuration for the laptop-local grasp service.

Only ``config/client_config.ini`` is intended to be edited by the operator.
The YOLO weight, camera intrinsic file, and Hand-Eye result stay on the laptop.
"""
from __future__ import annotations

import configparser
from pathlib import Path


RASPBERRY_PI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = RASPBERRY_PI_ROOT / "config" / "client_config.ini"


def _numbers(value: str, expected_count: int, name: str) -> list[float]:
    try:
        values = [float(item.strip()) for item in value.split(",")]
    except ValueError as exc:
        raise ValueError(
            f"{name} must be {expected_count} comma-separated numbers"
        ) from exc
    if len(values) != expected_count:
        raise ValueError(f"{name} must contain exactly {expected_count} values")
    return values


def _load() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    if not DEFAULT_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Raspberry Pi config not found: {DEFAULT_CONFIG_PATH}"
        )
    parser.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
    return parser


_parser = _load()

# Laptop network
GRASP_SERVER_URL = _parser.get("network", "grasp_server_url").strip()
EXPECTED_SERVER_RUNTIME = _parser.get(
    "network", "expected_server_runtime", fallback="laptop-local"
).strip()
ALLOW_LOOPBACK_SERVER = _parser.getboolean(
    "network", "allow_loopback_server", fallback=False
)
REQUEST_TIMEOUT_SEC = _parser.getfloat("network", "request_timeout_sec")
CONNECT_TIMEOUT_SEC = _parser.getfloat(
    "network", "connect_timeout_sec", fallback=3.0
)
HEALTH_TIMEOUT_SEC = _parser.getfloat(
    "network", "health_timeout_sec", fallback=5.0
)
CHECK_SERVER_ON_STARTUP = _parser.getboolean(
    "network", "check_server_on_startup", fallback=True
)
JPEG_QUALITY = _parser.getint("network", "jpeg_quality")
if not 1 <= JPEG_QUALITY <= 100:
    raise ValueError("jpeg_quality must be in the range 1..100")

# Optional camera preview stream to the laptop server web page.
if _parser.has_section("camera_stream"):
    CAMERA_STREAM_ENABLED = _parser.getboolean("camera_stream", "enabled", fallback=True)
    CAMERA_STREAM_FPS = _parser.getfloat("camera_stream", "fps", fallback=2.0)
    CAMERA_STREAM_TIMEOUT_SEC = _parser.getfloat("camera_stream", "timeout_sec", fallback=0.4)
    CAMERA_STREAM_JPEG_QUALITY = _parser.getint("camera_stream", "jpeg_quality", fallback=70)
else:
    CAMERA_STREAM_ENABLED = True
    CAMERA_STREAM_FPS = 2.0
    CAMERA_STREAM_TIMEOUT_SEC = 0.4
    CAMERA_STREAM_JPEG_QUALITY = 70
if CAMERA_STREAM_FPS <= 0:
    raise ValueError("camera_stream fps must be positive")
if CAMERA_STREAM_TIMEOUT_SEC <= 0:
    raise ValueError("camera_stream timeout_sec must be positive")
if not 1 <= CAMERA_STREAM_JPEG_QUALITY <= 100:
    raise ValueError("camera_stream jpeg_quality must be in the range 1..100")
CAMERA_STREAM_INTERVAL_SEC = 1.0 / CAMERA_STREAM_FPS

if _parser.has_section("remote_control"):
    REMOTE_COMMAND_ENABLED = _parser.getboolean("remote_control", "enabled", fallback=True)
    REMOTE_COMMAND_TIMEOUT_SEC = _parser.getfloat("remote_control", "timeout_sec", fallback=0.4)
else:
    REMOTE_COMMAND_ENABLED = True
    REMOTE_COMMAND_TIMEOUT_SEC = 0.4
if REMOTE_COMMAND_TIMEOUT_SEC <= 0:
    raise ValueError("remote_control timeout_sec must be positive")

# Camera: must match the laptop calibration image size.
CAMERA_ID_RAW = _parser.get("camera", "camera_id").strip()
CAMERA_ID = int(CAMERA_ID_RAW) if CAMERA_ID_RAW.isdecimal() else CAMERA_ID_RAW
CAMERA_FRAME_WIDTH = _parser.getint("camera", "frame_width")
CAMERA_FRAME_HEIGHT = _parser.getint("camera", "frame_height")
CAMERA_FLUSH_FRAMES = _parser.getint(
    "camera", "flush_frames_before_capture", fallback=0
)
if CAMERA_FRAME_WIDTH <= 0 or CAMERA_FRAME_HEIGHT <= 0:
    raise ValueError("camera frame_width and frame_height must be positive")
if CAMERA_FLUSH_FRAMES < 0:
    raise ValueError("flush_frames_before_capture must be >= 0")

# MyCobot motion
PORT = _parser.get("robot", "mycobot_port").strip()
BAUD = _parser.getint("robot", "mycobot_baud")
MOVE_SPEED = _parser.getint("robot", "move_speed")
MOVE_MODE = _parser.getint("robot", "move_mode")
POSE_POSITION_TOL_MM = _parser.getfloat("robot", "pose_position_tol_mm")
POSE_ANGLE_TOL_DEG = _parser.getfloat("robot", "pose_angle_tol_deg")
MOVE_TIMEOUT_SEC = _parser.getfloat("robot", "move_timeout_sec")
MOVE_POLL_SEC = _parser.getfloat("robot", "move_poll_sec")
HOME_FLANGE_COORDS = _numbers(
    _parser.get("robot", "home_flange_coords"), 6, "home_flange_coords"
)

# Gripper
GRIPPER_OPEN_VALUE = _parser.getint("gripper", "open_value")
GRIPPER_CLOSE_VALUE = _parser.getint("gripper", "close_value")
GRIPPER_SPEED = _parser.getint("gripper", "speed")
GRIPPER_SETTLE_SEC = _parser.getfloat("gripper", "settle_sec")

# Final safety gate: evaluated locally on the Raspberry Pi.
DRY_RUN = _parser.getboolean("safety", "dry_run")
SAFE_X_MM = (
    _parser.getfloat("safety", "safe_x_min_mm"),
    _parser.getfloat("safety", "safe_x_max_mm"),
)
SAFE_Y_MM = (
    _parser.getfloat("safety", "safe_y_min_mm"),
    _parser.getfloat("safety", "safe_y_max_mm"),
)
SAFE_Z_MM = (
    _parser.getfloat("safety", "safe_z_min_mm"),
    _parser.getfloat("safety", "safe_z_max_mm"),
)
SAFE_EULER_ABS_DEG = _parser.getfloat("safety", "safe_euler_abs_deg")

# Conservative random motion around the configured home flange pose.
if _parser.has_section("random_motion"):
    RANDOM_MOTION_ENABLED = _parser.getboolean("random_motion", "enabled", fallback=True)
    RANDOM_X_RADIUS_MM = _parser.getfloat("random_motion", "x_radius_mm", fallback=35.0)
    RANDOM_Y_RADIUS_MM = _parser.getfloat("random_motion", "y_radius_mm", fallback=35.0)
    RANDOM_Z_RADIUS_MM = _parser.getfloat("random_motion", "z_radius_mm", fallback=20.0)
else:
    RANDOM_MOTION_ENABLED = True
    RANDOM_X_RADIUS_MM = 35.0
    RANDOM_Y_RADIUS_MM = 35.0
    RANDOM_Z_RADIUS_MM = 20.0
if min(RANDOM_X_RADIUS_MM, RANDOM_Y_RADIUS_MM, RANDOM_Z_RADIUS_MM) < 0:
    raise ValueError("random_motion radii must be >= 0")

# Manual configured motion. M command moves to this Flange pose after local safety validation.
if _parser.has_section("manual_motion"):
    MANUAL_MOTION_ENABLED = _parser.getboolean("manual_motion", "enabled", fallback=True)
    MANUAL_FLANGE_COORDS = _numbers(
        _parser.get("manual_motion", "target_flange_coords"),
        6,
        "manual_motion.target_flange_coords",
    )
else:
    MANUAL_MOTION_ENABLED = False
    MANUAL_FLANGE_COORDS = HOME_FLANGE_COORDS

# UI
SHOW_WINDOW = _parser.getboolean("ui", "show_window")
WINDOW_NAME = _parser.get("ui", "window_name")

# -----------------------------
# Throw mode (unchanged robot-side behavior)
# -----------------------------
THROW_START_ANGLES = [43.68, 66.62, 3.79, -38.47, 7.16, 47.90]
THROW_END_ANGLES = [47.94, -29.44, -31.03, 40.19, 4.48, 47.90]
THROW_PREP_SPEED = 30
THROW_SPEED = 100
THROW_GRIPPER_OPEN_DELAY_SEC = 0.3
THROW_ANGLE_TOLERANCE_DEG = 2.0
THROW_PREP_TIMEOUT_SEC = 15.0
THROW_END_TIMEOUT_SEC = 10.0
THROW_FINAL_TIMEOUT_SEC = 20.0
THROW_FINAL_FLANGE_COORDS = [147.4, 52.6, 241.7, -177.68, 5.26, -94.11]
THROW_FINAL_MOVE_SPEED = 50
THROW_FINAL_MOVE_MODE = 0

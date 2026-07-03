"""실제 카메라/로봇 없이 수행하는 geometry 스모크 테스트."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from app.geometry import Calibration, compute_grasp_plan
from app.settings import Settings


def make_settings() -> Settings:
    return Settings(
        model_path=Path("dummy.pt"),
        device="cpu",
        default_conf=0.6,
        default_imgsz=640,
        default_target_label="toothbrush",
        save_results=False,
        save_root_dir=Path("logs"),
        intrinsic_file=Path("dummy.npz"),
        handeye_result_json=Path("dummy.json"),
        euler_order="zyx",
        object_plane_z_base_mm=0.0,
        toothbrush_target_z_offset_mm=0.0,
        tcp_offset_flange_to_tcp_mm=(0.0, 0.0, 0.0),
        max_upload_bytes=1024,
        expected_image_width=0,
        expected_image_height=0,
    )


def test_plan_returns_a_six_axis_command() -> None:
    # camera origin in base at z=100 and optical z points down: center ray intersects z=0.
    T_flange_camera = np.eye(4, dtype=np.float64)
    T_flange_camera[2, 3] = 100.0
    T_flange_camera[:3, :3] = np.diag([1.0, -1.0, -1.0])
    calibration = Calibration(
        K=np.array([[100.0, 0.0, 50.0], [0.0, 100.0, 50.0], [0.0, 0.0, 1.0]]),
        dist=np.zeros((5, 1)),
        T_flange_camera=T_flange_camera,
        selected_method="test",
        euler_order="zyx",
    )
    result = compute_grasp_plan(
        detection={"label": "toothbrush", "class_id": 0, "confidence": 0.9, "bbox": [40, 40, 60, 60]},
        current_flange_coords=[0, 0, 0, 0, 0, 0],
        calibration=calibration,
        settings=make_settings(),
    )
    assert len(result["plan"]["flange_command"]) == 6
    assert abs(result["plan"]["tcp_target_base_mm"][2]) < 1e-6

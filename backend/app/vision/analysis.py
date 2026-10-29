"""Incremental local video analysis with anonymous OpenCV and optional MediaPipe landmarks."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

import cv2

from app.core.config import settings

_landmarker: Any = None
_landmarker_path: str | None = None
_landmarker_error: str | None = None
_landmarker_lock = Lock()


def vision_status() -> dict[str, str | bool]:
    if not _opencv_available():
        return {"opencv": False, "landmarks": False, "landmark_status": "OpenCV capabilities unavailable"}
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        return {"opencv": True, "landmarks": False, "landmark_status": "MediaPipe is not installed; anonymous OpenCV counting remains available"}
    if not settings.mediapipe_model_path or not Path(settings.mediapipe_model_path).is_file():
        return {"opencv": True, "landmarks": False, "landmark_status": "Configure MEDIAPIPE_MODEL_PATH with a local Face Landmarker .task model"}
    try:
        _get_landmarker()
    except RuntimeError as exc:
        return {"opencv": True, "landmarks": False, "landmark_status": str(exc)}
    return {"opencv": True, "landmarks": True, "landmark_status": "available"}


def _opencv_available() -> bool:
    try:
        return hasattr(cv2, "VideoCapture") and hasattr(cv2, "CascadeClassifier") and bool(cv2.data.haarcascades)
    except Exception:
        return False


def _get_landmarker() -> Any:
    global _landmarker, _landmarker_path, _landmarker_error
    path = settings.mediapipe_model_path
    with _landmarker_lock:
        if _landmarker is not None and _landmarker_path == path:
            return _landmarker
        if not path or not Path(path).is_file():
            raise RuntimeError("MediaPipe model asset is missing; set MEDIAPIPE_MODEL_PATH")
        try:
            import mediapipe as mp
            options = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=path),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                num_faces=8,
            )
            _landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
            _landmarker_path = path
            _landmarker_error = None
            return _landmarker
        except Exception as exc:
            _landmarker_error = f"MediaPipe could not initialize the configured model: {exc}"
            raise RuntimeError(_landmarker_error) from exc


def analyze_frame(frame: Any) -> dict[str, Any]:
    """Count faces and optionally return aggregate approximate pose proxies for a BGR frame."""
    if frame is None or getattr(frame, "size", 0) == 0:
        raise ValueError("Frame is empty or unreadable")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    detector = cv2.CascadeClassifier(cascade_path)
    if detector.empty():
        raise RuntimeError("OpenCV Haar face detector is unavailable")
    boxes = detector.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(30, 30))
    result: dict[str, Any] = {"face_detections": len(boxes), "landmark_faces": 0, "avg_yaw_proxy": None, "avg_pitch_proxy": None, "landmark_status": "unavailable"}
    if len(boxes) == 0:
        return result
    try:
        import mediapipe as mp
        landmarker = _get_landmarker()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        landmarks_result = landmarker.detect(image)
        measures = []
        for face in landmarks_result.face_landmarks:
            if len(face) <= 263:
                continue
            left, nose, right = face[33], face[1], face[263]
            eye_distance = max(abs(right.x - left.x), 1e-6)
            eye_mid_x = (left.x + right.x) / 2
            eye_mid_y = (left.y + right.y) / 2
            measures.append(((nose.x - eye_mid_x) / eye_distance, (nose.y - eye_mid_y) / eye_distance))
        if measures:
            result.update({"landmark_faces": len(measures), "avg_yaw_proxy": sum(x for x, _ in measures) / len(measures), "avg_pitch_proxy": sum(y for _, y in measures) / len(measures), "landmark_status": "measured-approximate"})
    except ImportError:
        result["landmark_status"] = "MediaPipe is not installed"
    except RuntimeError as exc:
        result["landmark_status"] = str(exc)
    except Exception as exc:
        result["landmark_status"] = f"Landmark processing unavailable: {type(exc).__name__}"
    return result


def analyze_image(content: bytes) -> dict[str, Any]:
    import numpy as np
    frame = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Image frame is invalid or unreadable")
    return analyze_frame(frame)


def analyze_video_path(path: str, max_frames: int | None = None, max_duration_seconds: int | None = None) -> dict[str, Any]:
    if not _opencv_available():
        raise RuntimeError("OpenCV video capabilities are unavailable")
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        capture.release()
        raise ValueError("OpenCV could not open this video. Check the file and codec; try MP4/H.264 or AVI/MJPG.")
    sampled = frame_index = detections = landmark_faces = 0
    yaw_values: list[float] = []
    pitch_values: list[float] = []
    fps = frame_count = duration = 0.0
    max_frames = max_frames or settings.max_video_frames
    max_duration_seconds = max_duration_seconds or settings.max_video_duration_seconds
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps > 0 and frame_count > 0:
            duration = frame_count / fps
            if duration > max_duration_seconds:
                raise ValueError(f"Video is {duration:.0f}s; the configured limit is {max_duration_seconds}s")
            if frame_count > max_frames:
                raise ValueError(f"Video has {frame_count:.0f} frames; the configured limit is {max_frames}")
        if fps <= 0:
            fps = 15
        stride = max(1, round(fps))
        while frame_index < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % stride == 0:
                result = analyze_frame(frame)
                detections += result["face_detections"]
                landmark_faces += result["landmark_faces"]
                if result["avg_yaw_proxy"] is not None:
                    yaw_values.append(result["avg_yaw_proxy"])
                    pitch_values.append(result["avg_pitch_proxy"])
                sampled += 1
            frame_index += 1
        if sampled == 0:
            raise ValueError("No readable video frames were found. The file may be corrupt or use an unsupported codec.")
        actual_duration = duration if duration > 0 else frame_index / fps
        return {"sampled_frames": sampled, "face_detections": detections, "landmark_faces": landmark_faces, "avg_yaw_proxy": sum(yaw_values) / len(yaw_values) if yaw_values else None, "avg_pitch_proxy": sum(pitch_values) / len(pitch_values) if pitch_values else None, "duration_seconds": round(actual_duration, 2), "frame_count": int(frame_count) if frame_count > 0 else frame_index, "data_quality": "measured-anonymous"}
    finally:
        capture.release()


def analyze_video(content: bytes, max_frames: int = 600) -> dict[str, Any]:
    """Test/helper entry point. API uploads use disk-backed incremental processing."""
    handle = tempfile.NamedTemporaryFile(suffix=".video", delete=False)
    path = handle.name
    try:
        handle.write(content)
        handle.close()
        return analyze_video_path(path, max_frames=max_frames)
    finally:
        handle.close()
        try:
            os.unlink(path)
        except OSError:
            pass

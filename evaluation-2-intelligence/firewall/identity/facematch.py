from __future__ import annotations

import importlib.util
import io
import os
import threading
import warnings
from pathlib import Path
from typing import Any, Protocol

MODEL_NAME = "sface-2021dec"
THRESHOLDS = {"cosine": 0.363}
STATES = frozenset({"match", "no_match", "no_face_in_id", "no_face_live", "several_faces", "unreadable", "not_available"})
DETECTOR_FILE = "yunet.onnx"
RECOGNIZER_FILE = "sface.onnx"
MAX_PIXELS = 12_000_000
MAX_SIDE = 5000
WORK_SIDE = 960
SCORE_FLOOR = 0.7
NMS = 0.3
TOP_K = 20
SIMILAR_SIZE = 0.5
FORMATS = frozenset({"JPEG", "PNG"})


class FaceMatcher(Protocol):
    def available(self) -> bool: ...

    def compare(self, id_image_bytes: bytes, live_images: list[bytes]) -> dict[str, Any]: ...


def outcome(state: str, similarity: float | None = None, frames: int = 0) -> dict[str, Any]:
    return {
        "state": state,
        "similarity": None if similarity is None else round(float(similarity), 3),
        "threshold": THRESHOLDS["cosine"],
        "model": MODEL_NAME,
        "frames_checked": frames,
    }


def model_dir() -> Path:
    configured = os.environ.get("FIREWALL_FACE_MODEL_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / ".state" / "face"


def load_cv2() -> Any:
    try:
        import cv2

        return cv2
    except Exception:
        return None


def readable_header(data: Any) -> bool:
    if not isinstance(data, (bytes, bytearray)) or not data:
        return False
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with Image.open(io.BytesIO(bytes(data))) as picture:
                width, height = picture.size
                kind = picture.format
    except Exception:
        return False
    if kind not in FORMATS or width < 1 or height < 1:
        return False
    return width * height <= MAX_PIXELS and max(width, height) <= MAX_SIDE


class OpenCvFaceMatcher:
    def __init__(self, directory: str | os.PathLike[str] | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None
        self._lock = threading.Lock()
        self._models: tuple[Any, Any] | None = None

    def _folder(self) -> Path:
        return self._directory if self._directory is not None else model_dir()

    def available(self) -> bool:
        folder = self._folder()
        if not (folder / DETECTOR_FILE).is_file() or not (folder / RECOGNIZER_FILE).is_file():
            return False
        if importlib.util.find_spec("PIL") is None:
            return False
        return load_cv2() is not None

    def _load(self, cv2: Any) -> tuple[Any, Any]:
        if self._models is None:
            folder = self._folder()
            detector = cv2.FaceDetectorYN.create(str(folder / DETECTOR_FILE), "", (320, 320), SCORE_FLOOR, NMS, TOP_K)
            recognizer = cv2.FaceRecognizerSF.create(str(folder / RECOGNIZER_FILE), "")
            self._models = (detector, recognizer)
        return self._models

    @staticmethod
    def _decode(cv2: Any, data: bytes) -> Any:
        import numpy

        picture = cv2.imdecode(numpy.frombuffer(data, dtype=numpy.uint8), cv2.IMREAD_COLOR)
        if picture is None:
            return None
        height, width = picture.shape[:2]
        longest = max(height, width)
        if longest > WORK_SIDE:
            scale = WORK_SIDE / longest
            picture = cv2.resize(picture, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
        return picture

    @staticmethod
    def _faces(detector: Any, picture: Any) -> list[Any]:
        height, width = picture.shape[:2]
        detector.setInputSize((width, height))
        _, found = detector.detect(picture)
        if found is None:
            return []
        return sorted((row for row in found), key=lambda row: float(row[2]) * float(row[3]), reverse=True)

    @staticmethod
    def _crowded(faces: list[Any]) -> bool:
        if len(faces) < 2:
            return False
        first = float(faces[0][2]) * float(faces[0][3])
        second = float(faces[1][2]) * float(faces[1][3])
        return first > 0 and second / first >= SIMILAR_SIZE

    def compare(self, id_image_bytes: bytes, live_images: list[bytes]) -> dict[str, Any]:
        try:
            return self._compare(id_image_bytes, live_images)
        except Exception:
            return outcome("not_available")

    def _compare(self, id_image_bytes: bytes, live_images: list[bytes]) -> dict[str, Any]:
        images = [id_image_bytes, *list(live_images)]
        if len(images) < 2 or not all(readable_header(item) for item in images):
            return outcome("unreadable")
        if not self.available():
            return outcome("not_available")
        cv2 = load_cv2()
        with self._lock:
            detector, recognizer = self._load(cv2)
            id_picture = self._decode(cv2, id_image_bytes)
            if id_picture is None:
                return outcome("unreadable")
            id_faces = self._faces(detector, id_picture)
            if not id_faces:
                return outcome("no_face_in_id")
            id_feature = recognizer.feature(recognizer.alignCrop(id_picture, id_faces[0]))
            del id_picture
            best: float | None = None
            seen_none = seen_many = seen_bad = 0
            checked = 0
            for raw in live_images:
                picture = self._decode(cv2, raw)
                if picture is None:
                    seen_bad += 1
                    continue
                faces = self._faces(detector, picture)
                if not faces:
                    seen_none += 1
                elif self._crowded(faces):
                    seen_many += 1
                else:
                    feature = recognizer.feature(recognizer.alignCrop(picture, faces[0]))
                    score = float(recognizer.match(id_feature, feature, cv2.FaceRecognizerSF_FR_COSINE))
                    best = score if best is None else max(best, score)
                    checked += 1
                del picture
            del id_feature
        if best is not None:
            return outcome("match" if best >= THRESHOLDS["cosine"] else "no_match", best, checked)
        if seen_many:
            return outcome("several_faces")
        if seen_none:
            return outcome("no_face_live")
        return outcome("unreadable")

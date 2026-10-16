"""Where the face is in a player's photo, so their profile shows it rather than the top of an action shot (photos.py).

The photos job looks once at each thumbnail it keeps, in the worker, with OpenCV's YuNet detector: a
230 KB model (data/face_detection_yunet_2026may.onnx, MIT, from opencv_zoo), about 10 ms a photo. Only the
worker loads OpenCV; the API reads the box the job wrote (tests/test_faces.py checks).

Commons' structured data can say where a person is in a file ("depicts" with "relative position within
image", P2677), but none of the players' files we checked had it, so the detector looks at every one.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

MODEL = Path(__file__).parent / "data" / "face_detection_yunet_2026may.onnx"
SURE = 0.8  # the detector's score under which a "face" was as often a trophy or a far-off fan
OVERLAP = 0.3  # boxes overlapping more than this are one face
MOST = 50  # faces looked at in a photo (a crowd behind the player)


class Box(NamedTuple):
    """A face: its left, top, width and height, as fractions of the photo's width and height."""

    x: float
    y: float
    w: float
    h: float


@cache
def _detector() -> Any:
    import cv2

    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # OpenCV 5 warns of its new engine at every start
    return cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320), SURE, OVERLAP, MOST)


def find(data: bytes) -> tuple[int, int, Box | None]:
    """A photo's width and height in pixels, and the largest face in it the detector is sure of (the player
    is the photo's subject, nearest the camera); (0, 0, None) when the bytes aren't a picture."""
    import cv2
    import numpy as np

    try:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    except cv2.error:  # bytes OpenCV can't even start reading
        image = None
    if image is None:
        return 0, 0, None
    height, width = image.shape[:2]
    detector = _detector()
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    if faces is None or not len(faces):
        return width, height, None
    x, y, w, h = (float(v) for v in max(faces, key=lambda f: f[2] * f[3])[:4])
    # A face at the edge has a box reaching past it.
    left, top, right, bottom = max(x, 0), max(y, 0), min(x + w, width), min(y + h, height)
    return width, height, Box(round(left / width, 4), round(top / height, 4), round((right - left) / width, 4), round((bottom - top) / height, 4))

"""Where the face is in a player's photo (faces.py) and how the app is told to frame the photo around it
(photos.framing). Two public-domain photos from Wikimedia Commons stand in for players' (tests/fixtures/photos),
as 330-pixel thumbnails (the job keeps photos.WIDTH-pixel ones; the detector finds the same faces in either):
- babe-ruth.jpg, https://commons.wikimedia.org/wiki/File:Babe_Ruth2.jpg: full length, his face small near
  the top as in an action shot. 1920, from the Library of Congress; public domain (PD-US-expired).
- michael-jordan.jpg, https://commons.wikimedia.org/wiki/File:Michael_Jordan_in_2014.jpg: a close-up.
  A US Department of Defense photo by D. Myles Cullen, 2014; public domain (a US government work).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from sports_follow import faces, photos
from sports_follow.models import Photo

FIXTURES = Path(__file__).parent / "fixtures" / "photos"
RUTH = (FIXTURES / "babe-ruth.jpg").read_bytes()
JORDAN = (FIXTURES / "michael-jordan.jpg").read_bytes()


def _centre(box) -> tuple[float, float]:
    return box.x + box.w / 2, box.y + box.h / 2


def _jpeg(image) -> bytes:
    ok, data = cv2.imencode(".jpg", image)
    assert ok
    return data.tobytes()


# ---------------------------------------------------------------- finding it


def test_a_far_off_face_is_found_near_the_top_of_a_full_length_photo():
    width, height, box = faces.find(RUTH)
    assert (width, height) == (330, 435)
    assert box and box.w < 0.15 and box.h < 0.15
    x, y = _centre(box)
    assert 0.4 < x < 0.6 and 0.08 < y < 0.2


def test_a_close_up_face_fills_most_of_the_photos_width():
    width, height, box = faces.find(JORDAN)
    assert (width, height) == (330, 471)
    assert box and box.w > 0.4
    x, y = _centre(box)
    assert 0.3 < x < 0.6 and 0.2 < y < 0.5


def test_of_several_faces_the_largest_is_the_players():
    # The close-up on the right, and a copy at half the size on the left: a far-off face beside the player.
    near = cv2.imdecode(np.frombuffer(JORDAN, np.uint8), cv2.IMREAD_COLOR)
    far = cv2.resize(near, (165, 236))
    canvas = np.full((471, 495, 3), 200, np.uint8)
    canvas[:, 165:] = near
    canvas[:236, :165] = far
    width, _, box = faces.find(_jpeg(canvas))
    assert width == 495 and box and _centre(box)[0] > 0.5


def test_a_face_cut_by_the_edge_keeps_its_box_inside_the_photo():
    # The close-up's left 40% cut off: the face now starts at the edge.
    near = cv2.imdecode(np.frombuffer(JORDAN, np.uint8), cv2.IMREAD_COLOR)
    _, _, box = faces.find(_jpeg(near[:, 132:]))
    assert box and box.x >= 0 and box.y >= 0 and box.x + box.w <= 1 and box.y + box.h <= 1


def test_a_picture_without_a_face_has_its_size_and_no_face():
    assert faces.find(_jpeg(np.full((200, 300, 3), 128, np.uint8))) == (300, 200, None)


def test_bytes_that_arent_a_picture_have_no_size_and_no_face():
    assert faces.find(b"<html>not found</html>") == (0, 0, None)
    assert faces.find(b"") == (0, 0, None)  # which OpenCV raises on


def test_the_api_never_loads_opencv():
    """Only the worker looks for faces: the API, which imports photos.py, never loads OpenCV or NumPy."""
    code = "import sys, sports_follow.server; print('cv2' in sys.modules, 'numpy' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "REDIS_URL": "redis://127.0.0.1:1/0"}, cwd=Path(__file__).parent.parent)
    assert out.stdout.split() == ["False", "False"], out.stderr


# ---------------------------------------------------------------- framing the photo around it


def _inside(face: dict, crop: dict) -> bool:
    return crop["x"] <= face["x"] and crop["y"] <= face["y"] and face["x"] + face["w"] <= crop["x"] + crop["w"] + 1e-4 and face["y"] + face["h"] <= crop["y"] + crop["h"] + 1e-4


def test_a_face_in_an_action_shot_gets_a_square_around_it_head_and_shoulders():
    face = {"x": 0.44, "y": 0.25, "w": 0.15, "h": 0.15}  # 144 by 192 pixels, its centre at (494.4, 416)
    crop = photos.framing(960, 1280, face)["crop"]
    side = 192 / photos.FILL
    assert abs(crop["w"] * 960 - side) < 0.1 and abs(crop["h"] * 1280 - side) < 0.1  # square, the face FILL of it
    assert abs((crop["x"] + crop["w"] / 2) * 960 - 494.4) < 0.1  # centred across it
    assert abs((crop["y"] + crop["h"] / 2) * 1280 - (416 - photos.RAISE * 192)) < 0.1  # a little above, for the hair
    assert _inside(face, crop)


def test_a_close_up_shows_the_widest_square_the_photo_has():
    framed = photos.framing(960, 1370, {"x": 0.18, "y": 0.3, "w": 0.54, "h": 0.52})
    assert framed["crop"]["x"] == 0 and framed["crop"]["w"] == 1  # the full width, never past it
    assert 0 < framed["crop"]["y"] < 0.3  # some of the top cut, the hair above the face kept


def test_a_tiny_face_is_not_blown_up_past_the_closest_crop():
    framed = photos.framing(960, 1440, {"x": 0.5, "y": 0.3, "w": 0.02, "h": 0.02})
    assert round(framed["crop"]["w"] * 960) == photos.CLOSEST


def test_a_face_at_a_corner_keeps_the_square_inside_the_photo():
    face = {"x": 0.9, "y": 0.0, "w": 0.1, "h": 0.07}
    crop = photos.framing(960, 1440, face)["crop"]
    assert crop["x"] + crop["w"] == 1 and crop["y"] == 0
    assert _inside(face, crop)


def test_without_a_face_the_app_keeps_its_guess():
    assert photos.framing(330, 495, None) == {} and photos.framing(0, 0, None) == {} and photos.framing(None, None, None) == {}


def test_the_app_gets_the_framing_with_the_photo():
    photo = Photo(file="Babe Ruth2.jpg", status="ok", licence="public domain", digest="0" * 32, media_type="image/jpeg")
    assert "crop" not in photos.public(photo)  # not looked at yet
    width, height, box = faces.find(RUTH)
    assert box
    photo.width, photo.height, photo.face = width, height, box._asdict()
    shown = photos.public(photo)
    assert shown["crop"] == photos.framing(width, height, box._asdict())["crop"] and _inside(box._asdict(), shown["crop"])

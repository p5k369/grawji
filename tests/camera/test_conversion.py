"""Tests for collecting a conversion result from the camera."""

import struct

import pytest

from grawji.camera.conversion import (
    ConversionError,
    clear_leftovers,
    object_handles,
    wait_for_result,
)
from tests.ptp_support import (
    DELETE_OBJECT,
    GET_OBJECT,
    JPEG_FORMAT,
    OK,
    UPLOAD_FORMAT,
    PtpCamera,
)


class FakeTime:
    """A clock that only moves when slept on."""

    def __init__(self):
        """Start at zero."""
        self.now = 0.0
        self.sleeps = 0

    def clock(self):
        """The current fake time."""
        return self.now

    def sleep(self, seconds):
        """Advance the fake time."""
        self.now += seconds
        self.sleeps += 1


def wait(camera, timeout=30, **kwargs):
    """wait_for_result on fake time."""
    t = FakeTime()
    return wait_for_result(
        camera, timeout, clock=t.clock, sleep=t.sleep, **kwargs
    )


def test_listed_upload_is_never_taken_for_the_result():
    """The uploaded RAF listed first is skipped, not downloaded."""
    camera = PtpCamera({7: [UPLOAD_FORMAT, b"RAF"], 9: [JPEG_FORMAT, b"JPEG"]})
    assert wait(camera) == b"JPEG"
    assert (GET_OBJECT, 7) not in camera.ops
    assert 7 in camera.objects  # the session still needs its RAF
    assert 9 not in camera.objects  # the result is deleted


def test_waits_for_the_result_to_appear():
    """Polling continues until the camera lists the result."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    camera.hidden[3] = 4
    assert wait(camera) == b"JPEG"


def test_result_whose_info_is_refused_is_not_ready_yet():
    """An object the camera will not describe yet is polled again."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    camera.info_refusals[3] = 2
    assert wait(camera) == b"JPEG"


def test_refused_download_is_retried():
    """GetObject refused with 0x200F is retried, as X RAW Studio does."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    camera.get_refusals[3] = 3
    assert wait(camera) == b"JPEG"
    assert camera.count(GET_OBJECT) == 4


def test_failed_download_still_frees_the_camera():
    """A download that fails for good raises and deletes the object."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    camera.get_code[3] = 0x2002
    with pytest.raises(ConversionError, match="GetObject failed: 0x2002"):
        wait(camera)
    assert camera.objects == {}


def test_access_denied_until_the_deadline_gives_up():
    """Refusals stop being retried once the timeout has passed."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    camera.get_refusals[3] = 10_000
    with pytest.raises(ConversionError, match="0x200F"):
        wait(camera, timeout=1)
    assert camera.objects == {}


def test_objects_listed_before_the_conversion_are_ignored():
    """A handle in ignore is never downloaded."""
    camera = PtpCamera({5: [JPEG_FORMAT, b"STALE"], 6: [JPEG_FORMAT, b"JPEG"]})
    assert wait(camera, ignore={5}) == b"JPEG"
    assert 5 in camera.objects


def test_no_result_times_out():
    """Nothing listed within the timeout raises TimeoutError."""
    camera = PtpCamera()
    with pytest.raises(TimeoutError):
        wait(camera, timeout=1)


def test_thumbnail_download():
    """thumbnail=True fetches the thumbnail only."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    camera.thumbs[3] = b"THUMB"
    assert wait(camera, thumbnail=True) == b"THUMB"
    assert camera.count(GET_OBJECT) == 0
    assert camera.objects == {}


def test_refused_thumbnail_falls_back_to_the_image():
    """A body refusing GetThumb gets the full download."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    assert wait(camera, thumbnail=True) == b"JPEG"


def test_refused_listing_raises_with_its_code():
    """A refused GetObjectHandles keeps rawji's message shape."""
    camera = PtpCamera()
    camera.handles_code = 0x2019
    with pytest.raises(ConversionError, match="0x2019"):
        wait(camera)


def test_lost_reply_propagates_without_more_commands():
    """After a transport error no delete is sent on the poisoned session."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    camera.lost_reply_on = GET_OBJECT
    with pytest.raises(OSError, match="timed out"):
        wait(camera)
    assert camera.count(DELETE_OBJECT) == 0


def test_refused_delete_does_not_lose_the_image():
    """The image is still returned when the camera refuses the delete."""
    camera = PtpCamera({3: [JPEG_FORMAT, b"JPEG"]})
    camera.delete_code = 0x2019
    assert wait(camera) == b"JPEG"
    assert 3 in camera.objects


def test_clear_inside_a_session_keeps_the_upload():
    """Leftover results go; the RAF and undescribed objects stay."""
    camera = PtpCamera(
        {
            1: [UPLOAD_FORMAT, b"RAF"],
            2: [JPEG_FORMAT, b"OLD"],
            3: [JPEG_FORMAT, b"BUSY"],
        }
    )
    camera.info_refusals[3] = 1
    assert clear_leftovers(camera, keep_upload=True) == {1, 3}
    assert set(camera.objects) == {1, 3}


def test_clear_before_an_upload_deletes_everything():
    """Before uploading, every object is an orphan."""
    camera = PtpCamera({1: [UPLOAD_FORMAT, b"RAF"], 2: [JPEG_FORMAT, b"OLD"]})
    assert clear_leftovers(camera, keep_upload=False) == set()
    assert camera.objects == {}


def test_clear_is_best_effort():
    """A refused listing is not an error."""
    camera = PtpCamera()
    camera.handles_code = 0x2019
    assert clear_leftovers(camera, keep_upload=True) == set()


def test_bogus_handle_count_is_clamped():
    """A count larger than the data does not overrun."""

    class Liar:
        def send_command(self, code, params=None):
            return OK, [], struct.pack("<II", 1000, 7)

    assert object_handles(Liar()) == [7]

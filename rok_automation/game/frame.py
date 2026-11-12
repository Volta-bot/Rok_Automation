"""Game-frame geometry shared by every capture backend.

The UI templates and OCR regions are authored against a 1280x720 game
frame. Normalising every capture to that size keeps detection identical
no matter which emulator, resolution or window size produced it, so this
module is deliberately free of any emulator or backend specifics.
"""

from __future__ import annotations

from typing import Any

# The UI templates and OCR region ratios are authored against a 1280x720 game
# frame. Normalising every capture to that size keeps detection identical no
# matter how large or small the emulator window happens to be on screen.
CANONICAL_CAPTURE_SIZE = (1280, 720)
# A capture may deviate from the canonical aspect by this much before it is
# treated as non-16:9 and cropped instead of stretched.
_ASPECT_TOLERANCE = 0.02


def compute_capture_crop(
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """Return the region of a render window that the canonical frame covers.

    Returns ``(left, top, crop_width, crop_height)`` in render pixels. For a
    16:9 render window this is the whole window. A window with a different
    aspect is centre-cropped so the game content is never stretched.

    The result depends only on the render size, so the capture and the click
    mapping always agree without sharing any state.
    """

    target_width, target_height = CANONICAL_CAPTURE_SIZE
    if width <= 0 or height <= 0:
        return 0, 0, max(0, width), max(0, height)

    source_aspect = width / height
    target_aspect = target_width / target_height
    if abs(source_aspect - target_aspect) <= _ASPECT_TOLERANCE:
        return 0, 0, width, height

    if source_aspect > target_aspect:
        crop_width = max(1, round(height * target_aspect))
        left = max(0, (width - crop_width) // 2)
        return left, 0, crop_width, height

    crop_height = max(1, round(width / target_aspect))
    top = max(0, (height - crop_height) // 2)
    return 0, top, width, crop_height


def normalize_capture(frame: Any) -> Any:
    """Rescale a captured game frame to the canonical detection size.

    The emulator renders the game at a fixed guest resolution and scales it
    to fit its window, so the captured pixels change size whenever the
    window is resized. Rescaling every capture back to a single canonical
    size keeps template matching, OCR regions and the pinned-position cache
    stable regardless of the window size.

    Frames that are not 16:9 are centre-cropped to the canonical aspect
    first so the game content is never stretched.
    """

    import cv2

    height, width = frame.shape[:2]
    target_width, target_height = CANONICAL_CAPTURE_SIZE
    if width == target_width and height == target_height:
        return frame

    left, top, crop_width, crop_height = compute_capture_crop(width, height)
    if crop_width <= 0 or crop_height <= 0:
        return frame
    cropped = frame[top : top + crop_height, left : left + crop_width]

    scale = target_width / crop_width
    # Shrinking uses area averaging (best detail retention). Growing uses
    # bilinear rather than cubic/lanczos: those overshoot around sharp edges
    # and the ringing confuses the OCR that reads the small level label.
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(
        cropped,
        (target_width, target_height),
        interpolation=interpolation,
    )

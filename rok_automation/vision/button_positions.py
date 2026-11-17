"""Session-wide positions with a small visual check before reuse."""

from collections import OrderedDict

import cv2


class ButtonPositionCache:
    def __init__(self, limit=128):
        self.entries = OrderedDict()
        self.limit = limit

    def remember(self, frame, name, bounds):
        x, y, w, h = bounds
        if min(w, h) < 12 or x < 0 or y < 0 or x + w > frame.shape[1] or y + h > frame.shape[0]:
            return
        patch = frame[y:y+h, x:x+w].copy()
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
        if float(gray.std()) < 8:
            return
        key = (frame.shape, name)
        self.entries[key] = (bounds, patch)
        self.entries.move_to_end(key)
        while len(self.entries) > self.limit:
            self.entries.popitem(last=False)

    def locate(self, frame, name):
        key = (frame.shape, name)
        entry = self.entries.get(key)
        if entry is None:
            return None
        (x, y, w, h), template = entry
        # Look only a few pixels around the remembered position. The original
        # identity must still match; a nearby different button is rejected.
        pad = 8
        left, top = max(0, x-pad), max(0, y-pad)
        roi = frame[top:min(frame.shape[0], y+h+pad), left:min(frame.shape[1], x+w+pad)]
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))
        px, py = left + point[0], top + point[1]
        patch = frame[py:py+h, px:px+w]
        if score < .97 or float(cv2.absdiff(patch, template).mean()) > 12:
            del self.entries[key]
            return None
        bounds = (px, py, w, h)
        self.entries[key] = (bounds, template)
        self.entries.move_to_end(key)
        return bounds


# Survives individual character/Gmail runners within this worker process.
STATIC_BUTTON_POSITIONS = ButtonPositionCache()

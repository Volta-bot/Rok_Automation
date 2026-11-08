"""Detect a green dotted march route pointing at the selected map location."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class GreenRouteDetection:
    detected: bool
    confidence: float
    evidence: str


def detect_green_incoming_route(
    frame: Any,
    *,
    target_center: tuple[float, float] | None = None,
) -> GreenRouteDetection:
    """Detect a green dotted route whose arrow marker points toward the node.

    Search results are camera-centered in the supplied game screenshots. The
    default target point is therefore near the middle of the captured map.
    Detection combines a saturated-green mask, a large arrow marker component,
    and a dotted line whose direction and extrapolation lead toward that point.
    """

    if frame is None or len(frame.shape) < 2:
        return GreenRouteDetection(False, 0.0, "invalid screenshot frame")

    frame_height, frame_width = frame.shape[:2]
    if frame_height < 80 or frame_width < 80:
        return GreenRouteDetection(False, 0.0, "screenshot is too small")

    if target_center is None:
        # The camera places the selected node near the center, but the info
        # panel and aspect ratio shift it vertically by several percent.
        target_x = frame_width * 0.50
        target_y = frame_height * 0.50
    else:
        target_x = frame_width * target_center[0]
        target_y = frame_height * target_center[1]

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # March-route dots are vivid green. Keep saturation high to exclude map
    # foliage and terrain, which triggered false positives with a broad mask.
    green_mask = cv2.inRange(
        hsv,
        np.array((43, 185, 125), dtype=np.uint8),
        np.array((82, 255, 255), dtype=np.uint8),
    )

    # Exclude the edge HUD and chat region; the route itself may enter this
    # area, but its arrow marker and enough of the dotted trail remain visible.
    roi_left = round(frame_width * 0.10)
    roi_right = round(frame_width * 0.92)
    roi_top = round(frame_height * 0.12)
    roi_bottom = round(frame_height * 0.90)
    roi = green_mask[roi_top:roi_bottom, roi_left:roi_right]
    if roi.size == 0:
        return GreenRouteDetection(False, 0.0, "map search area is empty")

    min_dimension = min(frame_width, frame_height)
    scale = min_dimension / 1111.0
    marker_min_side = max(5, round(14 * scale))
    marker_max_side = max(marker_min_side + 2, round(60 * scale))
    frame_area = frame_width * frame_height
    marker_min_area = max(18, round(frame_area * 0.000035))
    marker_max_area = max(marker_min_area + 1, round(frame_area * 0.002))

    component_count, _labels, stats, centers = cv2.connectedComponentsWithStats(
        roi, connectivity=8
    )
    marker_candidates: list[tuple[float, float, int]] = []
    dot_candidates: list[tuple[float, float]] = []
    for component_index in range(1, component_count):
        x, y, width, height, area = stats[component_index]
        center_x, center_y = centers[component_index]
        dot_max_side = max(8, round(22 * scale))
        if (
            3 <= area <= max(90, round(frame_area * 0.0003))
            and 2 <= width <= dot_max_side
            and 2 <= height <= dot_max_side
            and 0.45 <= width / max(height, 1) <= 2.2
        ):
            dot_candidates.append(
                (float(center_x + roi_left), float(center_y + roi_top))
            )
        if not (
            marker_min_side <= width <= marker_max_side
            and marker_min_side <= height <= marker_max_side
            and marker_min_area <= area <= marker_max_area
        ):
            continue
        marker_candidates.append(
            (float(center_x + roi_left), float(center_y + roi_top), int(area))
        )

    # Close the small gaps between the route's bright dots before line
    # detection. Kernel size scales with the emulator capture resolution.
    # Bridge route-dot gaps without merging nearby map details into long lines.
    close_size = max(3, round(9 * scale))
    if close_size % 2 == 0:
        close_size += 1
    closed = cv2.morphologyEx(
        roi,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (close_size, close_size)
        ),
    )
    line_threshold = max(8, round(16 * scale))
    min_line_length = max(18, round(min_dimension * 0.08))
    max_line_gap = max(8, round(min_dimension * 0.03))
    lines = cv2.HoughLinesP(
        closed,
        rho=1,
        theta=np.pi / 180,
        threshold=line_threshold,
        minLineLength=min_line_length,
        maxLineGap=max_line_gap,
    )
    if lines is None:
        return GreenRouteDetection(
            False,
            0.0,
            "no elongated green dotted trail",
        )

    target = np.array((target_x, target_y), dtype=np.float32)
    max_target_distance = min_dimension * 0.24
    max_marker_line_gap = min_dimension * 0.04
    max_line_offset = min_dimension * 0.06
    best_score = 0.0

    for marker_x, marker_y, marker_area in marker_candidates:
        marker = np.array((marker_x, marker_y), dtype=np.float32)
        target_vector = target - marker
        target_distance = float(np.linalg.norm(target_vector))
        if (
            target_distance < min_dimension * 0.025
            or target_distance > max_target_distance
        ):
            continue

        for line in lines[:, 0, :]:
            x1, y1, x2, y2 = (float(value) for value in line)
            first = np.array((x1 + roi_left, y1 + roi_top), dtype=np.float32)
            second = np.array((x2 + roi_left, y2 + roi_top), dtype=np.float32)
            # Use the endpoint closest to the arrow marker as the trail's
            # beginning; dots should extend away from the selected resource.
            near, far = (
                (first, second)
                if np.linalg.norm(first - marker) <= np.linalg.norm(second - marker)
                else (second, first)
            )
            marker_line_gap = float(np.linalg.norm(near - marker))
            if marker_line_gap > max_marker_line_gap:
                continue

            trail_vector = far - marker
            trail_length = float(np.linalg.norm(trail_vector))
            if trail_length < min_line_length:
                continue
            cosine = float(np.dot(target_vector, trail_vector)) / (
                target_distance * trail_length
            )
            # The route dots must run away from the node in the opposite
            # direction from the arrow marker's target.
            if cosine > -0.72:
                continue

            line_vector = second - first
            denominator = float(np.dot(line_vector, line_vector))
            if denominator <= 0:
                continue
            projection = float(np.dot(target - first, line_vector) / denominator)
            if not -0.20 <= projection <= 1.20:
                continue
            projected_target = first + projection * line_vector
            line_offset = float(np.linalg.norm(target - projected_target))
            # Permit extrapolation up to the arrow-marker distance: the
            # reference route ends before reaching the resource itself.
            if line_offset > max_line_offset:
                continue

            # Require several separate bright-green dots along the trail.
            # A single green HUD glyph or patch of terrain can no longer
            # validate a candidate line by itself.
            trail_denominator = float(np.dot(trail_vector, trail_vector))
            aligned_dot_positions = []
            max_dot_offset = max(3.0, min_dimension * 0.012)
            for dot_x, dot_y in dot_candidates:
                dot = np.array((dot_x, dot_y), dtype=np.float32)
                relative = dot - marker
                position = float(np.dot(relative, trail_vector) / trail_denominator)
                projection_point = marker + position * trail_vector
                if (
                    -0.05 <= position <= 1.15
                    and float(np.linalg.norm(dot - projection_point)) <= max_dot_offset
                    and float(np.linalg.norm(relative)) > max_marker_line_gap
                ):
                    aligned_dot_positions.append(position)
            if len(aligned_dot_positions) < 3:
                continue
            if max(aligned_dot_positions) - min(aligned_dot_positions) < 0.12:
                continue

            angle_score = min(1.0, max(0.0, (-cosine - 0.72) / 0.28))
            offset_score = max(0.0, 1.0 - line_offset / max_line_offset)
            area_score = min(1.0, marker_area / max(marker_min_area * 2, 1))
            dot_score = min(1.0, len(aligned_dot_positions) / 6)
            score = (
                0.48 * angle_score
                + 0.32 * offset_score
                + 0.08 * area_score
                + 0.12 * dot_score
            )
            best_score = max(best_score, score)

    if best_score >= 0.35:
        return GreenRouteDetection(
            True,
            best_score,
            "green dotted march route points toward the selected resource",
        )
    return GreenRouteDetection(
        False,
        best_score,
        "no green dotted route aligned with the selected resource",
    )

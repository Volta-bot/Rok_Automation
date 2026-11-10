"""Windows window discovery and desktop-region capture for LDPlayer."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EmulatorWindow:
    """A matching visible emulator window."""

    handle: int
    title: str


def _enable_per_monitor_dpi_awareness() -> None:
    """Make Win32 window bounds use physical pixels for MSS captures."""

    if sys.platform != "win32":
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    set_awareness = user32.SetProcessDpiAwarenessContext
    set_awareness.argtypes = (ctypes.c_void_p,)
    set_awareness.restype = wintypes.BOOL

    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the documented -4 handle.
    if not set_awareness(ctypes.c_void_p(-4)):
        error_code = ctypes.get_last_error()
        if error_code != 5:  # ERROR_ACCESS_DENIED: process awareness already set.
            raise ctypes.WinError(error_code)

        get_thread_context = user32.GetThreadDpiAwarenessContext
        get_thread_context.argtypes = ()
        get_thread_context.restype = ctypes.c_void_p
        get_awareness = user32.GetAwarenessFromDpiAwarenessContext
        get_awareness.argtypes = (ctypes.c_void_p,)
        get_awareness.restype = ctypes.c_int
        current_awareness = get_awareness(get_thread_context())
        if current_awareness != 2:  # DPI_AWARENESS_PER_MONITOR_AWARE
            raise RuntimeError(
                "Could not enable per-monitor DPI awareness. Run the command "
                "in a fresh Python process before creating any app windows."
            )


def find_ldplayer_window(
    title_terms: tuple[str, ...] = ("ldplayer", "雷电"),
) -> EmulatorWindow | None:
    """Return the first visible top-level window matching a title term.

    Matching is case-insensitive. The function returns ``None`` when no
    matching window is open. It uses Win32 window enumeration and has no
    third-party runtime dependencies.
    """

    if sys.platform != "win32":
        raise RuntimeError("LDPlayer window detection is currently Windows-only")

    _enable_per_monitor_dpi_awareness()

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    enum_windows = user32.EnumWindows
    enum_windows.argtypes = (enum_callback_type, wintypes.LPARAM)
    enum_windows.restype = wintypes.BOOL

    is_window_visible = user32.IsWindowVisible
    is_window_visible.argtypes = (wintypes.HWND,)
    is_window_visible.restype = wintypes.BOOL

    get_window_text_length = user32.GetWindowTextLengthW
    get_window_text_length.argtypes = (wintypes.HWND,)
    get_window_text_length.restype = ctypes.c_int

    get_window_text = user32.GetWindowTextW
    get_window_text.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    get_window_text.restype = ctypes.c_int

    terms = tuple(term.casefold() for term in title_terms if term.strip())
    matches: list[EmulatorWindow] = []

    @enum_callback_type
    def visit_window(hwnd: int, _lparam: int) -> bool:
        if not is_window_visible(hwnd):
            return True

        text_length = get_window_text_length(hwnd)
        if text_length <= 0:
            return True

        title_buffer = ctypes.create_unicode_buffer(text_length + 1)
        get_window_text(hwnd, title_buffer, len(title_buffer))
        title = title_buffer.value.strip()
        if title and any(term in title.casefold() for term in terms):
            matches.append(EmulatorWindow(handle=int(hwnd), title=title))
            return False  # Stop at the first matching visible window.

        return True

    enum_windows(visit_window, 0)
    return matches[0] if matches else None


def describe_window_tree(window: EmulatorWindow) -> list[str]:
    """Describe the emulator window and its child windows for capture tuning."""

    if sys.platform != "win32":
        raise RuntimeError("LDPlayer window inspection is currently Windows-only")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    enum_child_windows = user32.EnumChildWindows
    enum_child_windows.argtypes = (
        wintypes.HWND,
        enum_callback_type,
        wintypes.LPARAM,
    )
    enum_child_windows.restype = wintypes.BOOL

    get_window_rect = user32.GetWindowRect
    get_window_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    get_window_rect.restype = wintypes.BOOL

    get_class_name = user32.GetClassNameW
    get_class_name.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    get_class_name.restype = ctypes.c_int

    get_window_text = user32.GetWindowTextW
    get_window_text.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    get_window_text.restype = ctypes.c_int

    is_window_visible = user32.IsWindowVisible
    is_window_visible.argtypes = (wintypes.HWND,)
    is_window_visible.restype = wintypes.BOOL

    descriptions: list[str] = []

    def describe(hwnd: int, depth: int) -> str:
        rect = wintypes.RECT()
        get_window_rect(hwnd, ctypes.byref(rect))
        class_buffer = ctypes.create_unicode_buffer(256)
        get_class_name(hwnd, class_buffer, len(class_buffer))
        title_buffer = ctypes.create_unicode_buffer(512)
        get_window_text(hwnd, title_buffer, len(title_buffer))
        bounds = f"({rect.left},{rect.top})-({rect.right},{rect.bottom})"
        title = title_buffer.value.strip()
        return (
            f"{'  ' * depth}HWND={int(hwnd)} visible={bool(is_window_visible(hwnd))} "
            f"class={class_buffer.value!r} title={title!r} rect={bounds}"
        )

    descriptions.append(describe(window.handle, 0))

    @enum_callback_type
    def visit_child(hwnd: int, _lparam: int) -> bool:
        descriptions.append(describe(hwnd, 1))
        return True

    enum_child_windows(wintypes.HWND(window.handle), visit_child, 0)
    return descriptions


def _find_render_window_handle(parent_handle: int) -> int | None:
    """Find LDPlayer's game-render child, if present."""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    enum_child_windows = user32.EnumChildWindows
    enum_child_windows.argtypes = (
        wintypes.HWND,
        enum_callback_type,
        wintypes.LPARAM,
    )
    enum_child_windows.restype = wintypes.BOOL

    get_class_name = user32.GetClassNameW
    get_class_name.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    get_class_name.restype = ctypes.c_int

    is_window_visible = user32.IsWindowVisible
    is_window_visible.argtypes = (wintypes.HWND,)
    is_window_visible.restype = wintypes.BOOL

    get_window_rect = user32.GetWindowRect
    get_window_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    get_window_rect.restype = wintypes.BOOL

    candidates: list[tuple[int, int]] = []

    @enum_callback_type
    def visit_child(hwnd: int, _lparam: int) -> bool:
        if not is_window_visible(hwnd):
            return True

        class_buffer = ctypes.create_unicode_buffer(256)
        get_class_name(hwnd, class_buffer, len(class_buffer))
        if class_buffer.value.casefold() != "renderwindow":
            return True

        rect = wintypes.RECT()
        if get_window_rect(hwnd, ctypes.byref(rect)):
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            if area:
                candidates.append((area, int(hwnd)))
        return True

    enum_child_windows(wintypes.HWND(parent_handle), visit_child, 0)
    return max(candidates)[1] if candidates else None


def capture_ldplayer_window(window: EmulatorWindow, output_path: Path) -> Path:
    """Capture the visible LDPlayer game-render area to a PNG file.

    This is a desktop-region capture: minimized, covered, or off-screen game
    areas cannot be captured as emulator content. ``output_path`` may be
    relative to the current working directory.
    """

    if sys.platform != "win32":
        raise RuntimeError("LDPlayer screenshot capture is currently Windows-only")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = wintypes.HWND(window.handle)

    is_window = user32.IsWindow
    is_window.argtypes = (wintypes.HWND,)
    is_window.restype = wintypes.BOOL

    is_window_visible = user32.IsWindowVisible
    is_window_visible.argtypes = (wintypes.HWND,)
    is_window_visible.restype = wintypes.BOOL

    is_iconic = user32.IsIconic
    is_iconic.argtypes = (wintypes.HWND,)
    is_iconic.restype = wintypes.BOOL

    if not is_window(hwnd):
        raise RuntimeError("The detected LDPlayer window is no longer available")
    if not is_window_visible(hwnd) or is_iconic(hwnd):
        raise RuntimeError("LDPlayer must be visible and unminimized to capture it")

    render_handle = _find_render_window_handle(window.handle)
    if render_handle is None:
        raise RuntimeError(
            "Could not find LDPlayer's RenderWindow child; run with --diagnose-window"
        )

    render_hwnd = wintypes.HWND(render_handle)
    get_window_rect = user32.GetWindowRect
    get_window_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    get_window_rect.restype = wintypes.BOOL
    render_rect = wintypes.RECT()
    if not get_window_rect(render_hwnd, ctypes.byref(render_rect)):
        raise ctypes.WinError(ctypes.get_last_error())

    width = render_rect.right - render_rect.left
    height = render_rect.bottom - render_rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("LDPlayer's game render area is empty")

    region = {
        "left": render_rect.left,
        "top": render_rect.top,
        "width": width,
        "height": height,
    }

    try:
        import mss
        import mss.tools
    except ImportError as exc:
        raise RuntimeError(
            "Screen capture dependency missing; install the project with `python -m pip install -e .`"
        ) from exc

    destination = output_path.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    resolved = destination.resolve()
    try:
        import cv2
        import numpy as np
        from rok_automation.vision.screen import cache_captured_frame

        with mss.MSS() as screen:
            shot = screen.grab(region)
            bgr = np.asarray(shot)[:, :, :3].copy()
            cv2.imwrite(str(resolved), bgr)
            cache_captured_frame(resolved, bgr)
    except Exception as exc:
        raise RuntimeError(f"Desktop capture failed: {exc}") from exc

    return resolved


def click_ldplayer_point(
    window: EmulatorWindow,
    point: tuple[int, int],
    frame_size: tuple[int, int],
) -> None:
    """Click a screenshot-space point inside LDPlayer's visible render child."""

    if sys.platform != "win32":
        raise RuntimeError("LDPlayer mouse interaction is currently Windows-only")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    parent = wintypes.HWND(window.handle)
    is_window = user32.IsWindow
    is_window.argtypes = (wintypes.HWND,)
    is_window.restype = wintypes.BOOL
    is_window_visible = user32.IsWindowVisible
    is_window_visible.argtypes = (wintypes.HWND,)
    is_window_visible.restype = wintypes.BOOL
    is_iconic = user32.IsIconic
    is_iconic.argtypes = (wintypes.HWND,)
    is_iconic.restype = wintypes.BOOL
    if not is_window(parent) or not is_window_visible(parent):
        raise RuntimeError("The detected LDPlayer window is no longer visible")
    if is_iconic(parent):
        raise RuntimeError("LDPlayer must be unminimized before farming")

    render_handle = _find_render_window_handle(window.handle)
    if render_handle is None:
        raise RuntimeError("Could not find LDPlayer's RenderWindow child")

    get_window_rect = user32.GetWindowRect
    get_window_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    get_window_rect.restype = wintypes.BOOL
    rect = wintypes.RECT()
    if not get_window_rect(wintypes.HWND(render_handle), ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())

    frame_height, frame_width = frame_size
    render_width = rect.right - rect.left
    render_height = rect.bottom - rect.top
    image_x, image_y = point
    if (
        frame_width <= 0
        or frame_height <= 0
        or not 0 <= image_x < frame_width
        or not 0 <= image_y < frame_height
    ):
        raise ValueError("The click point is outside the captured game frame")

    screen_x = rect.left + round(image_x * render_width / frame_width)
    screen_y = rect.top + round(image_y * render_height / frame_height)
    set_cursor_pos = user32.SetCursorPos
    set_cursor_pos.argtypes = (ctypes.c_int, ctypes.c_int)
    set_cursor_pos.restype = wintypes.BOOL
    if not set_cursor_pos(screen_x, screen_y):
        raise ctypes.WinError(ctypes.get_last_error())

    mouse_event = user32.mouse_event
    mouse_event.argtypes = (
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    )
    mouse_event.restype = None
    mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    time.sleep(0.025)
    mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP


def drag_ldplayer_points(
    window: EmulatorWindow,
    start: tuple[int, int],
    end: tuple[int, int],
    frame_size: tuple[int, int],
) -> None:
    """Drag between screenshot-space points inside LDPlayer's render child."""

    if sys.platform != "win32":
        raise RuntimeError("LDPlayer mouse interaction is currently Windows-only")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    parent = wintypes.HWND(window.handle)
    if not user32.IsWindow(parent) or not user32.IsWindowVisible(parent):
        raise RuntimeError("The detected LDPlayer window is no longer visible")
    if user32.IsIconic(parent):
        raise RuntimeError("LDPlayer must be unminimized before farming")

    render_handle = _find_render_window_handle(window.handle)
    if render_handle is None:
        raise RuntimeError("Could not find LDPlayer's RenderWindow child")
    rect = wintypes.RECT()
    get_window_rect = user32.GetWindowRect
    get_window_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    get_window_rect.restype = wintypes.BOOL
    if not get_window_rect(wintypes.HWND(render_handle), ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())

    frame_height, frame_width = frame_size
    render_width, render_height = rect.right - rect.left, rect.bottom - rect.top

    def to_screen(point: tuple[int, int]) -> tuple[int, int]:
        x, y = point
        if not (0 <= x < frame_width and 0 <= y < frame_height):
            raise ValueError("The drag point is outside the captured game frame")
        return (
            rect.left + round(x * render_width / frame_width),
            rect.top + round(y * render_height / frame_height),
        )

    start_screen, end_screen = to_screen(start), to_screen(end)
    set_cursor_pos = user32.SetCursorPos
    set_cursor_pos.argtypes = (ctypes.c_int, ctypes.c_int)
    set_cursor_pos.restype = wintypes.BOOL
    if not set_cursor_pos(*start_screen):
        raise ctypes.WinError(ctypes.get_last_error())

    mouse_event = user32.mouse_event
    mouse_event.argtypes = (
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    )
    mouse_event.restype = None
    mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    distance = max(abs(end_screen[0] - start_screen[0]), abs(end_screen[1] - start_screen[1]))
    steps = max(6, min(14, distance // 25))
    for step in range(1, steps + 1):
        ratio = step / steps
        x = round(start_screen[0] + (end_screen[0] - start_screen[0]) * ratio)
        y = round(start_screen[1] + (end_screen[1] - start_screen[1]) * ratio)
        if not set_cursor_pos(x, y):
            mouse_event(0x0004, 0, 0, 0, 0)
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(0.006)
    time.sleep(0.04)
    mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP

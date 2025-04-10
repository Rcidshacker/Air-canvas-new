import mediapipe as mp
import cv2
import numpy as np
import time
import os
from collections import deque
from threading import Thread, Lock, Event
import matplotlib
from dataclasses import dataclass, field, fields
from typing import List, Tuple, Optional, Dict
import logging
import yaml
import math
import pytesseract
from PIL import Image

# Set Tesseract executable path (adjust if needed)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

###############################################################################
# Config & SharedState
###############################################################################
@dataclass
class Config:
    device: str = 'cuda'
    model_dir: str = "./model_weights"
    model_filename: str = "depth_anything_v2_vits.pth"
    model_type: str = 'vits'
    input_size: int = 256
    pen_down_threshold: int = 50
    window_width: int = 1280
    window_height: int = 800
    max_points: int = 1024
    brush_sizes: List[int] = field(default_factory=lambda: [5, 10, 15, 20])
    colors: List[Tuple[int, int, int]] = field(default_factory=lambda: [
        (255, 255, 255), (0, 0, 0), (255, 0, 0),
        (0, 255, 0), (0, 0, 255), (255, 255, 0)
    ])
    use_depth: bool = True
    eraser_sizes: List[int] = field(default_factory=lambda: [20, 40, 60])
    eraser_size_index: int = 0
    depth_threshold: float = 0.5
    initial_brightness: float = 1.0
    initial_contrast: float = 1.0
    min_detection_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    cursor_image_path: str = "cursor.png"
    cursor_scale: float = 0.06
    depth_frame_skip: int = 5
    top_bar_height: int = 50
    palette_width: int = 200
    palette_columns: int = 2
    spacing: int = 10
    tool_icons: Dict[str, str] = field(default_factory=lambda: {
        'line': 'line.png',
        'rectangle': 'rectangle.png',
        'draw': 'draw.png',
        'erase': 'eraser.png',
        'circle': 'circle.png'
    })
    tool_icon_scale: float = 0.1

    # Multi-distance pinch detection thresholds (in pixels; later normalized)
    multi_pinch_threshold: float = 40.0
    multi_separation_threshold: float = 70.0

    # Kalman filter tuning parameters (unquoted numbers in config.yaml)
    kalman_process_noise: float = 1e-4
    kalman_measurement_noise: float = 1e-1

    @classmethod
    def from_yaml(cls, path: str) -> 'Config':
        import torch
        if os.path.exists(path):
            with open(path, 'r') as f:
                config_data = yaml.safe_load(f)
                if 'device' in config_data and config_data['device'] == 'cuda' and not torch.cuda.is_available():
                    print("WARNING: CUDA requested in config, but not available. Using CPU.")
                    config_data['device'] = 'cpu'
                valid_keys = {field.name for field in fields(cls)}
                filtered_config_data = {k: v for k, v in config_data.items() if k in valid_keys}
                return cls(**filtered_config_data)
        return cls()

class SharedState:
    def __init__(self) -> None:
        self.frame: Optional[np.ndarray] = None
        self.depth_display: Optional[np.ndarray] = None
        self.data_lock: Lock = Lock()
        self.stop_event: Event = Event()
        self.new_depth_available: Event = Event()
        self.latest_depth: Optional[np.ndarray] = None

###############################################################################
# OCR Function
###############################################################################
def perform_ocr(image: np.ndarray) -> str:
    pil_img = Image.fromarray(image)
    text = pytesseract.image_to_string(pil_img)
    return text

###############################################################################
# Global variables
###############################################################################
curr_tool: str = "draw"
prevx: int = 0
prevy: int = 0

###############################################################################
# AirPainting Class with Catmull–Rom Spline and Continuous Stroke Preview
###############################################################################
class AirPainting:
    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.state: SharedState = SharedState()
        self.points: List[deque] = [deque(maxlen=self.config.max_points) for _ in range(len(self.config.colors))]
        self.color_index: int = 0
        self.brush_size_index: int = 0
        self.paint_window: np.ndarray = self._init_canvas()
        self._setup_logging()

        # Depth model initialization if using depth
        if self.config.use_depth:
            self._init_model()
        self._init_mediapipe()
        if self.config.use_depth:
            self._start_depth_thread()

        # Load tool icons
        self.tools: Dict[str, np.ndarray] = self._load_tools()

        # Pinch detection and shape creation state
        self.pinching = False
        self.drawing_shape = False
        self.shape_start_x = 0
        self.shape_start_y = 0

        # For the "draw" tool, accumulate stroke points for spline interpolation
        self.current_stroke_points: List[Tuple[int, int]] = []

        # Initialize Kalman filter for fingertip smoothing using config values
        process_noise = np.eye(4, dtype=np.float32) * float(self.config.kalman_process_noise)
        measurement_noise = np.eye(2, dtype=np.float32) * float(self.config.kalman_measurement_noise)
        self.kalman = cv2.KalmanFilter(4, 2)
        self.kalman.transitionMatrix = np.array([[1, 0, 1, 0],
                                                 [0, 1, 0, 1],
                                                 [0, 0, 1, 0],
                                                 [0, 0, 0, 1]], np.float32)
        self.kalman.measurementMatrix = np.array([[1, 0, 0, 0],
                                                  [0, 1, 0, 0]], np.float32)
        self.kalman.processNoiseCov = process_noise
        self.kalman.measurementNoiseCov = measurement_noise
        self.kalman.errorCovPost = np.eye(4, dtype=np.float32)

        # Store filtered coordinates for cursor drawing.
        self.filtered_x = 0
        self.filtered_y = 0

        self.frame_times = deque(maxlen=30)
        self.last_time = time.time()
        self.brightness = self.config.initial_brightness
        self.contrast = self.config.initial_contrast
        self.last_draw_x = None
        self.last_draw_y = None
        self.cursor_image = self._load_cursor_image()
        self.frame_count = 0

    @staticmethod
    def catmull_rom_spline(P: List[Tuple[int, int]], nPoints: int = 20) -> List[Tuple[int, int]]:
        """
        Compute Catmull–Rom spline for a list of points P.
        If there are fewer than 4 points, use linear interpolation.
        """
        if len(P) < 4:
            result = []
            for i in range(len(P) - 1):
                for t in np.linspace(0, 1, nPoints, endpoint=False):
                    x = P[i][0] + (P[i+1][0] - P[i][0]) * t
                    y = P[i][1] + (P[i+1][1] - P[i][1]) * t
                    result.append((int(x), int(y)))
            result.append(P[-1])
            return result

        curve = []
        for i in range(1, len(P) - 2):
            p0 = np.array(P[i - 1], dtype=float)
            p1 = np.array(P[i], dtype=float)
            p2 = np.array(P[i + 1], dtype=float)
            p3 = np.array(P[i + 2], dtype=float)
            for t in np.linspace(0, 1, nPoints, endpoint=False):
                t2 = t * t
                t3 = t2 * t
                point = 0.5 * ((2 * p1) +
                               (-p0 + p2) * t +
                               (2*p0 - 5*p1 + 4*p2 - p3) * t2 +
                               (-p0 + 3*p1 - 3*p2 + p3) * t3)
                curve.append((int(point[0]), int(point[1])))
        curve.append(P[-2])
        curve.append(P[-1])
        return curve

    def _setup_logging(self) -> None:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    def _init_canvas(self) -> np.ndarray:
        return np.ones((self.config.window_height, self.config.window_width, 3), dtype=np.uint8) * 255

    def _load_tools(self) -> Dict[str, np.ndarray]:
        tools = {}
        for tool_name, image_path in self.config.tool_icons.items():
            try:
                icon = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
                if icon is None:
                    raise FileNotFoundError(f"Could not load tool icon at {image_path}")
                if icon.shape[2] != 4:
                    raise ValueError(f"Tool icon {image_path} must have an alpha channel.")
                width = int(icon.shape[1] * self.config.tool_icon_scale)
                height = int(icon.shape[0] * self.config.tool_icon_scale)
                icon = cv2.resize(icon, (width, height))
                tools[tool_name] = icon
            except (FileNotFoundError, ValueError) as e:
                logging.error(e)
                tools[tool_name] = np.zeros((32, 32, 4), dtype=np.uint8)
                tools[tool_name][:, :, 2] = 255
                tools[tool_name][:, :, 3] = 255
        return tools

    def _init_mediapipe(self) -> None:
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            max_num_hands=1,
            min_detection_confidence=self.config.min_detection_confidence,
            min_tracking_confidence=self.config.min_tracking_confidence
        )
        self.mp_drawing = mp.solutions.drawing_utils

    def _load_cursor_image(self) -> Optional[np.ndarray]:
        try:
            cursor_image = cv2.imread(self.config.cursor_image_path, cv2.IMREAD_UNCHANGED)
            if cursor_image is None:
                raise FileNotFoundError(f"Cursor image not found at {self.config.cursor_image_path}")
            if cursor_image.shape[2] != 4:
                raise ValueError("Cursor image must have an alpha channel (transparency).")
            new_width = int(cursor_image.shape[1] * self.config.cursor_scale)
            new_height = int(cursor_image.shape[0] * self.config.cursor_scale)
            return cv2.resize(cursor_image, (new_width, new_height))
        except (FileNotFoundError, ValueError) as e:
            logging.error(e)
            return None

    def _draw_cursor(self, frame: np.ndarray, x: int, y: int):
        if self.cursor_image is None:
            return
        cursor_h, cursor_w = self.cursor_image.shape[:2]
        x_offset, y_offset = x - cursor_w // 2, y - cursor_h // 2
        x_start, y_start = max(0, x_offset), max(0, y_offset)
        x_end, y_end = min(frame.shape[1], x_offset + cursor_w), min(frame.shape[0], y_offset + cursor_h)
        cursor_x_start = 0 if x_offset >= 0 else -x_offset
        cursor_y_start = 0 if y_offset >= 0 else -y_offset
        cursor_x_end = cursor_x_start + (x_end - x_start)
        cursor_y_end = cursor_y_start + (y_end - y_start)
        frame_region = frame[y_start:y_end, x_start:x_end]
        cursor_region = self.cursor_image[cursor_y_start:cursor_y_end, cursor_x_start:cursor_x_end]
        if frame_region.size == 0 or cursor_region.size == 0:
            return
        cursor_bgr = cursor_region[:, :, :3]
        cursor_alpha = cursor_region[:, :, 3] / 255.0
        for c in range(3):
            frame_region[:, :, c] = ((1 - cursor_alpha) * frame_region[:, :, c] +
                                     cursor_alpha * cursor_bgr[:, :, c])

    def _draw_ui(self, frame: np.ndarray) -> None:
        total_icon_width = sum(icon.shape[1] for icon in self.tools.values()) + self.config.spacing * (len(self.tools) - 1)
        total_icon_width -= self.config.spacing
        tool_x_start = (self.config.window_width - total_icon_width) // 2
        tool_x = tool_x_start
        for tool_name, icon in self.tools.items():
            icon_h, icon_w = icon.shape[:2]
            y_start, y_end = 0, min(self.config.top_bar_height, icon_h)
            x_start, x_end = tool_x, min(self.config.window_width, tool_x + icon_w)
            frame_region = frame[y_start:y_end, x_start:x_end]
            if frame_region.size == 0:
                logging.warning(f"Skipping tool icon {tool_name} due to invalid region size.")
                continue
            try:
                icon_region = icon[0:y_end, 0:x_end]
                icon_bgr = icon_region[:, :, :3]
                icon_alpha = icon_region[:, :, 3] / 255.0
                for c in range(3):
                    frame_region[:, :, c] = ((1 - icon_alpha) * frame_region[:, :, c] +
                                             icon_alpha * icon_bgr[:, :, c])
            except Exception as e:
                logging.error(f"Error drawing tool icon {tool_name}: {e}")
                continue
            if tool_name == curr_tool:
                cv2.rectangle(frame, (tool_x - 2, 0), (tool_x + icon_w + 2, self.config.top_bar_height), (0, 255, 0), 2)
            tool_x += icon_w + self.config.spacing

        palette_start_x = self.config.window_width - self.config.palette_width
        y_offset = self.config.top_bar_height + self.config.spacing
        color_swatch_size = 40
        for i, color in enumerate(self.config.colors):
            col = i % self.config.palette_columns
            row = i // self.config.palette_columns
            x_start = palette_start_x + col * (color_swatch_size + self.config.spacing)
            y_start = y_offset + row * (color_swatch_size + self.config.spacing)
            cv2.rectangle(frame, (x_start, y_start), (x_start + color_swatch_size, y_start + color_swatch_size), color, -1)
            if i == self.color_index:
                cv2.rectangle(frame, (x_start - 2, y_start - 2), (x_start + color_swatch_size + 2, y_start + color_swatch_size + 2), (0, 255, 0), 2)
        y_offset += ((len(self.config.colors) + self.config.palette_columns - 1) // self.config.palette_columns) * (color_swatch_size + self.config.spacing) + self.config.spacing

        for i, size in enumerate(self.config.brush_sizes):
            col = i % self.config.palette_columns
            row = i // self.config.palette_columns
            x = palette_start_x + col * (color_swatch_size + self.config.spacing) + color_swatch_size // 2
            y = y_offset + row * (color_swatch_size + self.config.spacing) + color_swatch_size // 2
            cv2.circle(frame, (x, y), size // 2, (0, 0, 0), -1)
            if i == self.brush_size_index:
                cv2.circle(frame, (x, y), size // 2 + 2, (0, 255, 0), 2)
        y_offset += ((len(self.config.brush_sizes) + self.config.palette_columns - 1) // self.config.palette_columns) * (color_swatch_size + self.config.spacing) + self.config.spacing

        for i, size in enumerate(self.config.eraser_sizes):
            col = i % self.config.palette_columns
            row = i // self.config.palette_columns
            x = palette_start_x + col * (color_swatch_size + self.config.spacing) + color_swatch_size // 2
            y = y_offset + row * (color_swatch_size + self.config.spacing) + color_swatch_size // 2
            cv2.circle(frame, (x, y), size // 2, (0, 0, 0), -1)
            if i == self.config.eraser_size_index:
                cv2.circle(frame, (x, y), size // 2 + 2, (0, 255, 0), 2)

        cv2.putText(frame, curr_tool, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        fps = self.calculate_fps()
        cv2.putText(frame, f"FPS: {fps:.2f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, f"B: {self.brightness:.1f}, C: {self.contrast:.1f}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    def _init_model(self) -> None:
        import torch
        model_configs = {
            'vits': {'encoder': 'vits', 'features': 64, 'out_channels': [48, 96, 192, 384]},
            'vitb': {'encoder': 'vitb', 'features': 128, 'out_channels': [96, 192, 384, 768]},
            'vitl': {'encoder': 'vitl', 'features': 256, 'out_channels': [256, 512, 1024, 1024]}
        }
        model_path = os.path.join(self.config.model_dir, self.config.model_filename)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model weights not found at {model_path}")
        try:
            from depth_anything_v2.dpt import DepthAnythingV2
            self.depth_model = DepthAnythingV2(**model_configs[self.config.model_type])
            self.depth_model.load_state_dict(torch.load(model_path, map_location='cpu', weights_only=True))
            self.depth_model = self.depth_model.to(self.config.device).eval()
            self.cmap = matplotlib.colormaps.get_cmap('Spectral_r')
        except Exception as e:
            logging.error(f"Model initialization failed: {e}")
            raise

    def _depth_estimation_worker(self) -> None:
        import torch
        while not self.state.stop_event.is_set():
            with self.state.data_lock:
                if self.state.frame is None:
                    continue
                current_frame = self.state.frame.copy()
            try:
                with torch.no_grad():
                    depth = self.depth_model.infer_image(current_frame, self.config.input_size)
                    depth_normalized = (depth - depth.min()) / (depth.max() - depth.min()) * 255.0
                    depth_normalized = depth_normalized.astype(np.uint8)
                    threshold_mask = depth_normalized < (self.config.depth_threshold * 255)
                    depth_colored = (self.cmap(depth_normalized)[:, :, :3] * 255)[:, :, ::-1].astype(np.uint8)
                    depth_colored[threshold_mask] = [0, 255, 0]
                with self.state.data_lock:
                    self.state.latest_depth = depth_colored
                    self.state.new_depth_available.set()
            except Exception as e:
                logging.error(f"Depth processing error: {e}")

    def _start_depth_thread(self) -> None:
        self.depth_thread = Thread(target=self._depth_estimation_worker, daemon=True)
        self.depth_thread.start()

    def _ui_selection_unconditional(self, index_x: int, index_y: int) -> bool:
        global curr_tool
        if index_y < self.config.top_bar_height:
            total_icon_width = sum(icon.shape[1] for icon in self.tools.values()) + self.config.spacing * (len(self.tools) - 1)
            tool_x_start = (self.config.window_width - total_icon_width) // 2
            tool_x = tool_x_start
            for tool_name, icon in self.tools.items():
                icon_h, icon_w = icon.shape[:2]
                tool_end_x = tool_x + icon_w
                if tool_x <= index_x <= tool_end_x and 0 <= index_y <= self.config.top_bar_height:
                    curr_tool = tool_name
                    return True
                tool_x += icon_w + self.config.spacing
        palette_start_x = self.config.window_width - self.config.palette_width
        if index_x > palette_start_x:
            y_offset = self.config.top_bar_height + self.config.spacing
            color_swatch_size = 40
            num_color_rows = (len(self.config.colors) + self.config.palette_columns - 1) // self.config.palette_columns
            for i in range(num_color_rows):
                for j in range(self.config.palette_columns):
                    color_index = i * self.config.palette_columns + j
                    if color_index < len(self.config.colors):
                        x_start = palette_start_x + j * (color_swatch_size + self.config.spacing)
                        y_start = y_offset + i * (color_swatch_size + self.config.spacing)
                        if (x_start <= index_x <= x_start + color_swatch_size and
                            y_start <= index_y <= y_start + color_swatch_size):
                            self.color_index = color_index
                            return True
            y_offset += num_color_rows * (color_swatch_size + self.config.spacing) + self.config.spacing
            num_brush_rows = (len(self.config.brush_sizes) + self.config.palette_columns - 1) // self.config.palette_columns
            for i in range(num_brush_rows):
                for j in range(self.config.palette_columns):
                    brush_index = i * self.config.palette_columns + j
                    if brush_index < len(self.config.brush_sizes):
                        x_start = palette_start_x + j * (color_swatch_size + self.config.spacing)
                        y_start = y_offset + i * (color_swatch_size + self.config.spacing)
                        if (x_start <= index_x <= x_start + color_swatch_size and
                            y_start <= index_y <= y_start + color_swatch_size):
                            self.brush_size_index = brush_index
                            return True
            y_offset += num_brush_rows * (color_swatch_size + self.config.spacing) + self.config.spacing
            num_eraser_rows = (len(self.config.eraser_sizes) + self.config.palette_columns - 1) // self.config.palette_columns
            for i in range(num_eraser_rows):
                for j in range(self.config.palette_columns):
                    eraser_index = i * self.config.palette_columns + j
                    if eraser_index < len(self.config.eraser_sizes):
                        x_start = palette_start_x + j * (color_swatch_size + self.config.spacing)
                        y_start = y_offset + i * (color_swatch_size + self.config.spacing)
                        if (x_start <= index_x <= x_start + color_swatch_size and
                            y_start <= index_y <= y_start + color_swatch_size):
                            self.eraser_size_index = eraser_index
                            return True
        return False

    def _handle_interaction(self, hand_landmarks: List, display_frame: np.ndarray) -> None:
        global curr_tool
        index_x = int(hand_landmarks[8].x * self.config.window_width)
        index_y = int(hand_landmarks[8].y * self.config.window_height)
        if self._ui_selection_unconditional(index_x, index_y):
            return
        thumb_x = hand_landmarks[4].x * self.config.window_width
        thumb_y = hand_landmarks[4].y * self.config.window_height
        middle_x = hand_landmarks[12].x * self.config.window_width
        middle_y = hand_landmarks[12].y * self.config.window_height

        def dist(ax, ay, bx, by):
            return math.hypot(ax - bx, ay - by)

        thumb_index_dist = dist(thumb_x, thumb_y, index_x, index_y)
        thumb_middle_dist = dist(thumb_x, thumb_y, middle_x, middle_y)
        index_middle_dist = dist(index_x, index_y, middle_x, middle_y)

        norm_factor = self.config.window_width
        pinch_threshold = self.config.multi_pinch_threshold / norm_factor
        separation_threshold = self.config.multi_separation_threshold / norm_factor

        is_thumb_index_close = (thumb_index_dist / norm_factor < pinch_threshold)
        is_thumb_middle_far = (thumb_middle_dist / norm_factor > separation_threshold)
        is_index_middle_far = (index_middle_dist / norm_factor > separation_threshold)
        pinch_detected = is_thumb_index_close and is_thumb_middle_far and is_index_middle_far

        depth_active = True
        if self.config.use_depth and self.state.latest_depth is not None:
            x_clamped = max(0, min(index_x, self.state.latest_depth.shape[1] - 1))
            y_clamped = max(0, min(index_y, self.state.latest_depth.shape[0] - 1))
            depth_value = self.state.latest_depth[y_clamped, x_clamped, 0] / 255.0
            if depth_value > self.config.depth_threshold:
                depth_active = False

        if pinch_detected and depth_active:
            if not self.pinching:
                self.pinching = True
                if curr_tool in ("rectangle", "circle", "line"):
                    self.shape_start_x, self.shape_start_y = index_x, index_y
                    self.drawing_shape = True
                if curr_tool == "draw":
                    self.current_stroke_points = [(index_x, index_y)]
            else:
                if curr_tool == "draw":
                    self.current_stroke_points.append((index_x, index_y))
                    # No preview drawn on display_frame here; it's drawn in run()
                elif curr_tool == "erase":
                    self._erase_continuous(index_x, index_y, display_frame)
                elif curr_tool in ("rectangle", "circle", "line"):
                    if self.drawing_shape:
                        if curr_tool == "rectangle":
                            cv2.rectangle(display_frame, (self.shape_start_x, self.shape_start_y), (index_x, index_y), (0, 255, 0), 2)
                        elif curr_tool == "circle":
                            radius = int(dist(self.shape_start_x, self.shape_start_y, index_x, index_y))
                            cv2.circle(display_frame, (self.shape_start_x, self.shape_start_y), radius, (0, 255, 0), 2)
                        elif curr_tool == "line":
                            cv2.line(display_frame, (self.shape_start_x, self.shape_start_y), (index_x, index_y), (0, 255, 0), 2)
        else:
            if self.pinching:
                if curr_tool == "draw" and self.current_stroke_points:
                    # Finalize stroke: compute smooth curve and draw it on the permanent canvas as a continuous line
                    curve = AirPainting.catmull_rom_spline(self.current_stroke_points, nPoints=10)
                    for i in range(len(curve) - 1):
                        cv2.line(self.paint_window, curve[i], curve[i + 1],
                                 self.config.colors[self.color_index],
                                 self.config.brush_sizes[self.brush_size_index])
                    self.current_stroke_points = []
                elif self.drawing_shape:
                    self._finalize_shape(index_x, index_y)
                if curr_tool in ("draw", "erase"):
                    self.last_draw_x = None
                    self.last_draw_y = None
            self.pinching = False
            self.drawing_shape = False

    def _draw_continuous_line(self, x: int, y: int, display_frame: np.ndarray):
        # Kept for backward compatibility (used by erase, etc.)
        global prevx, prevy
        color = self.config.colors[self.color_index]
        thickness = self.config.brush_sizes[self.brush_size_index]
        if self.last_draw_x is None or self.last_draw_y is None:
            self.last_draw_x, self.last_draw_y = x, y
            prevx, prevy = x, y
        else:
            distance = math.hypot(x - prevx, y - prevy)
            num_steps = max(1, int(distance / 2))
            for i in range(num_steps + 1):
                inter_x = int(prevx + (x - prevx) * (i / num_steps))
                inter_y = int(prevy + (y - prevy) * (i / num_steps))
                cv2.line(display_frame, (inter_x, inter_y), (inter_x, inter_y), color, thickness)
                cv2.line(self.paint_window, (inter_x, inter_y), (inter_x, inter_y), color, thickness)
            self.last_draw_x, self.last_draw_y, prevx, prevy = x, y, x, y

    def _erase_continuous(self, x: int, y: int, display_frame: np.ndarray):
        global prevx, prevy
        eraser_thickness = self.config.eraser_sizes[self.config.eraser_size_index]
        if self.last_draw_x is None or self.last_draw_y is None:
            self.last_draw_x, self.last_draw_y = x, y
            prevx, prevy = x, y
        else:
            cv2.circle(display_frame, (x, y), eraser_thickness, (255, 255, 255), -1)
            cv2.circle(self.paint_window, (x, y), eraser_thickness, (255, 255, 255), -1)
            self.last_draw_x, self.last_draw_y, prevx, prevy = x, y, x, y

    def _finalize_shape(self, end_x: int, end_y: int):
        global curr_tool
        color = self.config.colors[self.color_index]
        thickness = self.config.brush_sizes[self.brush_size_index]
        if curr_tool == "rectangle":
            cv2.rectangle(self.paint_window, (self.shape_start_x, self.shape_start_y), (end_x, end_y), color, thickness)
        elif curr_tool == "circle":
            radius = int(math.hypot(self.shape_start_x - end_x, self.shape_start_y - end_y))
            cv2.circle(self.paint_window, (self.shape_start_x, self.shape_start_y), radius, color, thickness)
        elif curr_tool == "line":
            cv2.line(self.paint_window, (self.shape_start_x, self.shape_start_y), (end_x, end_y), color, thickness)
        self.last_draw_x = None
        self.last_draw_y = None

    def calculate_fps(self):
        current_time = time.time()
        time_diff = current_time - self.last_time
        self.last_time = current_time
        self.frame_times.append(time_diff)
        return len(self.frame_times) / sum(self.frame_times) if self.frame_times else 0

    def adjust_brightness_contrast(self, frame: np.ndarray) -> np.ndarray:
        return cv2.convertScaleAbs(frame, alpha=self.contrast, beta=(self.brightness - 1) * 255)

    def run(self) -> None:
        global prevx, prevy
        prevx, prevy = 0, 0
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.window_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.window_height)
        if not cap.isOpened():
            raise RuntimeError("Could not access webcam")

        try:
            while True:
                ret, frm = cap.read()
                if not ret:
                    logging.warning("Empty frame received, skipping.")
                    continue
                frm = cv2.flip(frm, 1)
                frm = self.adjust_brightness_contrast(frm)
                display_frame = frm.copy()

                current_index_x = self.filtered_x
                current_index_y = self.filtered_y

                rgb = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
                op = self.hands.process(rgb)

                if op.multi_hand_landmarks:
                    for hand_lm in op.multi_hand_landmarks:
                        self.mp_drawing.draw_landmarks(display_frame, hand_lm, self.mp_hands.HAND_CONNECTIONS)
                        ix = int(hand_lm.landmark[8].x * self.config.window_width)
                        iy = int(hand_lm.landmark[8].y * self.config.window_height)
                        measurement = np.array([[np.float32(ix)], [np.float32(iy)]])
                        self.kalman.correct(measurement)
                        prediction = self.kalman.predict()
                        current_index_x = int(prediction[0])
                        current_index_y = int(prediction[1])
                        self._handle_interaction(hand_lm.landmark, display_frame)
                else:
                    self.pinching = False
                    self.last_draw_x = None
                    self.last_draw_y = None

                self.filtered_x = current_index_x
                self.filtered_y = current_index_y

                # Create a copy of the permanent canvas for preview
                display_paint_window = self.paint_window.copy()
                # If drawing with "draw" tool, overlay the continuous stroke preview
                if curr_tool == "draw" and self.pinching and len(self.current_stroke_points) > 1:
                    curve = AirPainting.catmull_rom_spline(self.current_stroke_points, nPoints=10)
                    for i in range(len(curve) - 1):
                        cv2.line(display_paint_window, curve[i], curve[i + 1],
                                 self.config.colors[self.color_index],
                                 self.config.brush_sizes[self.brush_size_index])
                self._draw_cursor(display_paint_window, self.filtered_x, self.filtered_y)
                self._draw_ui(display_frame)

                self.frame_count += 1
                if self.config.use_depth and self.frame_count % self.config.depth_frame_skip == 0:
                    with self.state.data_lock:
                        self.state.frame = frm.copy()
                    if self.state.new_depth_available.is_set():
                        with self.state.data_lock:
                            self.state.depth_display = self.state.latest_depth.copy()
                            self.state.new_depth_available.clear()

                if self.config.use_depth:
                    with self.state.data_lock:
                        if self.state.depth_display is not None:
                            depth_display = cv2.resize(self.state.depth_display,
                                                       (self.config.window_width, self.config.window_height))
                            cv2.imshow("Depth View", depth_display)

                cv2.imshow("Paint Window", display_paint_window)
                cv2.imshow("Webcam View", display_frame)

                key = cv2.waitKey(1) & 0xFF
                if key == 27:  # ESC key
                    break
                elif key != 255:
                    key_char = chr(key).lower()  # Case-insensitive hotkeys
                    if key_char == 'r':
                        self.paint_window = np.ones((self.config.window_height, self.config.window_width, 3), dtype=np.uint8) * 255
                    elif key_char == 'u':
                        new_config = Config.from_yaml('config.yaml')
                        self.config.depth_threshold = new_config.depth_threshold
                        self.config.multi_pinch_threshold = new_config.multi_pinch_threshold
                        self.config.multi_separation_threshold = new_config.multi_separation_threshold
                        print("Config updated from config.yaml!")
                    elif key_char == 'b':
                        self.brightness = min(self.brightness + 0.1, 3.0)
                    elif key_char == 'v':
                        self.brightness = max(self.brightness - 0.1, 0.1)
                    elif key_char == 'c':
                        self.contrast = min(self.contrast + 0.1, 3.0)
                    elif key_char == 'x':
                        self.contrast = max(self.contrast - 0.1, 0.1)
                    elif key_char == 's':
                        screenshot_filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
                        cv2.imwrite(screenshot_filename, display_paint_window)
                        print(f"Screenshot saved to {screenshot_filename}")
                    elif key_char == 'o':
                        recognized_text = perform_ocr(self.paint_window)
                        print("Recognized text:", recognized_text)

        finally:
            self.state.stop_event.set()
            if self.config.use_depth:
                self.depth_thread.join()
            cap.release()
            cv2.destroyAllWindows()

def main() -> None:
    config = Config.from_yaml('config.yaml')
    app = AirPainting(config)
    app.run()

if __name__ == "__main__":
    main()

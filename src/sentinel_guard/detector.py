import cv2
import numpy as np

PROC_WIDTH = 320
MAX_FG_FRACTION = 0.6  # mudança global (luz/auto-exposição): não é movimento


class MotionDetector:
    def __init__(self, sensitivity=5, min_area_pct=0.5, min_consecutive_frames=3,
                 history=500, warmup_frames=50):
        self.min_area_pct = min_area_pct
        self.min_consecutive = min_consecutive_frames
        self.warmup_frames = warmup_frames
        var_threshold = 40 - 3 * sensitivity  # sens 1 -> 37, 10 -> 10
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=history, varThreshold=var_threshold, detectShadows=False)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._frames = 0
        self._streak = 0
        self.last_area_pct = 0.0

    def reset(self) -> None:
        self._frames = 0
        self._streak = 0

    def process(self, frame: np.ndarray) -> bool:
        """Retorna True quando há movimento sustentado por N frames consecutivos."""
        h, w = frame.shape[:2]
        if w > PROC_WIDTH:
            frame = cv2.resize(frame, (PROC_WIDTH, int(h * PROC_WIDTH / w)), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (7, 7), 0)
        mask = self._bg.apply(gray)
        self._frames += 1
        if self._frames <= self.warmup_frames:
            return False
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.dilate(mask, self._kernel, iterations=2)
        total = mask.shape[0] * mask.shape[1]
        area_pct = 100.0 * cv2.countNonZero(mask) / total
        self.last_area_pct = area_pct
        if area_pct > MAX_FG_FRACTION * 100:
            self._streak = 0
            return False
        if area_pct >= self.min_area_pct:
            self._streak += 1
        else:
            self._streak = 0
        return self._streak >= self.min_consecutive

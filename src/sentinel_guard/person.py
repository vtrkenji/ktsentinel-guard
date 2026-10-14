"""Filtro de pessoa (MobileNet-SSD, OpenCV DNN). Só é chamado quando o MOG2 já detectou movimento."""
import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("sentinel_guard.person")

PERSON_CLASS = 15  # VOC: 15 = person (gatos, cães, carros etc. são outras classes)
MODEL_FILES = ("deploy.prototxt", "mobilenet_iter_73000.caffemodel")


def model_available(model_dir: Path) -> bool:
    return bool(model_dir) and all((Path(model_dir) / f).is_file() for f in MODEL_FILES)


def load_person_detector(model_dir: Path | None, confidence: float = 0.5):
    """Carrega o modelo. Devolve (detector, 'ok') ou (None, 'missing'|'error')."""
    if not model_available(model_dir):
        return None, "missing"
    try:
        return PersonDetector(model_dir, confidence), "ok"
    except Exception as e:
        log.error("falha ao carregar modelo de pessoa: %s", e)
        return None, "error"


class PersonDetector:
    def __init__(self, model_dir: Path, confidence: float = 0.5):
        d = Path(model_dir)
        self.confidence = confidence
        self._net = cv2.dnn.readNetFromCaffe(str(d / MODEL_FILES[0]), str(d / MODEL_FILES[1]))

    def detect_all(self, frame: np.ndarray) -> list:
        """Todas as pessoas acima da confiança, como (x, y, w, h) normalizados."""
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
        self._net.setInput(blob)
        boxes = []
        for det in self._net.forward()[0, 0]:
            if int(det[1]) == PERSON_CLASS and det[2] >= self.confidence:
                x1, y1, x2, y2 = (float(np.clip(v, 0, 1)) for v in det[3:7])
                boxes.append((x1, y1, x2 - x1, y2 - y1))
        return boxes

    def detect(self, frame: np.ndarray):
        """Devolve (x, y, w, h) normalizado da pessoa mais confiável, ou None."""
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
        self._net.setInput(blob)
        out = self._net.forward()[0, 0]
        best = None
        for det in out:
            if int(det[1]) == PERSON_CLASS and det[2] >= self.confidence and (best is None or det[2] > best[2]):
                best = det
        if best is None:
            return None
        x1, y1, x2, y2 = (float(np.clip(v, 0, 1)) for v in best[3:7])
        return (x1, y1, x2 - x1, y2 - y1)

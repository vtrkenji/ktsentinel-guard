import os

# Antes de qualquer import cv2 no pacote.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;15000000")

__version__ = "0.1.0"

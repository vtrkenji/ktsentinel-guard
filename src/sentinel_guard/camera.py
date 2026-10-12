import re
from dataclasses import dataclass


def redact_url(text: str) -> str:
    """Remove credenciais de URLs RTSP (user:pass@ e user=/password= na query)."""
    text = re.sub(r"(rtsp://)[^/@\s]+@", r"\1***@", text)
    return re.sub(r"(?i)\b(user|username|password|pwd)=[^&\s]*", r"\1=***", text)


@dataclass
class Camera:
    id: int
    name: str
    rtsp_url: str
    rtsp_url_secondary: str | None = None
    is_active: bool = True
    sensitivity: int | None = None
    min_area_pct: float | None = None
    min_consecutive_frames: int | None = None

    @property
    def detection_url(self) -> str:
        return self.rtsp_url_secondary or self.rtsp_url

    def to_json(self) -> dict:
        """Mesmo formato (camelCase) do modelo Camera do app, sem credenciais."""
        return {
            "id": self.id,
            "name": self.name,
            "rtspUrl": redact_url(self.rtsp_url),
            "rtspUrlSecondary": redact_url(self.rtsp_url_secondary) if self.rtsp_url_secondary else None,
            "isActive": self.is_active,
        }

import re
from dataclasses import dataclass, field


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
    ignore_zones: list = field(default_factory=list)   # polígonos [[x,y],...] normalizados 0..1
    include_zones: list = field(default_factory=list)  # se houver, só estas áreas contam
    # [{"name","line":[[x,y],[x,y]],"direction":"both|ab|ba"}]; "ab" = da esquerda para a direita de a→b
    tripwires: list = field(default_factory=list)
    loiter_seconds: float | None = None     # pessoa permanecendo na cena (ou em loiter_zone) por este tempo
    loiter_zone: list = field(default_factory=list)
    behavior_only: bool | None = None       # None = usa rules.behavior_only; False = avisa pessoa na zona de scan

    @property
    def analytics(self) -> bool:
        return bool(self.tripwires) or self.loiter_seconds is not None

    def uses_behavior_only(self, default: bool) -> bool:
        return default if self.behavior_only is None else self.behavior_only

    @property
    def detection_url(self) -> str:
        return self.rtsp_url_secondary or self.rtsp_url

    def apply_geometry(self, g: dict) -> None:
        self.ignore_zones = g.get("ignore_zones", self.ignore_zones)
        self.include_zones = g.get("include_zones", self.include_zones)
        self.tripwires = g.get("tripwires", self.tripwires)
        if "loiter_seconds" in g:
            self.loiter_seconds = g["loiter_seconds"]
        if "loiter_zone" in g:
            self.loiter_zone = g["loiter_zone"]
        if "behavior_only" in g:
            self.behavior_only = g["behavior_only"]

    def to_json(self) -> dict:
        """Mesmo formato (camelCase) do modelo Camera do app, sem credenciais."""
        return {
            "id": self.id,
            "name": self.name,
            "rtspUrl": redact_url(self.rtsp_url),
            "rtspUrlSecondary": redact_url(self.rtsp_url_secondary) if self.rtsp_url_secondary else None,
            "isActive": self.is_active,
            "ignoreZones": self.ignore_zones,
            "includeZones": self.include_zones,
            "tripwires": self.tripwires,
            "loiterSeconds": self.loiter_seconds,
            "loiterZone": self.loiter_zone,
            "behaviorOnly": self.behavior_only,
        }

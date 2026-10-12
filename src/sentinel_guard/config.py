import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .camera import Camera
from .paths import default_data_dir
from .topics import validate_topic


class ConfigError(Exception):
    pass


@dataclass
class FcmConfig:
    project_id: str = ""
    service_account_file: Path | None = None
    topic: str = ""
    title: str = "kTsentinel - Casa"

    @property
    def enabled(self) -> bool:
        return bool(self.project_id and self.service_account_file and self.topic)


@dataclass
class NtfyConfig:
    enabled: bool = False
    url: str = ""


@dataclass
class DetectionConfig:
    sensitivity: int = 5
    min_area_pct: float = 0.5
    min_consecutive_frames: int = 3
    fps: float = 5.0
    history: int = 500
    warmup_seconds: float = 10.0


@dataclass
class Config:
    armed: bool = True
    data_dir: Path = field(default_factory=default_data_dir)
    cooldown_seconds: float = 60.0
    fcm: FcmConfig = field(default_factory=FcmConfig)
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    cameras: list[Camera] = field(default_factory=list)
    path: Path | None = None


def check_private(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigError(f"{path} tem permissão {mode:04o}; execute: chmod 600 {path}")


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def load_config(path: Path, require_private: bool = True) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config não encontrado: {path}")
    if require_private:
        check_private(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"TOML inválido: {e}") from e

    g = raw.get("general", {})
    cfg = Config(path=path)
    cfg.armed = bool(g.get("armed", True))
    if "data_dir" in g:
        cfg.data_dir = _expand(g["data_dir"])
    cfg.cooldown_seconds = float(g.get("cooldown_seconds", 60))

    f = raw.get("fcm", {})
    cfg.fcm = FcmConfig(
        project_id=f.get("project_id", ""),
        service_account_file=_expand(f["service_account_file"]) if f.get("service_account_file") else None,
        topic=f.get("topic", ""),
        title=f.get("title", "kTsentinel - Casa"),
    )
    if cfg.fcm.topic:
        err = validate_topic(cfg.fcm.topic)
        if err:
            raise ConfigError(f"[fcm].topic: {err}")

    n = raw.get("ntfy", {})
    cfg.ntfy = NtfyConfig(enabled=bool(n.get("enabled", False)), url=n.get("url", ""))
    if cfg.ntfy.enabled and not cfg.ntfy.url:
        raise ConfigError("[ntfy] habilitado sem url")

    d = raw.get("detection", {})
    dd = DetectionConfig()
    for k in vars(dd):
        if k in d:
            setattr(dd, k, type(getattr(dd, k))(d[k]))
    if not 1 <= dd.sensitivity <= 10:
        raise ConfigError("[detection].sensitivity deve estar entre 1 e 10")
    cfg.detection = dd

    seen = set()
    for c in raw.get("cameras", []):
        for req in ("id", "name", "rtsp_url"):
            if req not in c:
                raise ConfigError(f"Câmera sem campo '{req}': {c}")
        if c["id"] in seen:
            raise ConfigError(f"id de câmera duplicado: {c['id']}")
        seen.add(c["id"])
        cfg.cameras.append(Camera(
            id=int(c["id"]), name=c["name"], rtsp_url=c["rtsp_url"],
            rtsp_url_secondary=c.get("rtsp_url_secondary") or None,
            is_active=bool(c.get("is_active", True)),
            sensitivity=c.get("sensitivity"),
            min_area_pct=c.get("min_area_pct"),
            min_consecutive_frames=c.get("min_consecutive_frames"),
        ))
    return cfg

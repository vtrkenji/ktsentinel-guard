import os
import stat
from datetime import datetime, time
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
class Rules:
    notify: bool = True               # False = só registra eventos (modo observação)
    cooldown_seconds: float = 60.0    # intervalo mínimo entre notificações da mesma câmera
    min_event_seconds: float = 2.0    # movimento precisa durar isso para notificar
    event_gap_seconds: float = 5.0    # sem movimento por este tempo encerra o evento
    max_notifications_per_hour: int = 6


@dataclass
class ScheduleWindow:
    days: tuple[int, ...]  # 0=segunda ... 6=domingo
    start: time
    end: time              # end < start cruza a meia-noite

    def active(self, now: datetime) -> bool:
        t, d = now.time(), now.weekday()
        if self.start <= self.end:
            return d in self.days and self.start <= t < self.end
        if t >= self.start:
            return d in self.days
        return (d - 1) % 7 in self.days and t < self.end


@dataclass
class Config:
    armed: bool = True
    data_dir: Path = field(default_factory=default_data_dir)
    rules: Rules = field(default_factory=Rules)
    schedule: list[ScheduleWindow] = field(default_factory=list)
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


def _zones(c: dict, key: str) -> list:
    zones = c.get(key, [])
    for z in zones:
        if len(z) < 3 or any(len(p) != 2 or not all(0 <= v <= 1 for v in p) for p in z):
            raise ConfigError(f"{key} da câmera {c.get('id')}: polígonos com >=3 pontos [x,y] entre 0 e 1")
    return zones


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
    r = Rules()
    for k in vars(r):
        if k in g:
            setattr(r, k, type(getattr(r, k))(g[k]))
    cfg.rules = r
    for w in raw.get("schedule", []):
        try:
            h1, m1 = map(int, w["start"].split(":"))
            h2, m2 = map(int, w["end"].split(":"))
            days = tuple(w.get("days", range(7)))
            if not all(0 <= d <= 6 for d in days):
                raise ValueError("dias devem estar entre 0 (segunda) e 6 (domingo)")
            cfg.schedule.append(ScheduleWindow(days, time(h1, m1), time(h2, m2)))
        except (KeyError, ValueError) as e:
            raise ConfigError(f"[[schedule]] inválido: {e}") from e

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
            ignore_zones=_zones(c, "ignore_zones"),
            include_zones=_zones(c, "include_zones"),
        ))
    return cfg

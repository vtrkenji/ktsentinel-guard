import json
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
    fps: float = 2.0                  # alarme, não tracking: 2 Hz no vazio basta
    history: int = 500
    warmup_seconds: float = 10.0
    person_filter: bool = False       # confirma pessoa com modelo leve quando há movimento
    person_confidence: float = 0.5
    person_check_seconds: float = 1.0
    person_min_hits: int = 2          # detecções de pessoa seguidas para confirmar (evita falso positivo de 1 frame)
    person_model_dir: Path | None = None


@dataclass
class Rules:
    notify: bool = True               # False = só registra eventos (modo observação)
    cooldown_seconds: float = 60.0    # intervalo mínimo entre notificações da mesma câmera
    min_event_seconds: float = 2.0    # movimento precisa durar isso para notificar
    event_gap_seconds: float = 5.0    # sem movimento por este tempo encerra o evento
    max_notifications_per_hour: int = 6
    require_person: bool = False      # só notifica se uma pessoa foi confirmada (exige detection.person_filter)
    behavior_only: bool = False       # movimento comum só registra; notifica apenas permanência (loiter) e cruzamento (tripwire)
    keep_days: int = 14               # apaga eventos/fotos mais antigos que isso (0 = nunca)
    max_snapshots_mb: int = 500       # teto de espaço das fotos; apaga as mais antigas primeiro (0 = sem teto)


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


def _hhmm(value) -> time:
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"horário inválido: {value}")
    return time(int(parts[0]), int(parts[1]))


def parse_window(w: dict) -> ScheduleWindow:
    start, end = _hhmm(w["start"]), _hhmm(w["end"])
    days = tuple(sorted({int(d) for d in w.get("days", range(7))}))
    if not days or not all(0 <= d <= 6 for d in days):
        raise ValueError("dias devem estar entre 0 (segunda) e 6 (domingo)")
    return ScheduleWindow(days, start, end)


# Ajustes editáveis pelo app: (seção, atributo, tipo, mínimo, máximo)
SETTINGS = {
    "sensitivity": ("detection", "sensitivity", int, 1, 10),
    "personMinHits": ("detection", "person_min_hits", int, 1, 5),
    "cooldownSeconds": ("rules", "cooldown_seconds", float, 10, 600),
    "maxNotificationsPerHour": ("rules", "max_notifications_per_hour", int, 1, 60),
    "keepDays": ("rules", "keep_days", int, 1, 90),
    "maxSnapshotsMb": ("rules", "max_snapshots_mb", int, 50, 5000),
    "notify": ("rules", "notify", bool, None, None),
    "requirePerson": ("rules", "require_person", bool, None, None),
    "behaviorOnly": ("rules", "behavior_only", bool, None, None),
}


def validate_settings(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if k not in SETTINGS:
            raise ValueError(f"ajuste desconhecido: {k}")
        _, _, typ, lo, hi = SETTINGS[k]
        if typ is bool:
            if not isinstance(v, bool):
                raise ValueError(f"{k} deve ser true/false")
            out[k] = v
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{k} deve ser numérico")
        if not lo <= v <= hi:
            raise ValueError(f"{k} deve estar entre {lo} e {hi}")
        out[k] = typ(v)
    return out


def apply_settings(cfg, values: dict) -> None:
    for k, v in values.items():
        sec, attr, *_ = SETTINGS[k]
        setattr(getattr(cfg, "detection" if sec == "detection" else "rules"), attr, v)


def current_settings(cfg) -> dict:
    return {k: getattr(getattr(cfg, "detection" if sec == "detection" else "rules"), attr)
            for k, (sec, attr, *_) in SETTINGS.items()}


@dataclass
class ApiConfig:
    enabled: bool = False
    bind: str = "127.0.0.1"   # use o IP do Tailscale/LAN para acesso remoto; nunca exponha na internet
    port: int = 8765
    token_file: Path | None = None


@dataclass
class Config:
    armed: bool = True
    data_dir: Path = field(default_factory=default_data_dir)
    rules: Rules = field(default_factory=Rules)
    api: ApiConfig = field(default_factory=ApiConfig)
    schedule: list[ScheduleWindow] = field(default_factory=list)
    fcm: FcmConfig = field(default_factory=FcmConfig)
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    cameras: list[Camera] = field(default_factory=list)
    path: Path | None = None


def check_private(path: Path) -> None:
    # No Windows o modo Unix não representa a ACL do perfil; o arquivo fica na pasta do usuário.
    if os.name == "nt":
        return
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


def parse_geometry(data: dict, cam_id=0) -> dict:
    """Valida zonas/tripwire/loiter (coordenadas 0..1). Campos ausentes viram vazio."""
    if not isinstance(data, dict):
        raise ConfigError("geometria deve ser um objeto")
    c = {"id": cam_id, "ignore_zones": data.get("ignore_zones") or [],
         "include_zones": data.get("include_zones") or [], "tripwires": data.get("tripwires") or []}
    loiter = data.get("loiter_seconds")
    out = {
        "ignore_zones": _zones(c, "ignore_zones"),
        "include_zones": _zones(c, "include_zones"),
        "tripwires": _tripwires(c),
        "loiter_seconds": float(loiter) if loiter else None,
        "loiter_zone": [],
    }
    if out["loiter_seconds"] is not None and out["loiter_seconds"] < 3:
        raise ConfigError(f"loiter_seconds da câmera {cam_id} deve ser >= 3")
    zone = data.get("loiter_zone") or []
    if zone:
        out["loiter_zone"] = _zones({"id": cam_id, "z": [zone]}, "z")[0]
    if "behavior_only" in data:
        v = data["behavior_only"]
        if v is None:
            out["behavior_only"] = None
        elif isinstance(v, bool):
            out["behavior_only"] = v
        else:
            raise ConfigError(f"behavior_only da câmera {cam_id} deve ser true/false")
    return out


def apply_geometry_overlay(cfg: Config) -> None:
    """geometry.json (desenhado na página do notebook) sobrepõe zonas do config.toml."""
    path = cfg.data_dir / "geometry.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return
    except Exception as e:
        raise ConfigError(f"geometry.json inválido: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("geometry.json deve ser um objeto id -> geometria")
    for key, geo in data.items():
        cam = next((c for c in cfg.cameras if str(c.id) == str(key)), None)
        if cam is None:
            continue
        cam.apply_geometry(parse_geometry(geo if isinstance(geo, dict) else {}, cam.id))


def _tripwires(c: dict) -> list:
    out = []
    for w in c.get("tripwires", []):
        line = w.get("line", [])
        if len(line) != 2 or any(len(p) != 2 or not all(0 <= v <= 1 for v in p) for p in line):
            raise ConfigError(f"tripwire da câmera {c.get('id')}: 'line' precisa de 2 pontos [x,y] entre 0 e 1")
        d = w.get("direction", "both")
        if d not in ("both", "ab", "ba"):
            raise ConfigError(f"tripwire da câmera {c.get('id')}: direction deve ser both, ab ou ba")
        out.append({"name": w.get("name", "linha"), "line": line, "direction": d})
    return out


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
    a = raw.get("api", {})
    cfg.api = ApiConfig(
        enabled=bool(a.get("enabled", False)), bind=a.get("bind", "127.0.0.1"), port=int(a.get("port", 8765)),
        token_file=_expand(a["token_file"]) if a.get("token_file") else _expand("~/.config/ktsentinel-guard/api-token"))
    for w in raw.get("schedule", []):
        try:
            cfg.schedule.append(parse_window(w))
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
        if k in d and k != "person_model_dir":
            setattr(dd, k, type(getattr(dd, k))(d[k]))
    dd.person_model_dir = _expand(d["person_model_dir"]) if d.get("person_model_dir") else cfg.data_dir / "models"
    if cfg.rules.require_person and not dd.person_filter:
        raise ConfigError("require_person exige [detection].person_filter = true")
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
            tripwires=_tripwires(c),
            loiter_seconds=float(c["loiter_seconds"]) if c.get("loiter_seconds") else None,
            loiter_zone=(_zones({"id": c["id"], "z": [c["loiter_zone"]]}, "z")[0] if c.get("loiter_zone") else []),
            behavior_only=bool(c["behavior_only"]) if "behavior_only" in c else None,
        ))
    if any(c.analytics for c in cfg.cameras) and not cfg.detection.person_filter:
        raise ConfigError("tripwire/loiter_seconds exigem [detection].person_filter = true")
    return cfg

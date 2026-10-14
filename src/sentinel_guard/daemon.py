import json
import logging
import signal
from datetime import datetime
import threading

from .alerts import AlertManager
from .config import (
    apply_geometry_overlay, apply_settings, current_settings, parse_geometry,
    parse_window, validate_settings,
)
from .capture import CameraWorker
from .config import Config, load_config
from .fcm import FcmSender, NtfySender, with_retries
from .paths import socket_path
from .person import model_available
from .server import SocketServer

log = logging.getLogger("sentinel_guard.daemon")


class Daemon:
    def __init__(self, config_path):
        self.config_path = config_path
        self.cfg: Config | None = None
        self.workers: list[CameraWorker] = []
        self.alerts: AlertManager | None = None
        self.armed = True
        self.schedule = []
        self.schedule_enabled = True
        self._lock = threading.RLock()
        self._stop = threading.Event()

    def is_armed(self) -> bool:
        """Armado manualmente e, se houver agenda, dentro de uma janela."""
        if not self.armed:
            return False
        sched = self.schedule
        return not sched or any(w.active(datetime.now()) for w in sched)

    def _settings_file(self):
        return self.cfg.data_dir / "settings.json"

    def _schedule_file(self):
        return self.cfg.data_dir / "schedule.json"

    def _geometry_file(self):
        return self.cfg.data_dir / "geometry.json"

    def _person_model_ok(self) -> bool:
        return model_available(self.cfg.detection.person_model_dir)

    def _enforce_fail_closed(self) -> None:
        """Sem modelo, não há como confirmar pessoa: nenhum push de movimento/analytics."""
        if self.cfg.detection.person_filter and not self._person_model_ok():
            self.cfg.rules.require_person = True
            log.error("modelo de pessoa ausente em %s; fail-closed (rode scripts/download_models.sh)",
                      self.cfg.detection.person_model_dir)

    def _load_schedule(self):
        """Agenda salva pelo app (schedule.json) tem prioridade sobre [[schedule]] do config."""
        try:
            data = json.loads(self._schedule_file().read_text())
        except Exception:
            self.schedule_enabled = True
            return list(self.cfg.schedule)
        self.schedule_enabled = bool(data.get("enabled", True))
        wins = [parse_window(w) for w in data.get("windows", [])]
        return wins if self.schedule_enabled else []


    # --- ciclo de vida ---
    def _state_file(self):
        return self.cfg.data_dir / "state.json"

    def _load_armed(self) -> bool:
        try:
            return bool(json.loads(self._state_file().read_text())["armed"])
        except Exception:
            return self.cfg.armed

    def _save_armed(self) -> None:
        self._state_file().write_text(json.dumps({"armed": self.armed}))

    def _notifiers(self):
        out = []
        if self.cfg.fcm.enabled:
            sender = FcmSender(self.cfg.fcm)
            out.append(lambda name, body: with_retries(lambda: sender.send(body)))
        if self.cfg.ntfy.enabled:
            ntfy = NtfySender(self.cfg.ntfy.url)
            out.append(lambda name, body: with_retries(lambda: ntfy.send(body, self.cfg.fcm.title)))
        if not out:
            log.warning("nenhum canal de notificação configurado")
        return out

    def start_workers(self) -> None:
        with self._lock:
            self.cfg = load_config(self.config_path)
            self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
            try:
                apply_settings(self.cfg, validate_settings(json.loads(self._settings_file().read_text())))
            except FileNotFoundError:
                pass
            except Exception as e:
                log.error("settings.json ignorado: %s", e)
            try:
                apply_geometry_overlay(self.cfg)
            except Exception as e:
                log.error("geometry.json ignorado: %s", e)
            self._enforce_fail_closed()
            self.armed = self._load_armed()
            self.schedule = self._load_schedule()
            self._notifier_list = self._notifiers()
            self.alerts = AlertManager(self.cfg.data_dir, self.cfg.rules, self._notifier_list)
            self.workers = [
                CameraWorker(c, self.cfg.detection, self.alerts, self.is_armed)
                for c in self.cfg.cameras if c.is_active]
            for w in self.workers:
                w.start()
            log.info("%d câmera(s) ativa(s); armado=%s", len(self.workers), self.armed)

    def stop_workers(self) -> None:
        with self._lock:
            for w in self.workers:
                w.stop()
            for w in self.workers:
                w.join(timeout=10)
            self.workers = []
            if self.alerts:
                self.alerts.close_all()

    def run(self) -> int:
        self.start_workers()
        server = SocketServer(socket_path(), self)
        server.start()
        api = None
        if self.cfg.api.enabled:
            from .api import ApiServer
            api = ApiServer(self.cfg.api, self)
            api.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self._stop.set())
        signal.signal(signal.SIGHUP, lambda *_: self._safe_reload())
        log.info("daemon pronto em %s", server.path)
        self._stop.wait()
        server.close()
        if api:
            api.close()
        self.stop_workers()
        return 0

    def _safe_reload(self):
        try:
            self.cmd_reload_config()
        except Exception as e:
            log.error("reload falhou: %s", e)

    # --- comandos do socket ---
    def cmd_status(self):
        with self._lock:
            r, d = self.cfg.rules, self.cfg.detection
            cams = [w.status() for w in self.workers]
            if d.person_filter:
                person_model = "ok" if self._person_model_ok() else "missing"
                if any(c.get("personModel") == "error" for c in cams):
                    person_model = "error"
            else:
                person_model = "off"
            warnings = []
            if person_model in ("missing", "error"):
                warnings.append("modelo de pessoa ausente; nenhum aviso até instalar (download_models.sh)")
            if r.behavior_only and not any(c.analytics for c in self.cfg.cameras if c.is_active):
                warnings.append("só permanência/entrada ligado, mas nenhuma câmera tem tripwire ou loiter")
            if self.armed and self.is_armed() and not any(c.get("state") == "online" for c in cams):
                warnings.append("armado, porém nenhuma câmera online")
            if r.notify and not self.cfg.fcm.enabled and not self.cfg.ntfy.enabled:
                warnings.append("avisos ligados sem FCM/ntfy configurado")
            return {
                "armed": self.armed, "scheduleActive": self.is_armed(),
                "notify": r.notify, "requirePerson": r.require_person, "behaviorOnly": r.behavior_only,
                "personFilter": d.person_filter, "personModel": person_model,
                "cameras": cams, "warnings": warnings,
                "fcmConfigured": self.cfg.fcm.enabled, "ntfyConfigured": self.cfg.ntfy.enabled,
            }

    def cmd_get_schedule(self):
        with self._lock:
            if self.schedule_enabled:
                wins = self.schedule
            else:
                try:
                    wins = [parse_window(w) for w in json.loads(self._schedule_file().read_text())["windows"]]
                except Exception:
                    wins = []
            return {"enabled": self.schedule_enabled and bool(self.schedule),
                    "windows": [{"days": list(w.days), "start": w.start.strftime("%H:%M"),
                                 "end": w.end.strftime("%H:%M")} for w in wins]}

    def cmd_set_schedule(self, windows, enabled=True):
        """windows: [{"days":[0..6],"start":"HH:MM","end":"HH:MM"}]; 0=segunda."""
        if not isinstance(windows, list) or len(windows) > 20:
            raise ValueError("windows deve ser uma lista com até 20 itens")
        parsed = [parse_window(w) for w in windows]
        with self._lock:
            data = [{"days": list(w.days), "start": w.start.strftime("%H:%M"), "end": w.end.strftime("%H:%M")}
                    for w in parsed]
            self._schedule_file().write_text(json.dumps({"enabled": bool(enabled), "windows": data}))
            self.schedule_enabled = bool(enabled)
            self.schedule = parsed if enabled else []
        return self.cmd_get_schedule()

    def cmd_get_settings(self):
        with self._lock:
            return current_settings(self.cfg)

    def cmd_set_settings(self, values):
        """Salva ajustes do app em settings.json (sobrepõem o config.toml)."""
        if not isinstance(values, dict):
            raise ValueError("values deve ser um objeto")
        new = validate_settings(values)
        with self._lock:
            before = current_settings(self.cfg)
            try:
                saved = json.loads(self._settings_file().read_text())
            except Exception:
                saved = {}
            saved.update(new)
            self._settings_file().write_text(json.dumps(saved))
            apply_settings(self.cfg, new)
            self._enforce_fail_closed()
            # Rules é compartilhado com o AlertManager; só detecção exige reiniciar as câmeras.
            if self.alerts:
                self.alerts.rules = self.cfg.rules
            changed_detection = any(before[k] != current_settings(self.cfg)[k]
                                    for k in new if k in ("sensitivity", "personMinHits"))
        if changed_detection:
            self.cmd_reload_config()
        return self.cmd_get_settings()

    def cmd_arm(self):
        self.armed = True
        self._save_armed()
        return {"armed": True}

    def cmd_disarm(self):
        self.armed = False
        self._save_armed()
        return {"armed": False}

    def cmd_list_cameras(self):
        return [c.to_json() for c in self.cfg.cameras]

    def cmd_preview(self, camera_id):
        cid = int(camera_id)
        with self._lock:
            w = next((x for x in self.workers if x.camera.id == cid), None)
        if w is None:
            raise ValueError(f"câmera {cid} não está ativa")
        jpeg = w.preview_jpeg()
        if not jpeg:
            raise RuntimeError("sem frame; câmera offline ou ainda conectando")
        return jpeg

    def cmd_set_geometry(self, camera_id, values):
        cid = int(camera_id)
        geo = parse_geometry(values, cid)
        with self._lock:
            cam = next((c for c in self.cfg.cameras if c.id == cid), None)
            if cam is None:
                raise ValueError(f"câmera {cid} desconhecida")
            if (geo["tripwires"] or geo["loiter_seconds"]) and not self.cfg.detection.person_filter:
                raise ValueError("tripwire/loiter exigem [detection].person_filter = true")
            try:
                saved = json.loads(self._geometry_file().read_text())
            except Exception:
                saved = {}
            saved[str(cid)] = {
                "ignore_zones": geo["ignore_zones"], "include_zones": geo["include_zones"],
                "tripwires": geo["tripwires"], "loiter_seconds": geo["loiter_seconds"],
                "loiter_zone": geo["loiter_zone"],
            }
            path = self._geometry_file()
            path.write_text(json.dumps(saved))
            path.chmod(0o600)
            cam.apply_geometry(geo)
        self._restart_worker(cid)
        return cam.to_json()

    def _restart_worker(self, cam_id: int) -> None:
        """Reinicia só a câmera que já estava rodando; sem worker, o próximo start_workers lê geometry.json."""
        with self._lock:
            old = [w for w in self.workers if w.camera.id == cam_id]
            if not old:
                return
            self.workers = [w for w in self.workers if w.camera.id != cam_id]
            cam = next(c for c in self.cfg.cameras if c.id == cam_id)
        for w in old:
            w.stop()
        for w in old:
            w.join(timeout=10)
        if not cam.is_active or self.alerts is None:
            return
        w = CameraWorker(cam, self.cfg.detection, self.alerts, self.is_armed)
        w.start()
        with self._lock:
            self.workers.append(w)

    def cmd_list_events(self, limit=50, since=None, only_notified=False):
        return self.alerts.list_events(int(limit), since, bool(only_notified))

    cmd_list_alerts = cmd_list_events

    def cmd_test_notification(self):
        if not self._notifier_list:
            raise RuntimeError("nenhum canal de notificação configurado")
        for n in self._notifier_list:
            n("teste", "Teste do kTsentinel Guard")
        return {"sent": len(self._notifier_list)}

    def cmd_reload_config(self):
        self.stop_workers()
        self.start_workers()
        return {"cameras": len(self.workers)}

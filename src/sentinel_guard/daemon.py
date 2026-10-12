import json
import logging
import signal
import threading

from .alerts import AlertManager
from .capture import CameraWorker
from .config import Config, load_config
from .fcm import FcmSender, NtfySender, with_retries
from .paths import socket_path
from .server import SocketServer

log = logging.getLogger("sentinel_guard.daemon")


class Daemon:
    def __init__(self, config_path):
        self.config_path = config_path
        self.cfg: Config | None = None
        self.workers: list[CameraWorker] = []
        self.alerts: AlertManager | None = None
        self.armed = True
        self._lock = threading.RLock()
        self._stop = threading.Event()

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
            self.armed = self._load_armed()
            self._notifier_list = self._notifiers()
            self.alerts = AlertManager(self.cfg.data_dir, self.cfg.cooldown_seconds, self._notifier_list)
            self.workers = [
                CameraWorker(c, self.cfg.detection, self.alerts, lambda: self.armed)
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

    def run(self) -> int:
        self.start_workers()
        server = SocketServer(socket_path(), self)
        server.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self._stop.set())
        signal.signal(signal.SIGHUP, lambda *_: self._safe_reload())
        log.info("daemon pronto em %s", server.path)
        self._stop.wait()
        server.close()
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
            return {"armed": self.armed, "cameras": [w.status() for w in self.workers],
                    "fcmConfigured": self.cfg.fcm.enabled, "ntfyConfigured": self.cfg.ntfy.enabled}

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

    def cmd_list_alerts(self, limit=50):
        return self.alerts.list_alerts(int(limit))

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

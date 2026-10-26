"""Indicador local do alarme. Lê o socket do daemon; não fala com a câmera."""
import argparse
import json
import os
import socket
import sys
import tomllib
from datetime import datetime
from pathlib import Path


def socket_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        runtime = Path(base) / "ktsentinel-guard"
    else:
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/ktsentinel-guard-{os.getuid()}")
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime / "ktsentinel-guard.sock"


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ktsentinel-guard" / "config.toml"


def ask(cmd: str, timeout: float = 2.0) -> dict:
    path = socket_path()
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall((json.dumps({"id": 1, "cmd": cmd, "args": {}}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    resp = json.loads(buf)
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error") or "comando falhou")
    return resp.get("result") or {}


def api_enabled(config_path: Path) -> bool | None:
    try:
        data = tomllib.loads(config_path.read_text())
    except OSError:
        return None
    except tomllib.TOMLDecodeError:
        return None
    return bool((data.get("api") or {}).get("enabled", False))


def describe(status: dict | None, schedule: dict | None = None, api_on: bool | None = None) -> dict:
    """Resumo estável para o indicador. status=None quando o daemon não responde."""
    if not status:
        return {
            "tone": "off", "headline": "PARADO", "source": "daemon sem resposta",
            "cameras": "", "warnings": "", "api": "",
        }
    watching = bool(status.get("scheduleActive"))
    armed = bool(status.get("armed"))
    if watching:
        tone, headline = "on", "ON"
    elif armed:
        tone, headline = "pause", "PAUSA"
    else:
        tone, headline = "off", "OFF"
    if "manualOverride" in status:
        override = status.get("manualOverride")
        if override is True:
            source = "ligado no manual"
        elif override is False:
            source = "desligado no manual"
        elif status.get("inWindow"):
            source = "agenda"
        else:
            source = "fora da agenda"
    else:
        windows = (schedule or {}).get("windows") or []
        using = bool((schedule or {}).get("enabled")) and bool(windows)
        if not using:
            source = "sem agenda, ligado direto" if watching else "sem agenda"
        elif watching:
            source = "dentro da agenda"
        elif armed:
            source = "armado, fora da agenda"
        else:
            source = "desarmado"
    cams = status.get("cameras") or []
    cameras = " · ".join(
        f"{c.get('name') or c.get('id')} {c.get('state') or '?'}" for c in cams)
    if api_on is True:
        api = "celular pode falar com este notebook"
    elif api_on is False:
        api = "API do celular desligada"
    else:
        api = ""
    return {
        "tone": tone, "headline": headline, "source": source, "cameras": cameras,
        "warnings": " · ".join(status.get("warnings") or []), "api": api,
    }


def _lock():
    path = socket_path().with_name("ktsentinel-guard-hud.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("indicador já está aberto", file=sys.stderr)
        raise SystemExit(0)
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def _view(config_path: Path) -> dict:
    try:
        status = ask("status")
        try:
            schedule = ask("get_schedule")
        except (OSError, RuntimeError, json.JSONDecodeError, TimeoutError):
            schedule = None
        return describe(status, schedule, api_enabled(config_path))
    except (OSError, RuntimeError, json.JSONDecodeError, TimeoutError):
        return describe(None)


def _run_tk(config_path: Path, lock) -> int:
    import tkinter as tk
    from tkinter import font as tkfont

    colors = {"on": "#65e6a4", "pause": "#F5B62E", "off": "#5a6a7e"}
    root = tk.Tk()
    root.title("kTsentinel Guard")
    root.configure(bg="#08090b")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root._lock = lock
    head_font = tkfont.Font(family="sans-serif", size=22, weight="bold")
    body = tkfont.Font(family="sans-serif", size=11)
    head = tk.Label(root, text="…", fg=colors["off"], bg="#08090b", font=head_font, anchor="w")
    sub = tk.Label(root, text="", fg="#8897A6", bg="#08090b", font=body, anchor="w", justify="left", wraplength=300)
    cams = tk.Label(root, text="", fg="#E7EDF2", bg="#08090b", font=body, anchor="w", justify="left", wraplength=300)
    api = tk.Label(root, text="", fg="#8897A6", bg="#08090b", font=body, anchor="w", justify="left", wraplength=300)
    warn = tk.Label(root, text="", fg="#ffb4b4", bg="#08090b", font=body, anchor="w", justify="left", wraplength=300)
    clock = tk.Label(root, text="", fg="#8897A6", bg="#08090b", font=body, anchor="w")
    for w in (head, sub, cams, api, warn, clock):
        w.pack(fill="x", padx=14, pady=1)
    head.pack_configure(pady=(12, 2))
    clock.pack_configure(pady=(2, 12))

    def place():
        root.update_idletasks()
        x = root.winfo_screenwidth() - root.winfo_width() - 24
        root.geometry(f"+{x}+24")

    def refresh():
        view = _view(config_path)
        head.configure(text=view["headline"], fg=colors[view["tone"]])
        sub.configure(text=view["source"])
        cams.configure(text=view["cameras"])
        api.configure(text=view["api"])
        warn.configure(text=view["warnings"])
        clock.configure(text=datetime.now().strftime("atualizado %H:%M:%S"))
        root.after(2000, refresh)

    refresh()
    place()
    root.mainloop()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sentinel-guard-hud")
    p.add_argument("--config", default=str(default_config_path()))
    args = p.parse_args(argv)
    lock = _lock()
    config_path = Path(args.config)
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return _run_tk(config_path, lock)
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget,
    )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("kTsentinel Guard")
    colors = {"on": "#65e6a4", "pause": "#F5B62E", "off": "#5a6a7e"}

    def icon_for(tone: str) -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(colors[tone]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(8, 8, 48, 48)
        painter.end()
        return QIcon(pm)

    win = QWidget()
    win.setWindowTitle("kTsentinel Guard")
    win.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
    win.setStyleSheet(
        "QWidget { background:#08090b; color:#E7EDF2; font-family:sans-serif; }"
        "QLabel#head { font-size:28px; font-weight:700; }"
        "QLabel#sub { color:#8897A6; font-size:13px; }"
        "QLabel#bad { color:#ffb4b4; font-size:12px; }"
    )
    lay = QVBoxLayout(win)
    lay.setContentsMargins(16, 14, 16, 14)
    head = QLabel("…")
    head.setObjectName("head")
    sub = QLabel("")
    sub.setObjectName("sub")
    sub.setWordWrap(True)
    cams = QLabel("")
    cams.setWordWrap(True)
    api = QLabel("")
    api.setObjectName("sub")
    api.setWordWrap(True)
    warn = QLabel("")
    warn.setObjectName("bad")
    warn.setWordWrap(True)
    clock = QLabel("")
    clock.setObjectName("sub")
    for w in (head, sub, cams, api, warn, clock):
        lay.addWidget(w)
    win.setFixedWidth(320)

    tray = QSystemTrayIcon(icon_for("off"))
    tray.setToolTip("kTsentinel Guard")
    menu = QMenu()
    show_act = QAction("Mostrar")
    quit_act = QAction("Fechar")
    menu.addAction(show_act)
    menu.addAction(quit_act)
    tray.setContextMenu(menu)

    def place():
        screen = app.primaryScreen().availableGeometry()
        win.adjustSize()
        frame = win.frameGeometry()
        win.move(screen.right() - frame.width() - 16, screen.top() + 16)

    def refresh():
        view = _view(config_path)
        tone = view["tone"]
        head.setText(view["headline"])
        head.setStyleSheet(f"color:{colors[tone]}; font-size:28px; font-weight:700;")
        sub.setText(view["source"])
        cams.setText(view["cameras"])
        api.setText(view["api"])
        warn.setText(view["warnings"])
        clock.setText(datetime.now().strftime("atualizado %H:%M:%S"))
        tray.setIcon(icon_for(tone))
        tip = view["headline"]
        if view["source"]:
            tip += " · " + view["source"]
        if view["cameras"]:
            tip += "\n" + view["cameras"]
        tray.setToolTip(tip)

    def toggle(_reason=None):
        if win.isVisible():
            win.hide()
        else:
            place()
            win.show()
            win.raise_()

    show_act.triggered.connect(lambda: (place(), win.show(), win.raise_()))
    quit_act.triggered.connect(app.quit)
    tray.activated.connect(
        lambda reason: toggle(reason) if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    refresh()
    place()
    win.show()
    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(2000)
    # mantém o lock vivo enquanto a janela existir
    win._lock = lock
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ConfigError, check_private, load_config
from .paths import default_config_path, socket_path
from .topics import generate_secure_topic, is_sentinel_topic


def _load(args):
    return load_config(Path(args.config))


def cmd_run(args):
    import os
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;15000000")
    from .daemon import Daemon
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return Daemon(Path(args.config)).run()


def cmd_check_config(args):
    cfg = _load(args)
    print(f"config OK: {cfg.path}")
    problems = 0
    f = cfg.fcm
    if f.enabled:
        sa = f.service_account_file
        if not sa.exists():
            print(f"ERRO: chave não encontrada: {sa}"); problems += 1
        else:
            try:
                check_private(sa)
                data = json.loads(sa.read_text())
                if data.get("type") != "service_account":
                    raise ValueError("não é uma chave de service account")
                if data.get("project_id") != f.project_id:
                    raise ValueError(f"project_id da chave ({data.get('project_id')}) != config ({f.project_id})")
                print("FCM: chave OK")
            except Exception as e:
                print(f"ERRO FCM: {e}"); problems += 1
        if not is_sentinel_topic(f.topic):
            print(f"AVISO: tópico fora do formato sentinel_<32 hex>: {f.topic}")
    else:
        print("AVISO: FCM não configurado")
    print(f"{len(cfg.cameras)} câmera(s): " + ", ".join(f"{c.id}:{c.name}" for c in cfg.cameras))
    if cfg.detection.person_filter:
        from .person import model_available
        if not model_available(cfg.detection.person_model_dir):
            print(f"ERRO: modelo de pessoa ausente em {cfg.detection.person_model_dir} (scripts/download_models.sh)")
            problems += 1
        else:
            print("filtro de pessoa: modelo OK")
    elif any(c.analytics for c in cfg.cameras):
        print("ERRO: tripwire/loiter sem [detection].person_filter"); problems += 1
    if cfg.rules.behavior_only and not any(c.analytics for c in cfg.cameras if c.is_active):
        print("AVISO: behavior_only sem tripwire/loiter — nenhum push de movimento")
    return 1 if problems else 0


def cmd_test_fcm(args):
    from .fcm import FcmError, FcmSender
    cfg = _load(args)
    if not cfg.fcm.enabled:
        print("ERRO: [fcm] incompleto no config"); return 1
    try:
        res = FcmSender(cfg.fcm).send("Teste do kTsentinel Guard", validate_only=args.dry_run)
    except FcmError as e:
        print(f"ERRO: {e}"); return 1
    print(("dry-run aceito pela API" if args.dry_run else "enviado") + f": {res}")
    return 0


def _draw_zones(frame, cam):
    import numpy as np
    import cv2
    h, w = frame.shape[:2]
    for zones, color in ((cam.ignore_zones, (0, 0, 255)), (cam.include_zones, (0, 255, 0))):
        for z in zones:
            pts = np.array([[int(x * w), int(y * h)] for x, y in z], np.int32)
            cv2.polylines(frame, [pts], True, color, 2)
    if cam.loiter_zone:  # amarelo = zona de loitering; azul = tripwires
        pts = np.array([[int(x * w), int(y * h)] for x, y in cam.loiter_zone], np.int32)
        cv2.polylines(frame, [pts], True, (0, 220, 255), 2)
    for t in cam.tripwires:
        (x1, y1), (x2, y2) = t["line"]
        cv2.line(frame, (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h)), (255, 128, 0), 3)
        cv2.putText(frame, f"{t['name']} ({t['direction']})", (int(x1 * w) + 5, int(y1 * h) + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 128, 0), 1)


def cmd_test_camera(args):
    import cv2
    from .camera import redact_url
    import sentinel_guard.capture  # noqa: F401  (opções ffmpeg RTSP)
    cfg = _load(args)
    cams = [c for c in cfg.cameras if args.id in (None, c.id)]
    if not cams:
        print("ERRO: câmera não encontrada"); return 1
    rc = 0
    for c in cams:
        for label, url in (("principal", c.rtsp_url), ("secundário", c.rtsp_url_secondary)):
            if not url:
                continue
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            ok, frame = cap.read() if cap.isOpened() else (False, None)
            cap.release()
            if ok:
                out = Path(args.out) / f"cam{c.id}_{label}.jpg"
                out.parent.mkdir(parents=True, exist_ok=True)
                _draw_zones(frame, c)
                cv2.imwrite(str(out), frame)
                print(f"[{c.name}] {label}: OK {frame.shape[1]}x{frame.shape[0]} -> {out}")
            else:
                print(f"[{c.name}] {label}: FALHA ({redact_url(url)})"); rc = 1
    return rc


def cmd_socket(args):
    from .server import request
    try:
        resp = request(socket_path(), args.cmd.replace("-", "_"), {})
    except OSError as e:
        print(f"daemon não está rodando ({e})"); return 1
    print(json.dumps(resp, indent=2, ensure_ascii=False))
    return 0 if resp.get("ok") else 1


def cmd_events(args):
    from .server import request
    try:
        resp = request(socket_path(), "list_events", {"limit": args.limit, "since": args.since})
    except OSError as e:
        print(f"daemon não está rodando ({e})"); return 1
    if not resp.get("ok"):
        print(resp.get("error")); return 1
    for e in reversed(resp["result"]):
        day, t0 = e["start"].split("T")
        t1 = (e["end"] or e["start"]).split("T")[1]
        status = "notificado" if e["notified"] else (e["reason"] or "pendente")
        kind = e.get("kind") or "motion"
        print(f"#{e['id']:<4} {day} {t0[:8]}–{t1[:8]}  {e['cameraName']:<10} {kind:<9} pico {e['peakAreaPct'] or 0:5.1f}%  "
              f"{status:<17} DVR: {e['dvrWindow'][0]}–{e['dvrWindow'][1]}")
        print(f"      {e['peakSnapshot'] or e['snapshot']}")
    return 0


def cmd_gen_token(args):
    import os
    import secrets
    cfg = _load(args)
    path = cfg.api.token_file
    if path.exists() and not args.force:
        print(f"{path} já existe (use --force para substituir)"); return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32) + "\n")
    print(f"token gravado em {path} (600); copie-o para o app por um canal seguro")
    return 0


def cmd_gen_topic(args):
    print(generate_secure_topic())
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="sentinel-guard")
    p.add_argument("--config", default=str(default_config_path()))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("check-config").set_defaults(fn=cmd_check_config)
    gt = sub.add_parser("gen-token", help="gera o token da API HTTP")
    gt.add_argument("--force", action="store_true")
    gt.set_defaults(fn=cmd_gen_token)
    sub.add_parser("gen-topic").set_defaults(fn=cmd_gen_topic)
    t = sub.add_parser("test-fcm")
    t.add_argument("--dry-run", action="store_true", help="valida na API sem entregar")
    t.set_defaults(fn=cmd_test_fcm)
    tc = sub.add_parser("test-camera")
    tc.add_argument("id", type=int, nargs="?")
    tc.add_argument("--out", default="/tmp/ktsentinel-guard-test")
    tc.set_defaults(fn=cmd_test_camera)
    for name in ("status", "arm", "disarm", "list-cameras", "test-notification", "reload-config"):
        sub.add_parser(name).set_defaults(fn=cmd_socket, cmd=name)
    ev = sub.add_parser("events", help="eventos de movimento com horário para achar no DVR")
    ev.add_argument("--limit", type=int, default=20)
    ev.add_argument("--since", help="ex.: 2026-10-13 ou 2026-10-13T08:00")
    ev.set_defaults(fn=cmd_events)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f"ERRO de configuração: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

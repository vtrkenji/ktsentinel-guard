import logging
import threading
import time

import requests

from .config import FcmConfig

log = logging.getLogger("sentinel_guard.fcm")
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"


class FcmError(Exception):
    pass


def build_message(topic: str, title: str, body: str) -> dict:
    return {"message": {
        "topic": topic,
        "notification": {"title": title, "body": body},
        "android": {"priority": "HIGH"},
    }}


class FcmSender:
    def __init__(self, cfg: FcmConfig, session=None):
        self.cfg = cfg
        self._session = session or requests.Session()
        self._creds = None
        self._lock = threading.Lock()

    def _token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        with self._lock:
            if self._creds is None:
                self._creds = service_account.Credentials.from_service_account_file(
                    str(self.cfg.service_account_file), scopes=[SCOPE])
            if not self._creds.valid:
                self._creds.refresh(Request())
            return self._creds.token

    def send(self, body: str, validate_only: bool = False, title: str | None = None) -> dict:
        payload = build_message(self.cfg.topic, title or self.cfg.title, body)
        if validate_only:
            payload["validate_only"] = True
        try:
            r = self._session.post(
                ENDPOINT.format(project=self.cfg.project_id), json=payload, timeout=15,
                headers={"Authorization": f"Bearer {self._token()}"})
        except Exception as e:  # rede ou credencial
            raise FcmError(f"falha ao chamar FCM: {e}") from e
        if r.status_code != 200:
            raise FcmError(f"FCM HTTP {r.status_code}: {r.text[:300]}")
        return r.json()


class NtfySender:
    def __init__(self, url: str, session=None):
        self.url = url
        self._session = session or requests.Session()

    def send(self, body: str, title: str = "kTsentinel - Casa") -> None:
        r = self._session.post(self.url, data=body.encode("utf-8"),
                               headers={"Title": title}, timeout=15)
        if r.status_code >= 300:
            raise FcmError(f"ntfy HTTP {r.status_code}")


def with_retries(fn, attempts=3, delay=2.0):
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            log.warning("envio falhou (%d/%d): %s", i + 1, attempts, e)
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))

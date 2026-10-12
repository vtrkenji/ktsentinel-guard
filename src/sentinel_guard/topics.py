"""Validação de tópicos FCM (espelha lib/utils/fcm_utils.dart do app kTsentinel)."""
import re
import secrets

_PATTERN = re.compile(r"^[a-zA-Z0-9_\-.~%]+$")
_MAX_LEN = 900
_SENTINEL = re.compile(r"^sentinel_[0-9a-f]{32}$")


def validate_topic(topic: str | None) -> str | None:
    """Retorna None se válido, senão a mensagem de erro."""
    if not topic:
        return "O tópico FCM não pode ser vazio."
    if len(topic) > _MAX_LEN:
        return f"O tópico FCM não pode ter mais de {_MAX_LEN} caracteres."
    if not _PATTERN.match(topic):
        return "O tópico FCM só pode conter letras, números, _, -, ., ~, %."
    return None


def is_sentinel_topic(topic: str) -> bool:
    return bool(_SENTINEL.match(topic))


def generate_secure_topic() -> str:
    return "sentinel_" + secrets.token_hex(16)

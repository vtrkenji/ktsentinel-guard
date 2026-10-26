import os
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ktsentinel-guard"


def default_config_path() -> Path:
    return config_dir() / "config.toml"


def default_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ktsentinel-guard"


def runtime_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        path = Path(base) / "ktsentinel-guard"
    else:
        path = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/ktsentinel-guard-{os.getuid()}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def socket_path() -> Path:
    return runtime_dir() / "ktsentinel-guard.sock"

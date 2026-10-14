import os
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ktsentinel-guard"


def default_config_path() -> Path:
    return config_dir() / "config.toml"


def default_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ktsentinel-guard"


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/ktsentinel-guard-{os.getuid()}"
    Path(runtime).mkdir(mode=0o700, exist_ok=True)
    return Path(runtime) / "ktsentinel-guard.sock"

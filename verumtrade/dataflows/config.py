import verumtrade.default_config as default_config
from typing import Dict, Optional
from pathlib import Path


def _load_env_if_present() -> None:
    """Best-effort .env loading for non-CLI entrypoints (Windows-friendly).

    Many scripts/tests import verumtrade directly without going through cli/main.py,
    so ensure environment variables (e.g., ALPHA_VANTAGE_API_KEY) can still be found.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    here = Path(__file__).resolve()
    repo_root = here.parents[2]  # .../verumtrade/dataflows/config.py -> repo root

    for candidate in (repo_root / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            break

# Use default config but allow it to be overridden
_config: Optional[Dict] = None
DATA_DIR: Optional[str] = None


def initialize_config():
    """Initialize the configuration with default values."""
    global _config, DATA_DIR
    if _config is None:
        _config = default_config.DEFAULT_CONFIG.copy()
        DATA_DIR = _config["data_dir"]


def set_config(config: Dict):
    """Update the configuration with custom values."""
    global _config, DATA_DIR
    if _config is None:
        _config = default_config.DEFAULT_CONFIG.copy()
    _config.update(config)
    DATA_DIR = _config["data_dir"]


def get_config() -> Dict:
    """Get the current configuration."""
    if _config is None:
        initialize_config()
    return _config.copy()


# Initialize with default config
_load_env_if_present()
initialize_config()

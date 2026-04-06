"""Load and validate the user's YAML config file."""
import os
from pathlib import Path
from typing import Optional
import yaml
from dotenv import load_dotenv

from .models import SearchCriteria

CONFIG_PATH = Path.home() / ".config" / "property-alert" / "config.yaml"

load_dotenv()


def config_path() -> Path:
    return CONFIG_PATH


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config file not found at {CONFIG_PATH}.\n"
            "Run `property-alert init` to create it."
        )
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    # Pull secrets from environment variables if not set in config
    notif = cfg.setdefault("notification", {})
    email = notif.setdefault("email", {})
    if not email.get("from"):
        email["from"] = os.getenv("EMAIL_FROM", "")
    if not email.get("to"):
        email["to"] = os.getenv("EMAIL_TO", "")
    if not email.get("password"):
        email["password"] = os.getenv("EMAIL_APP_PASSWORD", "")

    cfg.setdefault("google_maps_api_key", os.getenv("GOOGLE_MAPS_API_KEY", ""))
    cfg.setdefault("epc_api_key", os.getenv("EPC_API_KEY", ""))

    return cfg


def get_searches(cfg: dict) -> list[SearchCriteria]:
    return [SearchCriteria.from_dict(s) for s in cfg.get("searches", [])]


def get_email_config(cfg: dict) -> dict:
    return cfg.get("notification", {}).get("email", {})


def get_digest_times(cfg: dict) -> list[str]:
    return cfg.get("notification", {}).get("digest_times", ["09:00", "18:00"])


def get_schedule_hours(cfg: dict) -> int:
    return cfg.get("schedule_hours", 6)

"""Reads config.yaml (settings) and .env (secret keys)."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_FOLDER = Path(__file__).resolve().parent.parent


def load_config(data_folder=None):
    """Return all settings as one dictionary, plus the paths where files are saved."""
    load_dotenv(PROJECT_FOLDER / ".env")

    config_file = PROJECT_FOLDER / "config.yaml"
    if not config_file.exists():
        raise FileNotFoundError("config.yaml not found in " + str(PROJECT_FOLDER))
    cfg = yaml.safe_load(config_file.read_text(encoding="utf-8"))

    for section in ["niche", "discovery", "filters", "brand", "llm", "messages", "sending"]:
        if section not in cfg:
            raise ValueError("config.yaml is missing the '" + section + "' section")

    data = Path(data_folder or PROJECT_FOLDER / "data")
    data.mkdir(parents=True, exist_ok=True)
    (data / "outbox").mkdir(exist_ok=True)
    cfg["paths"] = {"data": data, "db": data / "outreach.db", "outbox": data / "outbox"}
    return cfg


def all_keywords(cfg):
    """Every niche keyword from config.yaml in one flat list."""
    keywords = []
    for keyword_list in cfg["niche"]["keywords"].values():
        keywords.extend(keyword_list)
    return keywords

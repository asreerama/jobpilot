"""Config loading + shared paths. The pipeline root is the directory holding config.yaml."""
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    path = os.path.join(ROOT, "config.yaml")
    if not os.path.exists(path):
        sys.exit(
            "config.yaml not found. Copy config.example.yaml to config.yaml "
            "and fill in your profile and criteria first (see README)."
        )
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = ROOT
    cfg["_db_path"] = os.path.join(ROOT, cfg.get("db", "jobs.db"))
    cfg["_csv_path"] = os.path.normpath(
        os.path.join(ROOT, cfg.get("applications_csv", "applications.csv"))
    )
    cfg["_candidate_rules"] = _load_profile()
    return cfg


def _load_profile() -> str:
    """profile.md carries the candidate facts and fixed answers injected into
    the apply prompt. It is gitignored; users create it from profile.example.md."""
    path = os.path.join(ROOT, "profile.md")
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""

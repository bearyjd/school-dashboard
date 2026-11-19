"""Seeding helpers for the report / alias / sender tests (synthetic data only)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from recon.ingest_support import make_db

REPO = Path(__file__).resolve().parents[2]
SYNTH_TITLES = ("Essay Draft", "Chapter 4 Quiz", "Chapter 4 Quiz Review", "Volcano Poster")
SYNTH_CLASSES = ("Language Arts", "ELA", "Math 6")


def sgy(title: str, cls: str, key: str, *, due: str = "2026-11-20", status: str = "open", **extra: Any) -> dict:
    return {"kid": "Alex", "title": title, "title_key": title.casefold(), "class_name": cls,
            "class_key": cls.casefold(), "due_date": due, **extra,
            "links": [{"source": "schoology", "external_key": key, "norm_status": status}]}


def ps(title: str, cls: str, key: str, *, due: str = "2026-11-20", status: str = "open", **extra: Any) -> dict:
    return {"kid": "Alex", "title": title, "title_key": title.casefold(), "class_name": cls,
            "class_key": cls.casefold(), "due_date": due, **extra,
            "links": [{"source": "powerschool", "external_key": key, "norm_status": status}]}


def db_with(tmp_path: Path, seed: list[dict], **kw: Any) -> str:
    return make_db(tmp_path, {"seed": seed, **kw.pop("setup", {})}, **kw)


def cli(state_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "SCHOOL_STATE_DIR": str(state_dir), "PYTHONPATH": str(REPO)}
    for k in ("SCHOOL_STATE_PATH", "SCHOOL_DB_PATH", "RECON_INGEST_ENABLED", "RECON_PUSH_ENABLED"):
        env.pop(k, None)
    return subprocess.run([sys.executable, "-m", "school_dashboard.cli", "assignments", *args],
                          capture_output=True, text=True, env=env, cwd=REPO)

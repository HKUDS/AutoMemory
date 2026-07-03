from __future__ import annotations

import json
import os
import tempfile
import hashlib
from typing import Any, Dict, List

import pandas as pd


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def canonical_json(obj: Any) -> str:
    def default(o: Any):
        if isinstance(o, (pd.Timestamp,)):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=default)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def atomic_write_bytes(path: str, data: bytes) -> None:
    ensure_dir(os.path.dirname(path))
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=dir_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def atomic_write_text(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_parquet(df: pd.DataFrame, path: str) -> None:
    ensure_dir(os.path.dirname(path))
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=dir_name)
    os.close(fd)
    try:
        df.to_parquet(tmp_path, index=False, compression="zstd", row_group_size=128_000)
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def dataframe_content_hash(df: pd.DataFrame, sort_by: List[str] | None = None) -> str:
    if df is None or len(df) == 0:
        return sha256_text("[]")
    if sort_by:
        df = df.sort_values(sort_by)
    # Standardize column order to ensure repeatability
    cols = list(df.columns)
    records = df[cols].to_dict(orient="records")
    return sha256_obj(records)


def write_parquet_idempotent(df: pd.DataFrame, path: str, sort_by: List[str] | None = None) -> bool:
    """Write Parquet (with content hash). Skip if exists and content unchanged.
    Returns True if write/replace occurred, False if skipped.
    """
    if df is None:
        return False
    ensure_dir(os.path.dirname(path))
    hash_path = path + ".sha256"
    new_hash = dataframe_content_hash(df, sort_by)
    try:
        if os.path.exists(path) and os.path.exists(hash_path):
            old_hash = open(hash_path, "r", encoding="utf-8").read().strip()
            if old_hash == new_hash:
                return False
        atomic_write_parquet(df, path)
        atomic_write_text(hash_path, new_hash)
        return True
    except Exception:
        # Fallback to best-effort write (still use compression)
        df.to_parquet(path, index=False, compression="zstd", row_group_size=128_000)
        atomic_write_text(hash_path, new_hash)
        return True


def atomic_append_jsonl(path: str, record: Dict[str, Any]) -> None:
    """Atomically append one line of JSON to JSONL file."""
    ensure_dir(os.path.dirname(path))
    existing = ""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        except Exception:
            existing = ""
    new_line = canonical_json(record) + "\n"
    atomic_write_text(path, existing + new_line)
 
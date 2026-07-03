"""
TradingAgent Utilities Module

Provides common utility functions for formatting, IO operations, logging configuration, etc.
"""

# Formatting tools
from .formatting import round_numbers_in_obj

# IO tools
from .io import (
    ensure_dir,
    canonical_json,
    sha256_text,
    sha256_obj,
    atomic_write_bytes,
    atomic_write_text,
    atomic_write_parquet,
    dataframe_content_hash,
    write_parquet_idempotent,
    atomic_append_jsonl,
)

# Logging tools
from .logging_setup import setup_json_logging, Metrics
from .logging_helper import get_llm_logger

__all__ = [
    # Formatting
    "round_numbers_in_obj",
    
    # IO operations
    "ensure_dir",
    "canonical_json", 
    "sha256_text",
    "sha256_obj",
    "atomic_write_bytes",
    "atomic_write_text", 
    "atomic_write_parquet",
    "dataframe_content_hash",
    "write_parquet_idempotent",
    "atomic_append_jsonl",
    
    # Logging
    "setup_json_logging",
    "Metrics",
    "get_llm_logger",
]
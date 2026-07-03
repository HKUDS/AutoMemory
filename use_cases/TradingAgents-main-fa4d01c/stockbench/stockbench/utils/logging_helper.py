"""
LLM Logger Configuration
Unified management of LLM-related log output, supports structured logging
"""

from __future__ import annotations

import os
from loguru import logger
from typing import Optional


def get_llm_logger():
    """Get LLM-specific logger
    
    Returns:
        logger: Configured LLM logger with structured output support
    """
    return logger


from __future__ import annotations

import math
from typing import Any, Dict, List


def _round_sig_number(value: float, sig: int = 2) -> float | int:
    """Round numeric values to specified significant digits.
    - Keep boolean values unchanged
    - Return 0.0 directly for 0
    - Return non-numeric and invalid values as-is
    - Absolute value >= 1: keep two decimal places
    - Absolute value < 1: keep two significant digits
    """
    try:
        if isinstance(value, bool):
            return value
        if value == 0 or value is None:
            return 0.0
        abs_val = abs(float(value))
        if abs_val == 0 or math.isinf(abs_val) or math.isnan(abs_val):
            return value
        
        # Absolute value >= 1: keep two decimal places
        if abs_val >= 1:
            return round(value, 2)
        # Absolute value < 1: keep two significant digits
        else:
            digits = sig - 1 - int(math.floor(math.log10(abs_val)))
            return round(value, digits)
    except Exception:
        return value


def round_numbers_in_obj(obj: Any, sig: int = 2) -> Any:
    """Recursively traverse structures and round all numeric values to specified significant digits.
    Supports dict / list / tuple / scalars. Other types are returned as-is.
    """
    if obj is None:
        return obj
    # Numbers (excluding booleans)
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return _round_sig_number(obj, sig)
    # Mapping types
    if isinstance(obj, dict):
        return {k: round_numbers_in_obj(v, sig) for k, v in obj.items()}
    # Sequence types
    if isinstance(obj, (list, tuple)):
        return [round_numbers_in_obj(v, sig) for v in obj]
    return obj



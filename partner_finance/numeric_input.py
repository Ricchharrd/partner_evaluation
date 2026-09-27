"""Conservative parsing of AI numeric fields; ambiguous locale formats are rejected."""
import re


def parse_number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing numeric value")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError("Not a number")
    value = value.strip().replace("\u2212", "-")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1].strip()
    if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", value):
        value = value.replace(",", "")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value):
        raise ValueError("Ambiguous numeric format")
    return float(value)

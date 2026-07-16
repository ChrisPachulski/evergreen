"""Line parsers: one callable per wire format, all signalling failure as ValueError."""

import json


def parse_json_line(line):
    """Parse one JSON log line into a flat dict; reject non-object top levels."""
    record = json.loads(line)
    if not isinstance(record, dict):
        raise ValueError("top-level JSON value is not an object")
    return record


def parse_logfmt_line(line):
    """Parse one logfmt line (space-separated key=value pairs)."""
    record = {}
    for token in line.split():
        key, sep, value = token.partition("=")
        if not sep:
            raise ValueError(f"bare token in logfmt line: {key!r}")
        record[key] = value.strip('"')
    return record


PARSERS = {"json": parse_json_line, "logfmt": parse_logfmt_line}

"""Row formatting and ordering helpers."""
import json


def sort_releases(releases):
    # Alphabetical by name so output is stable across machines.
    return sorted(releases, key=lambda r: r.get("name", ""))


def format_row(release, as_json=False):
    if as_json:
        return json.dumps(release, sort_keys=True)
    return f"{release.get('name', '?'):20} {release.get('date', '')}"

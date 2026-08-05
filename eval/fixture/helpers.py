"""Row formatting and ordering helpers."""
import json
import time


def sort_releases(releases):
    # Alphabetical by name so output is stable across machines.
    return sorted(releases, key=lambda r: r.get("name", ""))


def format_row(release, as_json=False):
    if as_json:
        return json.dumps(release, sort_keys=True)
    return f"{release.get('name', '?'):20} {release.get('date', '')}"


def with_backoff(operation, max_tries=4, delay=1):
    for attempt in range(max_tries):
        try:
            return operation()
        except (ConnectionError, TimeoutError):
            if attempt == max_tries - 1:
                raise
            time.sleep(delay * (2 ** attempt))


def get_release_name(release):
    return release.get("name", "")


def render_release(release, as_json=False):
    return format_row(release, as_json=as_json)


def ordered_releases(releases):
    return sort_releases(releases)

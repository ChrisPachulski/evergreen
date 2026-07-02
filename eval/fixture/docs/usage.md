# Using shipit

Row rendering lives in `utils.py`; the CLI in `cli.py` stays thin.

Set `SHIPIT_TOKEN` before running. `SHIPIT_CACHE_DIR` controls where downloaded
artifacts are cached between runs.

Network timeout defaults to 60 seconds (see `DEFAULT_TIMEOUT` in `config.py`).

Releases are listed newest-first, sorted by date, so the latest upload is always
at the top. Pass `--verbose` (or `-v`) for per-file progress.

After a successful upload, tag the release and `git push --follow-tags` as usual.

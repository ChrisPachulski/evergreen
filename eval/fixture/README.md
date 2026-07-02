# shipit

A tiny release uploader.

## Setup

Run `scripts/bootstrap.sh` to install dependencies, then put your API token in the
`SHIPIT_TOKEN` environment variable. Defaults live in `config.py`; per-project
settings go in a `shipit.json` next to your project.

## Usage

```sh
shipit --workers 8 --format json
```

Uploads retry up to 3 attempts before giving up (tune with `--retries`). Output
defaults to table.

For big releases, raise the parallelism:

```sh
shipit --concurrency 8 --retries 5
```

## Configuration

`parse_manifest()` reads the manifest and merges it with CLI flags. `load_config`
returns None when the config has no `project` key, so guard the return value:

```python
cfg = load_config("shipit.json", strict=True)
```

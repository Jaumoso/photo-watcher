## Setup

MacOS/Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows:

```sh
python3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux (CachyOS fish terminal)

```sh
source .venv/bin/activate.fish
python3 -m unittest test_watcher -v
```

## Run

```sh
python watcher.py
```

## Run with docker

```sh
docker compose -f docker-compose.dev.yml up
```

## Run on any machine with docker

```sh
docker compose up -d
```

## Configuration

All configuration is done through environment variables:

| Variable | Description | Default |
|---|---|---|
| `SOURCE_DIRS` | Comma-separated list of source directories to monitor | `./source` |
| `TARGET_BASE` | Destination base directory | `./target` |
| `IGNORE_FILES` | Comma-separated fnmatch patterns to ignore | _(empty)_ |
| `REQUIRE_DATE_DIRS` | Comma-separated source dirs where files **without embedded date metadata** (EXIF/video metadata) are skipped instead of using mtime fallback. Useful for WhatsApp folders where you want to manually set the date before archiving. | _(empty)_ |

# Current targer folder structure

`TARGET_FOLDER/<year>/<month_number>. <month_with_uppercase> <year>/<photos - videos>`
Example:
`target/2025/10. October 2025/IMG_***.jpg`

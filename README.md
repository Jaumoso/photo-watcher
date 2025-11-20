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

# Current targer folder structure

`TARGET_FOLDER/<year>/<month_number>. <month_with_uppercase> <year>/<photos - videos>`
Example:
`target/2025/10. October 2025/IMG_***.jpg`

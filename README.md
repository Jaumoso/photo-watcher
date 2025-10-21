## Setup

MacOS/Linux

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows:

```
.venv\Scripts\activate
python3 -m venv .venv
pip install -r requirements.txt
```

## Run

`python watcher.py`

## Run with docker

`docker compose -f docker-compose.dev.yml up`

## Run on any machine with docker

`docker compose up -d`

# Current targer folder structure

`TARGET_FOLDER/<year>/<month_number>. <month_with_uppercase> <year>/<photos - videos>`
Example:
`target/2025/10. October 2025/IMG_***.jpg`

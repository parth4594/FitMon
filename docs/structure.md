.
├── CLAUDE.md
├── Makefile
├── README.md
├── dbt
├── docs
│   └── structure.md
├── logs
│   ├── pipeline_2026-07-03.log
│   ├── pipeline_2026-07-13.log
│   └── pipeline_2026-07-17.log
├── notebooks
├── pyproject.toml
├── src
│   ├── cli.py
│   ├── config
│   │   ├── __init__.py
│   │   ├── logging_config.py
│   │   └── settings.py
│   ├── db
│   │   ├── __init__.py
│   │   └── postgres.py
│   ├── ingestion
│   │   ├── column_maps
│   │   │   ├── hevy
│   │   │   │   └── en.yaml
│   │   │   └── strong
│   │   │       ├── de.yaml
│   │   │       └── en.yaml
│   │   ├── ingest_apple_health_data.py
│   │   ├── ingest_hevy_api.py
│   │   ├── ingest_workout_csv.py
│   │   └── sources
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── hevy.py
│   │       └── strong.py
│   ├── models.py
│   ├── pipeline.py
│   └── services
├── tests
│   └── unit
│       └── test_ingest_workout_csv.py
├── utils
│   ├── create_meta_tables.sql
│   └── create_raw_tables.sql
└── uv.lock

17 directories, 30 files

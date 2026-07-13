.
├── CLAUDE.md
├── Makefile
├── README.md
├── dbt
├── docs
│   └── structure.md
├── logs
│   └── pipeline_2026-07-03.log
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
│   │   │   └── strong
│   │   │       ├── de.yaml
│   │   │       └── en.yaml
│   │   ├── ingest_apple_health_data.py
│   │   ├── ingest_hevy_api.py
│   │   └── ingest_workout_csv.py
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

15 directories, 23 files

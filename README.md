# FitMon 🏋️

**Fitness Monitor** — a personal, self-hosted workout tracking and real-time optimization system backed by science.

---

## Overview

FitMon is a project designed to replace commercial fitness apps with a fully owned, data-driven training platform. It tracks workouts, analyzes performance in real time, and delivers evidence-based suggestions to optimize training — all without vendor lock-in.

---

## Goals

- **Track workouts** — log exercises, sets, reps, and weight with a simple interface
- **Real-time analysis** — surface insights during and after each session
- **Science-backed optimization** — suggest progressive overload, deload weeks, and exercise swaps grounded in sports science research

---

## Tech Stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL (via Supabase) |
| Transformations | dbt Core |
| Data ingestion | Strong CSV export / Hevy API (planned) |
| Health metrics | Apple Health XML export |
| Bot interface | Telegram bot + Claude API |
| Dashboard | Grafana |
| Hosting | Railway / Render (free tier) |

---

## Data Sources

- **Strong** — historical workout data via CSV export
- **Hevy** — planned migration (official public API)
- **Apple Health / Apple Watch** — steps, heart rate, sleep, active calories


---

## Getting Started

> Prerequisites: Docker, Python 3.10+, Node.js 18+

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/FitMon.git
cd FitMon

# Start local Supabase (Postgres)
supabase start

# Install Python dependencies
pip install -r requirements.txt

```

---

## Roadmap

- [ ] Supabase schema setup
- [ ] Strong CSV import script
- [ ] Apple Health XML parser
- [ ] Telegram bot for ongoing workout logging
- [ ] dbt transformation models
- [ ] Grafana dashboard (PRs, volume, frequency heatmap)
- [ ] Real-time workout analysis via Claude API
- [ ] Migrate to Hevy API

---

## License

MIT — personal use project. Use freely, adapt as needed.
# WC2026 Prediction Bot ⚽

Telegram bot for a FIFA World Cup 2026 score-prediction pool (bólão). Each
player predicts the exact score of every match; after each game they get a DM
with the real result and their points. Standings are sent by DM at the end of
each round. Nobody sees anyone else's predictions.

## Scoring

| Outcome | Points |
|---|---|
| Correct direction (win/draw/loss) | **3** |
| Correct exact score | **+2** (total 5) |
| Correct World Cup champion (applied after the final) | **+100** |

- A draw counts as its own direction.
- Knockout matches are scored on the **90' score** (extra time / penalties
  ignored). The champion is resolved from who actually advanced.
- Predictions lock `LOCK_MINUTES` before each kickoff (default 15).
- Champion bets lock at the opening match kickoff.

## Commands

| Command | Action |
|---|---|
| `/start` | Register (asks for a unique nickname) |
| `/nick <name>` | Change nickname |
| `/proximos` | Matches still open for prediction |
| `/prever` | Make/edit a prediction (pick match → enter score like `2-1`) |
| `/minhas` | Your predictions and points |
| `/campeao` | Bet on the World Cup champion (+100) |
| `/tabela` | Current standings (your row highlighted) |
| `/ajuda` | Help |

## Architecture

```
wc2026bot/
├── scoring.py        Pure scoring (direction/exact/champion) — 100% unit-tested
├── service.py        Business logic over a DB session (register, predict, ...)
├── rounds.py         Canonical round identity from (stage, matchday)
├── standings.py      Standings, round completion, champion bonus
├── notifier.py       Throttled DM fan-out (respects Telegram limits)
├── config.py         Settings from .env
├── db/               SQLAlchemy 2.x models + session (UTC-aware datetimes)
├── providers/        FootballProvider abstraction + football-data.org impl + sync
├── bot/              Telegram handlers (thin) + message formatting
├── scheduler/        Jobs: sync fixtures, poll+score, publish tables, champion
└── main.py           Application + JobQueue wiring
```

The data source is behind a `FootballProvider` interface, so swapping
football-data.org for another API (e.g. API-Football) is a single new class.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) → get `TELEGRAM_BOT_TOKEN`.
2. Get a free key at [football-data.org](https://www.football-data.org/client/register).
3. Copy `.env.example` to `.env` and fill it in.

```bash
cp .env.example .env
# edit .env
```

## Run

### Local

```bash
pip install -r requirements-dev.txt
python -m wc2026bot.main
```

### Docker

```bash
docker compose up -d --build
docker compose logs -f
```

SQLite lives in the `wc2026bot_data` named volume at `/app/data/wc2026.db`.

### Deploy (standalone)

This bot is self-contained — it is **not** part of the homeserver orchestrator.
It runs its own compose stack with its own named volume. On the server:

```bash
cd /home/garminbot/WC2026
# create .env once (from .env.example) — never committed
bash deploy.sh
```

`deploy.sh` syncs to `origin/main`, rebuilds, restarts, and checks the
container is running.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The scoring, sync, service, standings, notifier and orchestration jobs are all
covered. Tests use a temporary SQLite file and never hit the network.

## Notes / limitations

- football-data.org's free tier rate-limits requests; polling is windowed and
  retries back off (never retrying HTTP 429).
- Knockout fixtures arrive with `matchday=null` from the provider; round
  identity therefore comes from the stage, not the matchday.

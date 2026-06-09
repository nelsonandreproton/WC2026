"""One-time migration: add reminder_sent_at and result_broadcast_at to matches.

Run once on the production DB:
    python migrate_add_notification_columns.py [DB_PATH]

DB_PATH defaults to data/wc2026.db (same default as the bot).
"""

import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else "data/wc2026.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

for col in ("reminder_sent_at", "result_broadcast_at"):
    try:
        cur.execute(f"ALTER TABLE matches ADD COLUMN {col} DATETIME")
        print(f"Added column: {col}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc):
            print(f"Already exists (skipped): {col}")
        else:
            raise

conn.commit()
conn.close()
print("Done.")

"""
SQLite queue for managing search terms and creator handles.

Usage:
  python3 queue.py import search_terms.txt search
  python3 queue.py import tiktok_creators.txt creator
  python3 queue.py stats
  python3 queue.py reset
"""
import sqlite3
import os
import sys

QUEUE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.db")


def _conn():
    conn = sqlite3.connect(QUEUE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS terms (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                term         TEXT UNIQUE NOT NULL,
                type         TEXT NOT NULL DEFAULT 'search',
                status       TEXT NOT NULL DEFAULT 'pending',
                added_at     TEXT NOT NULL DEFAULT (datetime('now')),
                started_at   TEXT,
                completed_at TEXT,
                videos_saved INTEGER
            )
        """)
        conn.commit()


def import_from_file(filepath, term_type='search'):
    """Import terms from a text file, skipping # comments and blank lines."""
    init_db()
    terms = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                terms.append(line)

    inserted = 0
    with _conn() as conn:
        for term in terms:
            cur = conn.execute(
                "INSERT OR IGNORE INTO terms (term, type) VALUES (?, ?)",
                (term, term_type)
            )
            inserted += cur.rowcount
        conn.commit()
    return inserted, len(terms)


def grab_batch(n=5, term_type=None):
    """
    Grab N random pending terms, mark them in_progress.
    Returns list of (id, term).
    """
    init_db()
    with _conn() as conn:
        if term_type:
            rows = conn.execute(
                "SELECT id, term FROM terms "
                "WHERE status='pending' AND type=? "
                "ORDER BY RANDOM() LIMIT ?",
                (term_type, n)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, term FROM terms "
                "WHERE status='pending' "
                "ORDER BY RANDOM() LIMIT ?",
                (n,)
            ).fetchall()

        if not rows:
            return []

        ids = [r["id"] for r in rows]
        conn.execute(
            f"UPDATE terms SET status='in_progress', started_at=datetime('now') "
            f"WHERE id IN ({','.join('?'*len(ids))})",
            ids
        )
        conn.commit()
        return [(r["id"], r["term"]) for r in rows]


def mark_done(term_id, videos_saved=0):
    with _conn() as conn:
        conn.execute(
            "UPDATE terms SET status='done', completed_at=datetime('now'), "
            "videos_saved=? WHERE id=?",
            (videos_saved, term_id)
        )
        conn.commit()


def mark_failed(term_id):
    with _conn() as conn:
        conn.execute(
            "UPDATE terms SET status='failed', completed_at=datetime('now') WHERE id=?",
            (term_id,)
        )
        conn.commit()


def reset_in_progress():
    """Reset stuck in_progress terms to pending (e.g. after a crash)."""
    with _conn() as conn:
        n = conn.execute(
            "UPDATE terms SET status='pending', started_at=NULL WHERE status='in_progress'"
        ).rowcount
        conn.commit()
    return n


def stats():
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM terms GROUP BY status ORDER BY status"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
    result = {r["status"]: r["n"] for r in rows}
    result["total"] = total
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "import":
        if len(sys.argv) < 3:
            print("Usage: python3 queue.py import <file> [search|creator]")
            sys.exit(1)
        filepath = sys.argv[2]
        term_type = sys.argv[3] if len(sys.argv) > 3 else "search"
        inserted, total = import_from_file(filepath, term_type)
        print(f"Imported {inserted} new / {total} total ({total - inserted} already existed) [{term_type}]")

    elif cmd == "stats":
        s = stats()
        for k, v in sorted(s.items()):
            print(f"  {k:15} {v}")

    elif cmd == "reset":
        n = reset_in_progress()
        print(f"Reset {n} in_progress → pending.")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

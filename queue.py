"""
PostgreSQL-backed queue for search terms.
Supports concurrent consumers via FOR UPDATE SKIP LOCKED — safe for multiple
scraper instances running on different machines simultaneously.

Usage:
  python3 queue.py import search_terms.txt search
  python3 queue.py stats
  python3 queue.py reset       # reset in_progress → pending (unstick crashes)
  python3 queue.py reset-all   # reset ALL terms → pending (fresh start)
  python3 queue.py migrate     # one-time: import local queue.db SQLite → PostgreSQL
"""
import os
import sys
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Text, DateTime, create_engine, text
from sqlalchemy.orm import Session, DeclarativeBase

VPS_HOST = "150.136.40.239"
DB_USER  = "app1_user"
DB_PASS  = "app1dev"
DB_NAME  = "tiktoks"


def _get_engine():
    url = os.environ.get(
        "TIKTOKS_DATABASE_URL",
        f"postgresql://{DB_USER}:{DB_PASS}@{VPS_HOST}:5432/{DB_NAME}"
    )
    return create_engine(url, pool_pre_ping=True)


engine = _get_engine()


class Base(DeclarativeBase):
    pass


class Term(Base):
    __tablename__ = "terms"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    term         = Column(Text, nullable=False, unique=True)
    type         = Column(Text, nullable=False, default="search")
    status       = Column(Text, nullable=False, default="pending")
    added_at     = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at   = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    videos_saved = Column(Integer)


def init_db():
    Base.metadata.create_all(engine)


def import_from_file(filepath, term_type="search"):
    """Import terms from a text file, skipping # comments and blank lines."""
    init_db()
    terms = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)

    inserted = 0
    with Session(engine) as session:
        for term in terms:
            exists = session.execute(
                text("SELECT 1 FROM terms WHERE term = :t"), {"t": term}
            ).fetchone()
            if not exists:
                session.execute(
                    text("INSERT INTO terms (term, type) VALUES (:t, :tp)"),
                    {"t": term, "tp": term_type}
                )
                inserted += 1
        session.commit()
    return inserted, len(terms)


def grab_batch(n=5, term_type=None):
    """
    Grab N random pending terms atomically using FOR UPDATE SKIP LOCKED.
    Two concurrent callers will never receive the same term.
    Returns list of (id, term).
    """
    init_db()
    with Session(engine) as session:
        with session.begin():
            if term_type:
                rows = session.execute(
                    text("""
                        SELECT id, term FROM terms
                        WHERE status = 'pending' AND type = :type
                        ORDER BY RANDOM() LIMIT :n
                        FOR UPDATE SKIP LOCKED
                    """),
                    {"type": term_type, "n": n}
                ).fetchall()
            else:
                rows = session.execute(
                    text("""
                        SELECT id, term FROM terms
                        WHERE status = 'pending'
                        ORDER BY RANDOM() LIMIT :n
                        FOR UPDATE SKIP LOCKED
                    """),
                    {"n": n}
                ).fetchall()

            if not rows:
                return []

            ids = [r.id for r in rows]
            session.execute(
                text("""
                    UPDATE terms SET status = 'in_progress', started_at = NOW()
                    WHERE id = ANY(:ids)
                """),
                {"ids": ids}
            )
            return [(r.id, r.term) for r in rows]


def mark_done(term_id, videos_saved=0):
    with Session(engine) as session:
        session.execute(
            text("""
                UPDATE terms SET status = 'done', completed_at = NOW(),
                videos_saved = :vs WHERE id = :id
            """),
            {"vs": videos_saved, "id": term_id}
        )
        session.commit()


def mark_failed(term_id):
    with Session(engine) as session:
        session.execute(
            text("UPDATE terms SET status = 'failed', completed_at = NOW() WHERE id = :id"),
            {"id": term_id}
        )
        session.commit()


def reset_in_progress():
    """Reset stuck in_progress terms to pending (e.g. after a crash)."""
    with Session(engine) as session:
        r = session.execute(
            text("UPDATE terms SET status = 'pending', started_at = NULL WHERE status = 'in_progress'")
        )
        session.commit()
        return r.rowcount


def reset_all(term_type="search"):
    """Reset ALL terms of a given type back to pending."""
    with Session(engine) as session:
        r = session.execute(
            text("""
                UPDATE terms
                SET status = 'pending', started_at = NULL,
                    completed_at = NULL, videos_saved = NULL
                WHERE type = :tp
            """),
            {"tp": term_type}
        )
        session.commit()
        return r.rowcount


def stats():
    init_db()
    with Session(engine) as session:
        rows = session.execute(
            text("SELECT status, COUNT(*) AS n FROM terms GROUP BY status ORDER BY status")
        ).fetchall()
        total = session.execute(text("SELECT COUNT(*) FROM terms")).fetchone()[0]
    result = {r.status: r.n for r in rows}
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

    elif cmd == "reset-all":
        term_type = sys.argv[2] if len(sys.argv) > 2 else "search"
        n = reset_all(term_type)
        print(f"Reset {n} {term_type} terms → pending.")

    elif cmd == "migrate":
        import sqlite3
        SQLITE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.db")
        if not os.path.exists(SQLITE_DB):
            print(f"No queue.db found at {SQLITE_DB}")
            sys.exit(1)
        init_db()
        conn = sqlite3.connect(SQLITE_DB)
        conn.row_factory = sqlite3.Row
        sqlite_rows = conn.execute("SELECT * FROM terms").fetchall()
        conn.close()
        migrated = 0
        with Session(engine) as session:
            for row in sqlite_rows:
                exists = session.execute(
                    text("SELECT 1 FROM terms WHERE term = :t"), {"t": row["term"]}
                ).fetchone()
                if not exists:
                    session.execute(
                        text("""
                            INSERT INTO terms
                              (term, type, status, added_at, started_at, completed_at, videos_saved)
                            VALUES
                              (:term, :type, :status, :added_at, :started_at, :completed_at, :videos_saved)
                        """),
                        {k: row[k] for k in ("term", "type", "status", "added_at",
                                              "started_at", "completed_at", "videos_saved")}
                    )
                    migrated += 1
            session.commit()
        print(f"Migrated {migrated} / {len(sqlite_rows)} terms from SQLite → PostgreSQL.")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

"""Database helpers."""

from __future__ import annotations

import psycopg

from .config import Config


def connect(cfg: Config) -> psycopg.Connection:
    conn = psycopg.connect(cfg.connection)
    if cfg.temp_tablespace:
        # Staging tables can run to several GB; keep them off the system disk
        # when the role is allowed to use the configured tablespace.
        usable = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_tablespace WHERE spcname = %s "
            "AND has_tablespace_privilege(oid, 'CREATE'))", (cfg.temp_tablespace,)).fetchone()[0]
        if usable:
            conn.execute("SELECT set_config('temp_tablespaces', %s, false)", (cfg.temp_tablespace,))
        conn.commit()
    return conn


def copy_rows(cur: psycopg.Cursor, table: str, columns: list[str], rows) -> int:
    """COPY an iterable of tuples into `table`; returns the number of rows."""
    n = 0
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
            n += 1
    return n

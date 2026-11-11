"""Operator commands: python -m ytstudio.cli <command>."""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from ytstudio.adapters.persistence.db import engine_for
from ytstudio.config import ROOT, get_settings


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def migrate(url: str | None = None) -> None:
    url = url or get_settings().database_url
    cfg = alembic_config(url)
    tables = set(inspect(engine_for(url)).get_table_names())
    if "channel" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "0001")  # database made by the pre-migration app: its schema is the baseline
    command.upgrade(cfg, "head")


def copy_sqlite(src: Path, dest_url: str) -> None:
    """One-off copy of an existing SQLite database into a freshly migrated Postgres."""
    from alembic.script import ScriptDirectory
    from sqlalchemy import MetaData, create_engine, text

    src_engine = create_engine(f"sqlite:///{src}")
    head = ScriptDirectory.from_config(alembic_config(dest_url)).get_current_head()
    with src_engine.connect() as c:
        version = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if version != head:  # an old file still has plain-text tokens and an older shape
        raise SystemExit(
            f"{src} is at migration {version}, not {head}. Run it first: "
            f"DATABASE_URL=sqlite:///{src} python -m ytstudio.cli migrate"
        )
    migrate(dest_url)
    dest = engine_for(dest_url)
    meta = MetaData()
    meta.reflect(bind=src_engine)
    with src_engine.connect() as s, dest.begin() as d:
        for table in meta.sorted_tables:
            if table.name == "alembic_version":
                continue
            rows = [dict(r._mapping) for r in s.execute(table.select())]
            if rows:
                d.execute(table.insert(), rows)
                print(f"{table.name}: {len(rows)} rows")
        for table in meta.sorted_tables:  # move Postgres sequences past the copied ids
            if "id" in table.c and str(table.c.id.type).startswith("INTEGER"):
                d.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', 'id'), "  # noqa: S608  (names from our own schema)
                        f'COALESCE((SELECT MAX(id) FROM "{table.name}"), 1))'
                    )
                )


def rotate_keys() -> None:
    """Re-encrypt every token with the first TOKEN_ENCRYPTION_KEY. Keep old keys listed until this ran."""
    from sqlmodel import select

    from ytstudio.adapters.persistence import tables as t
    from ytstudio.adapters.persistence.db import session_for
    from ytstudio.adapters.token_vault import TokenCipher

    s = get_settings()
    cipher = TokenCipher(s.token_encryption_key)
    with session_for(s.database_url) as db:
        rows = list(db.exec(select(t.Channel)))
        for row in rows:
            row.creds = cipher.rotate(row.creds)
            db.add(row)
        db.commit()
    print(f"Re-encrypted {len(rows)} channel token(s). You can now remove the old key.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="ytstudio")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create or upgrade the database schema")
    cp = sub.add_parser("copy-sqlite", help="copy ytstudio.db into the DATABASE_URL Postgres")
    cp.add_argument("src", type=Path)
    sub.add_parser("rotate-keys", help="re-encrypt tokens with the newest key")
    hp = sub.add_parser("health", help="exit 1 if a part is unhealthy (beat, worker_io, worker_llm, ...)")
    hp.add_argument("part", nargs="?", default="beat")
    a = p.parse_args(argv)
    if a.cmd == "migrate":
        migrate()
    elif a.cmd == "copy-sqlite":
        copy_sqlite(a.src, get_settings().database_url)
    elif a.cmd == "rotate-keys":
        rotate_keys()
    elif a.cmd == "health":
        from ytstudio.container import get_container

        report = get_container().health()
        print(report.get(a.part))
        sys.exit(0 if report.get(a.part, {}).get("ok") else 1)


if __name__ == "__main__":
    main()

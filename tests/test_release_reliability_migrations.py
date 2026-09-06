"""Exercise real legacy shapes, not the dynamic initial migration's current ORM."""

import sqlite3

from alembic import command
from alembic.config import Config


def test_disposition_upgrade_preserves_legacy_rows_and_adds_sqlite_references(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE requests(id INTEGER PRIMARY KEY);
            CREATE TABLE releases(id INTEGER PRIMARY KEY);
            CREATE TABLE staged_torrents(id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL);
            INSERT INTO alembic_version VALUES ('b7c8d9e0f1a2');
            INSERT INTO staged_torrents VALUES (1, 'Existing release');
            """
        )
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "c1d2e3f4a5b6")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,title,identity_override FROM staged_torrents").fetchall() == [
            (1, "Existing release", 0)
        ]
        assert db.execute("SELECT count(*) FROM release_dispositions").fetchone() == (0,)
        references = {
            row[3]: (row[2], row[6])
            for row in db.execute("PRAGMA foreign_key_list(staged_torrents)")
        }
        assert references["source_release_id"] == ("releases", "SET NULL")
        assert references["replaces_id"][0] == "staged_torrents"
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO releases VALUES (12)")
        db.execute("UPDATE staged_torrents SET source_release_id=12")
        db.execute("DELETE FROM releases WHERE id=12")
        assert db.execute("SELECT source_release_id FROM staged_torrents").fetchone() == (None,)
    command.downgrade(config, "b7c8d9e0f1a2")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM staged_torrents").fetchall() == [(1, "Existing release")]

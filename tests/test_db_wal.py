from app.services.memory import LinkStore
from app.services.usage import UsageStore


async def test_file_db_uses_wal(tmp_path):
    db = tmp_path / "q.db"
    store = LinkStore(db)
    UsageStore(db)
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    await store.ping()

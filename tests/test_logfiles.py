import logging

from app.logfiles import add_file_handler


def test_file_handler_writes_and_caps(tmp_path):
    root = logging.getLogger("t-logfiles")
    root.setLevel(logging.INFO)
    root.propagate = False
    path = add_file_handler(str(tmp_path / "logs"), "%(message)s", keep_days=2, max_mb=0, root=root)
    assert path is not None
    handler = root.handlers[-1]
    handler.max_bytes = 200
    try:
        for i in range(40):
            root.info("line %03d %s", i, "x" * 20)
    finally:
        handler.close()
        root.removeHandler(handler)
    files = sorted(p.name for p in path.parent.iterdir())
    assert "app.log" in files
    assert len(files) <= 3  # 当前 + 最多 keep_days 份备份
    assert "line 039" in path.read_text(encoding="utf-8")


def test_empty_dir_disables():
    assert add_file_handler("", "%(message)s") is None


def test_unwritable_dir_does_not_crash(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    assert add_file_handler(str(blocker / "sub"), "%(message)s") is None

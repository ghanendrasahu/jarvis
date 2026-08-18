
from app.core.media import background_path, list_backgrounds


def test_list_backgrounds_filters_and_url_encodes(tmp_path):
    (tmp_path / "galaxy.mp4").write_bytes(b"x")
    (tmp_path / "anime loop.webm").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("nope")

    items = list_backgrounds(tmp_path)
    names = [item["name"] for item in items]
    assert names == ["anime loop.webm", "galaxy.mp4"]
    assert items[0]["url"] == "/backgrounds/anime%20loop.webm"


def test_list_backgrounds_missing_dir(tmp_path):
    assert list_backgrounds(tmp_path / "does-not-exist") == []


def test_background_path_blocks_traversal(tmp_path):
    (tmp_path / "ok.mp4").write_bytes(b"x")
    assert background_path(tmp_path, "ok.mp4") == tmp_path / "ok.mp4"
    assert background_path(tmp_path, "..\\..\\secret.mp4") is None
    assert background_path(tmp_path, "sub/missing.mp4") is None
    assert background_path(tmp_path, "missing.mp4") is None

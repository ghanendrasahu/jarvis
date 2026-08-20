from app.core.media import background_image_path, list_background_images


def test_list_background_images_filters(tmp_path):
    (tmp_path / "nebula.png").write_bytes(b"x")
    (tmp_path / "loop.gif").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("nope")
    (tmp_path / "video.mp4").write_bytes(b"x")

    items = list_background_images(tmp_path)
    names = [item["name"] for item in items]
    assert names == ["loop.gif", "nebula.png"]


def test_list_background_images_missing_dir(tmp_path):
    assert list_background_images(tmp_path / "does-not-exist") == []


def test_background_image_path_blocks_traversal(tmp_path):
    (tmp_path / "ok.png").write_bytes(b"x")
    assert background_image_path(tmp_path, "ok.png") == tmp_path / "ok.png"
    assert background_image_path(tmp_path, "..\\..\\secret.png") is None
    assert background_image_path(tmp_path, "sub/missing.png") is None
    assert background_image_path(tmp_path, "missing.png") is None

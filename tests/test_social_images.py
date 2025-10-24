import articles
import pytest


def test_pick_random_social_image_requires_files(tmp_path, monkeypatch):
    """Selecting an image fails gracefully when the directory is empty."""

    monkeypatch.setattr(articles, "SOCIAL_IMAGE_DIR", tmp_path)

    with pytest.raises(articles.SocialImageError):
        articles._pick_random_social_image()

    image_path = tmp_path / "example.png"
    image_path.write_bytes(b"fake image data")
    (tmp_path / "ignore.txt").write_text("not an image")

    selected = articles._pick_random_social_image()
    assert selected == image_path

import articles
import pytest
import requests


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


def test_upload_twitter_media_network_error(tmp_path, monkeypatch):
    image_path = tmp_path / "example.png"
    image_path.write_bytes(b"fake image data")

    def fake_post(*args, **kwargs):
        raise requests.exceptions.ConnectionError("DNS failure")

    monkeypatch.setattr(articles.requests, "post", fake_post)

    with pytest.raises(articles.SocialPublishError) as exc:
        articles._upload_twitter_media(image_path)

    assert "Impossible de se connecter à X (Twitter) pour téléverser l'image" in str(
        exc.value
    )
    assert exc.value.status_code == 502


def test_publish_tweet_network_error(monkeypatch):
    monkeypatch.setattr(articles, "X_API_CONSUMER_KEY", "key")
    monkeypatch.setattr(articles, "X_API_CONSUMER_SECRET", "secret")
    monkeypatch.setattr(articles, "X_API_ACCESS_TOKEN", "token")
    monkeypatch.setattr(articles, "X_API_ACCESS_TOKEN_SECRET", "token-secret")
    monkeypatch.setattr(articles, "_build_oauth1_header", lambda method, url: "OAuth")

    def fake_post(*args, **kwargs):
        raise requests.exceptions.Timeout("Request timed out")

    monkeypatch.setattr(articles.requests, "post", fake_post)

    with pytest.raises(articles.SocialPublishError) as exc:
        articles._publish_tweet("hello world")

    assert "Impossible de se connecter à X (Twitter) pour publier le tweet" in str(
        exc.value
    )
    assert exc.value.status_code == 502

import articles


def test_generate_and_publish_tweet_retries_once(monkeypatch):
    selection = articles.Selection("Provider", "Certification")

    monkeypatch.setattr(
        articles,
        "generate_certification_tweet",
        lambda *args, **kwargs: "tweet test",
    )

    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return articles.SocialPostResult(
                text="tweet test", published=False, status_code=500, error="err1"
            )
        return articles.SocialPostResult(
            text="tweet test", published=True, status_code=200, response={"id": "ok"}
        )

    monkeypatch.setattr(articles, "_run_tweet_workflow", fake_run)

    _, result = articles._generate_and_publish_tweet_task(
        selection, "https://example.com", "career_impact", attach_image=False
    )

    assert result.published is True
    assert calls["count"] == 2


def test_generate_and_publish_linkedin_stops_after_one_retry(monkeypatch):
    selection = articles.Selection("Provider", "Certification")

    monkeypatch.setattr(
        articles,
        "generate_certification_linkedin_post",
        lambda *args, **kwargs: "linkedin test",
    )

    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        return articles.SocialPostResult(
            text="linkedin test", published=False, status_code=503, error=f"err{calls['count']}"
        )

    monkeypatch.setattr(articles, "_run_linkedin_workflow", fake_run)

    _, result = articles._generate_and_publish_linkedin_task(
        selection, "https://example.com", "career_impact", attach_image=False
    )

    assert calls["count"] == 2
    assert result.published is False
    assert "2 tentatives" in (result.error or "")

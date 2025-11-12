from flask import Flask

import articles


def _build_test_app():
    app = Flask(__name__)
    app.register_blueprint(articles.articles_bp, url_prefix="/articles")
    return app


def test_run_playbook_continues_when_exam_generation_fails(monkeypatch):
    app = _build_test_app()
    client = app.test_client()

    selection = articles.Selection("Provider", "Certification")
    monkeypatch.setattr(articles, "_fetch_selection", lambda *_: selection)

    def fail_examboot(_cert_id):
        raise articles.ExambootTestGenerationError("API Examboot indisponible")

    monkeypatch.setattr(
        articles,
        "_create_shareable_examboot_test",
        fail_examboot,
    )

    monkeypatch.setattr(
        articles,
        "_generate_and_persist_article_task",
        lambda *args, **kwargs: {
            "article": "Contenu généré",
            "blog_id": 42,
            "title": "Titre",
            "summary": "Résumé",
        },
    )

    tweet_result = articles.SocialPostResult(
        text="Tweet publié",
        published=True,
        status_code=200,
        response={"id": "1"},
    )
    linkedin_result = articles.SocialPostResult(
        text="LinkedIn publié",
        published=True,
        status_code=200,
        response={"id": "2"},
    )

    monkeypatch.setattr(
        articles,
        "_generate_and_publish_tweet_task",
        lambda *args, **kwargs: (tweet_result.text, tweet_result),
    )
    monkeypatch.setattr(
        articles,
        "_generate_and_publish_linkedin_task",
        lambda *args, **kwargs: (linkedin_result.text, linkedin_result),
    )

    payload = {
        "provider_id": 1,
        "certification_id": 2,
        "exam_url": "http://existant",
        "topic_type": "career_impact",
    }

    response = client.post("/articles/run-playbook", json=payload)
    assert response.status_code == 200
    data = response.get_json()

    assert data["exam_generated"] is False
    assert "Examboot" in data["exam_error"]
    assert data["tweet_published"] is True
    assert data["linkedin_published"] is True

    exam_step = next(step for step in data["playbook_steps"] if step["id"] == "exam")
    assert exam_step["success"] is False
    assert "Examboot" in exam_step["message"]

    tweet_step = next(step for step in data["playbook_steps"] if step["id"] == "tweet")
    assert tweet_step["success"] is True


def test_run_playbook_reports_article_failure_without_blocking_social(monkeypatch):
    app = _build_test_app()
    client = app.test_client()

    selection = articles.Selection("Provider", "Certification")
    monkeypatch.setattr(articles, "_fetch_selection", lambda *_: selection)
    monkeypatch.setattr(
        articles,
        "_create_shareable_examboot_test",
        lambda *_: "http://nouveau-test",
    )

    def fail_article(*_args, **_kwargs):
        raise RuntimeError("Base de données indisponible")

    monkeypatch.setattr(articles, "_generate_and_persist_article_task", fail_article)

    tweet_result = articles.SocialPostResult(
        text="Tweet contenu",
        published=True,
        status_code=200,
        response={"id": "10"},
    )
    linkedin_result = articles.SocialPostResult(
        text="LinkedIn contenu",
        published=True,
        status_code=200,
        response={"id": "11"},
    )

    monkeypatch.setattr(
        articles,
        "_generate_and_publish_tweet_task",
        lambda *args, **kwargs: (tweet_result.text, tweet_result),
    )
    monkeypatch.setattr(
        articles,
        "_generate_and_publish_linkedin_task",
        lambda *args, **kwargs: (linkedin_result.text, linkedin_result),
    )

    payload = {
        "provider_id": 5,
        "certification_id": 9,
        "exam_url": "http://existant",
        "topic_type": "experience_testimony",
    }

    response = client.post("/articles/run-playbook", json=payload)
    assert response.status_code == 200
    data = response.get_json()

    assert data["exam_generated"] is True
    assert data["article"] == ""
    assert data["article_error"] == "Base de données indisponible"
    assert data["tweet_published"] is True
    assert data["linkedin_published"] is True

    article_step = next(step for step in data["playbook_steps"] if step["id"] == "article")
    assert article_step["success"] is False
    assert "Base de données" in article_step["message"]

    tweet_step = next(step for step in data["playbook_steps"] if step["id"] == "tweet")
    assert tweet_step["success"] is True

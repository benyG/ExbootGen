from pathlib import Path

import articles
import pytest


def test_resolve_carousel_template_accepts_explicit_values(monkeypatch):
    monkeypatch.setattr(articles, "CAROUSEL_TEMPLATE_PATH", Path(__file__))
    monkeypatch.setattr(articles, "CAROUSEL_DARK_TEMPLATE_PATH", Path(__file__))

    template_name, template_path = articles._resolve_carousel_template("light")
    assert template_name == articles.CAROUSEL_TEMPLATE_LIGHT
    assert template_path == Path(__file__)

    template_name, template_path = articles._resolve_carousel_template("dark")
    assert template_name == articles.CAROUSEL_TEMPLATE_DARK
    assert template_path == Path(__file__)


def test_resolve_carousel_template_random_choice(monkeypatch):
    monkeypatch.setattr(articles, "CAROUSEL_TEMPLATE_PATH", Path(__file__))
    monkeypatch.setattr(articles, "CAROUSEL_DARK_TEMPLATE_PATH", Path(__file__))
    monkeypatch.setattr(articles.random, "choice", lambda values: values[1])

    template_name, _ = articles._resolve_carousel_template("random")
    assert template_name == articles.CAROUSEL_TEMPLATE_DARK


def test_resolve_carousel_template_invalid_value():
    with pytest.raises(ValueError):
        articles._resolve_carousel_template("invalid-template")

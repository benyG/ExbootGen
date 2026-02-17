from pathlib import Path

import articles
import fitz
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


def test_build_carousel_pdf_uses_page_specific_frame_rect(monkeypatch):
    class FakeTemplatePage:
        def __init__(self, index: int):
            self.index = index
            self.rect = fitz.Rect(0, 0, 100, 100)

    class FakeTemplateDoc:
        page_count = 6

        def load_page(self, idx: int):
            return FakeTemplatePage(idx)

        def close(self):
            return None

    class FakeOutputDoc:
        page_count = 6

        def insert_pdf(self, _template):
            return None

        def load_page(self, idx: int):
            return object()

        def save(self, _path):
            return None

        def close(self):
            return None

    template_doc = FakeTemplateDoc()
    output_doc = FakeOutputDoc()
    open_calls = {"count": 0}

    def fake_open(_path=None):
        open_calls["count"] += 1
        return template_doc if open_calls["count"] == 1 else output_doc

    seen_page_indexes = []
    subtext_rect_y = []

    def fake_find_frame(page):
        seen_page_indexes.append(page.index)
        # Rect varies by page index to ensure the builder recomputes per page.
        y_offset = page.index * 10
        return fitz.Rect(0, y_offset, 100, y_offset + 100)

    def fake_insert_text_block(_page, rect, text, **_kwargs):
        if text.startswith("s"):
            subtext_rect_y.append(rect.y0)

    monkeypatch.setattr(articles, "_resolve_carousel_template", lambda name=None: ("light", Path("dummy.pdf")))
    monkeypatch.setattr(articles, "_resolve_carousel_fonts", lambda: ("F", None, "F", None))
    monkeypatch.setattr(articles, "_find_carousel_frame_rect", fake_find_frame)
    monkeypatch.setattr(articles, "_insert_text_block", fake_insert_text_block)
    monkeypatch.setattr(articles.fitz, "open", fake_open)

    pages = [
        {"headline": f"h{i}", "subtext": f"s{i}", "key_message": f"k{i}"}
        for i in range(5)
    ]
    _, selected = articles._build_carousel_pdf(pages)

    assert selected == "light"
    assert seen_page_indexes == [0, 1, 2, 3, 4]
    assert len(set(subtext_rect_y)) == 5

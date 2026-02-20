from pathlib import Path

import articles


def test_run_linkedin_workflow_uses_document_media_for_carousel(monkeypatch, tmp_path):
    selection = articles.Selection("Provider", "Cert")
    pdf_path = tmp_path / "carousel.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(articles, "generate_certification_linkedin_post", lambda *args, **kwargs: "post")
    monkeypatch.setattr(articles, "_upload_linkedin_document", lambda _: "urn:li:digitalmediaAsset:doc123")

    called = {}

    def fake_publish(text, media_asset=None, media_category="IMAGE"):
        called["text"] = text
        called["media_asset"] = media_asset
        called["media_category"] = media_category
        return {"id": "ok"}

    monkeypatch.setattr(articles, "_publish_linkedin_post", fake_publish)

    result = articles._run_linkedin_workflow(
        selection,
        "https://example.test",
        "preparation_methodology",
        attach_image=True,
        linkedin_post="carousel post",
        document_path=pdf_path,
    )

    assert result.published is True
    assert result.media_filename == "carousel.pdf"
    assert called["text"] == "carousel post"
    assert called["media_asset"] == "urn:li:digitalmediaAsset:doc123"
    assert called["media_category"] == "DOCUMENT"

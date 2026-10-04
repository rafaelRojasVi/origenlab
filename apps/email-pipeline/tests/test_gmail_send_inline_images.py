from __future__ import annotations

from pathlib import Path

import pytest

from origenlab_email_pipeline.gmail_send import build_gmail_message_with_inline_images, extract_inline_images_from_html


def test_extract_inline_images_rewrites_src_and_collects_attachments(tmp_path: Path) -> None:
    html_dir = tmp_path
    assets = html_dir / "catalog_assets_premium"
    assets.mkdir(parents=True)

    (assets / "a.jpg").write_bytes(b"fake")
    (assets / "b.png").write_bytes(b"fake")

    html = """
    <html>
      <body>
        <img src="catalog_assets_premium/a.jpg">
        <img src='catalog_assets_premium/b.png'>
        <img src="https://example.com/remote.jpg">
      </body>
    </html>
    """

    rewritten, imgs = extract_inline_images_from_html(html, html_dir=html_dir)

    assert 'src="cid:a.jpg"' in rewritten
    assert 'src="cid:b.png"' in rewritten
    assert "https://example.com/remote.jpg" in rewritten

    cids = {i.cid for i in imgs}
    assert cids == {"a.jpg", "b.png"}


def test_build_gmail_message_sets_single_to_and_no_cc(tmp_path: Path) -> None:
    html = "<html><body><p>x</p></body></html>"
    msg, _ = build_gmail_message_with_inline_images(
        sender_email="OrigenLab <send@example.com>",
        to_emails="a@example.com",
        subject="Subj",
        html=html,
        html_dir=tmp_path,
    )
    assert msg["To"] == "a@example.com"
    assert "Cc" not in msg


def test_build_gmail_message_sets_multiple_to_and_cc(tmp_path: Path) -> None:
    html = "<html><body><p>x</p></body></html>"
    msg, _ = build_gmail_message_with_inline_images(
        sender_email="OrigenLab <send@example.com>",
        to_emails=["a@example.com", "b@example.com"],
        cc_emails=["cc@example.com"],
        subject="Subj",
        html=html,
        html_dir=tmp_path,
    )
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Cc"] == "cc@example.com"


def test_build_gmail_message_rejects_empty_to(tmp_path: Path) -> None:
    html = "<html><body><p>x</p></body></html>"
    with pytest.raises(ValueError, match="at least one"):
        build_gmail_message_with_inline_images(
            sender_email="send@example.com",
            to_emails=[],
            subject="Subj",
            html=html,
            html_dir=tmp_path,
        )



def test_build_gmail_message_sets_list_unsubscribe_when_given(tmp_path: Path) -> None:
    msg, _ = build_gmail_message_with_inline_images(
        sender_email="OrigenLab <send@example.com>",
        to_emails="a@example.com",
        subject="Subj",
        html="<html><body><p>x</p></body></html>",
        html_dir=tmp_path,
        list_unsubscribe="<mailto:send@example.com?subject=REMOVER>",
    )
    assert msg["List-Unsubscribe"] == "<mailto:send@example.com?subject=REMOVER>"


def test_build_gmail_message_has_no_list_unsubscribe_by_default(tmp_path: Path) -> None:
    msg, _ = build_gmail_message_with_inline_images(
        sender_email="OrigenLab <send@example.com>",
        to_emails="a@example.com",
        subject="Subj",
        html="<html><body><p>x</p></body></html>",
        html_dir=tmp_path,
    )
    assert "List-Unsubscribe" not in msg

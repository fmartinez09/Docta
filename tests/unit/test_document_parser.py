import pymupdf
import pytest

from docta_api.document_parser import (
    DocumentProcessingFailure,
    build_chunks,
)
from docta_api.models import DocumentVersionState
from docta_api.pymupdf_parser import PyMuPDFDocumentParser


def test_digital_pdf_preserves_page_provenance_and_builds_searchable_chunks() -> None:
    parser = PyMuPDFDocumentParser(max_pages=10)

    parsed = parser.parse(_pdf_bytes("ALPHAONLY731 first page", "Second page concept"))
    chunks = build_chunks(parsed, size_characters=400, overlap_characters=40)

    assert [page.number for page in parsed.pages] == [1, 2]
    assert parsed.pages[0].text == "ALPHAONLY731 first page"
    assert [(chunk.page, chunk.ordinal) for chunk in chunks] == [(1, 0), (2, 1)]
    assert all(len(chunk.content_hash) == 64 for chunk in chunks)


def test_parser_rejects_invalid_pdf() -> None:
    parser = PyMuPDFDocumentParser(max_pages=10)

    with pytest.raises(DocumentProcessingFailure) as captured:
        parser.parse(b"not a pdf")

    assert captured.value.code == "DOCUMENT_INVALID"
    assert captured.value.state == DocumentVersionState.REJECTED


def test_parser_rejects_textless_pdf_as_ocr_required() -> None:
    parser = PyMuPDFDocumentParser(max_pages=10)

    with pytest.raises(DocumentProcessingFailure) as captured:
        parser.parse(_pdf_bytes(""))

    assert captured.value.code == "DOCUMENT_OCR_REQUIRED"
    assert captured.value.state == DocumentVersionState.OCR_REQUIRED


def test_parser_rejects_encrypted_pdf_without_attempting_a_password() -> None:
    parser = PyMuPDFDocumentParser(max_pages=10)
    encrypted = _pdf_bytes("private", encrypted=True)

    with pytest.raises(DocumentProcessingFailure) as captured:
        parser.parse(encrypted)

    assert captured.value.code == "DOCUMENT_ENCRYPTED"
    assert captured.value.state == DocumentVersionState.REJECTED


def test_parser_enforces_page_limit() -> None:
    parser = PyMuPDFDocumentParser(max_pages=1)

    with pytest.raises(DocumentProcessingFailure) as captured:
        parser.parse(_pdf_bytes("one", "two"))

    assert captured.value.code == "DOCUMENT_TOO_MANY_PAGES"


def _pdf_bytes(*pages: str, encrypted: bool = False) -> bytes:
    document = pymupdf.open()
    try:
        for text in pages:
            page = document.new_page()
            if text:
                page.insert_text((72, 72), text)
        if encrypted:
            return document.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="owner-password",
                user_pw="user-password",
            )
        return document.tobytes()
    finally:
        document.close()

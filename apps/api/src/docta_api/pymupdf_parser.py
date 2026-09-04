import re
import unicodedata

import pymupdf

from docta_api.document_parser import (
    DocumentProcessingFailure,
    ParsedDocument,
    ParsedPage,
)
from docta_api.models import DocumentVersionState


class PyMuPDFDocumentParser:
    def __init__(self, *, max_pages: int) -> None:
        self._max_pages = max_pages

    def parse(self, content: bytes) -> ParsedDocument:
        if not content[:1024].lstrip().startswith(b"%PDF-"):
            raise DocumentProcessingFailure(
                "DOCUMENT_INVALID",
                DocumentVersionState.REJECTED,
            )
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
        except Exception as error:
            raise DocumentProcessingFailure(
                "DOCUMENT_INVALID",
                DocumentVersionState.REJECTED,
            ) from error

        try:
            if document.needs_pass or document.is_encrypted:
                raise DocumentProcessingFailure(
                    "DOCUMENT_ENCRYPTED",
                    DocumentVersionState.REJECTED,
                )
            if document.page_count == 0:
                raise DocumentProcessingFailure(
                    "DOCUMENT_INVALID",
                    DocumentVersionState.REJECTED,
                )
            if document.page_count > self._max_pages:
                raise DocumentProcessingFailure(
                    "DOCUMENT_TOO_MANY_PAGES",
                    DocumentVersionState.REJECTED,
                )

            pages = tuple(
                ParsedPage(
                    number=page.number + 1,
                    text=_normalize_text(page.get_text("text", sort=True)),
                )
                for page in document
            )
        except DocumentProcessingFailure:
            raise
        except Exception as error:
            raise DocumentProcessingFailure(
                "DOCUMENT_INVALID",
                DocumentVersionState.REJECTED,
            ) from error
        finally:
            document.close()

        if not any(page.text for page in pages):
            raise DocumentProcessingFailure(
                "DOCUMENT_OCR_REQUIRED",
                DocumentVersionState.OCR_REQUIRED,
            )
        return ParsedDocument(
            pages=pages,
            parser_version=f"pymupdf-{pymupdf.__version__}",
        )


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    normalized = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", normalized)
    normalized = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()

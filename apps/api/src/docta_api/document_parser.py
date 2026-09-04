from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from docta_api.models import DocumentVersionState


@dataclass(frozen=True)
class ParsedPage:
    number: int
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    pages: tuple[ParsedPage, ...]
    parser_version: str


@dataclass(frozen=True)
class ParsedChunk:
    page: int
    ordinal: int
    content: str
    content_hash: str


class DocumentProcessingFailure(Exception):
    def __init__(self, code: str, state: DocumentVersionState) -> None:
        super().__init__(code)
        self.code = code
        self.state = state


class DocumentParser(Protocol):
    def parse(self, content: bytes) -> ParsedDocument: ...


def build_chunks(
    document: ParsedDocument,
    *,
    size_characters: int,
    overlap_characters: int,
) -> tuple[ParsedChunk, ...]:
    chunks: list[ParsedChunk] = []
    ordinal = 0
    for page in document.pages:
        start = 0
        while start < len(page.text):
            proposed_end = min(start + size_characters, len(page.text))
            end = proposed_end
            if proposed_end < len(page.text):
                break_at = page.text.rfind(" ", start + size_characters // 2, proposed_end)
                if break_at > start:
                    end = break_at
            content = page.text[start:end].strip()
            if content:
                chunks.append(
                    ParsedChunk(
                        page=page.number,
                        ordinal=ordinal,
                        content=content,
                        content_hash=sha256(content.encode("utf-8")).hexdigest(),
                    )
                )
                ordinal += 1
            if end >= len(page.text):
                break
            next_start = max(end - overlap_characters, start + 1)
            start = next_start
    if not chunks:
        raise DocumentProcessingFailure(
            "DOCUMENT_OCR_REQUIRED",
            DocumentVersionState.OCR_REQUIRED,
        )
    return tuple(chunks)

"""The cited specification pages, indexed, and a verifier for quotes taken from them.

Every finding cites one of four HL7 pages. ``corpus/SOURCES.json`` maps those
URLs to retained copies. This module turns each page into heading-bounded
passages a model can be shown and checks that a quote the model attributes to
a page actually occurs in it. The check is a pure function over committed
files: the page is the evidence, the model is only the narrator.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

MIN_QUOTE_CHARS = 24
PASSAGE_TARGET_CHARS = 900
PASSAGE_MAX_CHARS = 1600
_HEADINGS = frozenset({"h1", "h2", "h3", "h4"})
_BLOCKS = frozenset(
    {"p", "div", "li", "ul", "ol", "tr", "td", "th", "table", "pre", "br", "section", "dd", "dt"}
)
_SKIP = frozenset({"script", "style", "noscript", "head", "title", "svg"})


class CorpusError(ValueError):
    """The corpus could not be indexed as committed."""


@dataclass(frozen=True)
class Passage:
    passage_id: str
    source_id: str
    index: int
    heading: str
    text: str


@dataclass(frozen=True)
class Document:
    source_id: str
    label: str
    citation_urls: tuple[str, ...]
    local_copy: str
    passages: tuple[Passage, ...]
    normalized: str


@dataclass(frozen=True)
class QuoteMatch:
    source_id: str
    quote: str
    passage_id: str | None


class _Sectioner(HTMLParser):
    """Collect (heading, text) sections from an HL7 page, breaking lines at blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str, str]] = []
        self._heading = ""
        self._buffer: list[str] = []
        self._capture_heading: list[str] | None = None
        self._skip = 0

    def _flush(self) -> None:
        text = _tidy("".join(self._buffer))
        if text:
            self.sections.append((self._heading, text))
        self._buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag in _HEADINGS and not self._skip:
            self._flush()
            self._capture_heading = []
        elif tag in _BLOCKS:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _HEADINGS and self._capture_heading is not None:
            self._heading = " ".join("".join(self._capture_heading).split())
            self._capture_heading = None
        elif tag in _BLOCKS:
            self._buffer.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._capture_heading is not None:
            self._capture_heading.append(data)
        else:
            self._buffer.append(data)

    def finish(self) -> list[tuple[str, str]]:
        self.close()
        self._flush()
        return self.sections


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_sections(markup: str) -> list[tuple[str, str]]:
    parser = _Sectioner()
    parser.feed(markup)
    sections = parser.finish()
    if not sections:
        raise CorpusError("HTML document has no text content")
    return sections


_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark
        "\u201c": '"',  # left double quotation mark
        "\u201d": '"',  # right double quotation mark
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u00a0": " ",  # no-break space
        "\u00ad": "",  # soft hyphen
    }
)


def normalize_for_match(text: str) -> str:
    """Reduce text to the characters that carry meaning for a verbatim check."""
    folded = unicodedata.normalize("NFKC", text).translate(_QUOTE_MAP).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def _chunk(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [paragraph] if len(paragraph) <= PASSAGE_MAX_CHARS else _split_long(paragraph)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) > PASSAGE_TARGET_CHARS and current:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _split_long(paragraph: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.;:])\s+|\n", paragraph):
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > PASSAGE_TARGET_CHARS and current:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def split_passages(source_id: str, sections: list[tuple[str, str]]) -> tuple[Passage, ...]:
    passages: list[Passage] = []
    for heading, body in sections:
        for chunk in _chunk(body):
            passages.append(
                Passage(f"{source_id}#{len(passages)}", source_id, len(passages), heading, chunk)
            )
    return tuple(passages)


class CorpusIndex:
    """Documents keyed by source ID, citation URLs mapped to them, and a verifier."""

    def __init__(self, documents: dict[str, Document], not_retained: dict[str, str]) -> None:
        self.documents = documents
        self.not_retained = not_retained
        self._by_url = {
            url: doc.source_id for doc in documents.values() for url in doc.citation_urls
        }

    @classmethod
    def load(cls, root: Path) -> CorpusIndex:
        manifest_path = root / "corpus" / "SOURCES.json"
        try:
            manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CorpusError(f"cannot read {manifest_path}: {exc}") from exc
        documents: dict[str, Document] = {}
        for entry in manifest.get("sources", []):
            source_id = str(entry["source_id"])
            path = root / str(entry["local_copy"])
            if not path.is_file():
                raise CorpusError(f"{source_id}: local copy missing at {path}")
            if str(entry.get("format", "")) != "html":
                raise CorpusError(f"{source_id}: unsupported format {entry.get('format')!r}")
            sections = html_sections(path.read_text(encoding="utf-8", errors="replace"))
            documents[source_id] = Document(
                source_id=source_id,
                label=str(entry.get("label", source_id)),
                citation_urls=tuple(str(u) for u in entry.get("citation_urls", [])),
                local_copy=str(entry["local_copy"]),
                passages=split_passages(source_id, sections),
                normalized=normalize_for_match("\n".join(f"{h}\n{b}" for h, b in sections)),
            )
        if not documents:
            raise CorpusError("corpus manifest lists no sources")
        not_retained = {
            str(item["citation_url"]): str(item.get("reason", ""))
            for item in manifest.get("not_retained", [])
        }
        return cls(documents, not_retained)

    def source_for_url(self, url: str) -> str | None:
        return self._by_url.get(url)

    def passages_for(self, source_ids: list[str] | tuple[str, ...]) -> list[Passage]:
        result: list[Passage] = []
        for source_id in source_ids:
            document = self.documents.get(source_id)
            if document:
                result.extend(document.passages)
        return result

    def passage(self, passage_id: str) -> Passage | None:
        source_id, _, index = passage_id.partition("#")
        document = self.documents.get(source_id)
        if document is None or not index.isdigit() or int(index) >= len(document.passages):
            return None
        return document.passages[int(index)]

    def verify_quote(self, source_id: str, quote: str) -> QuoteMatch | None:
        """Where ``quote`` occurs verbatim in the named page, or ``None``.

        Checked against the whole page, not the passage shown, so a faithful
        quote across a passage boundary still verifies and a passage ID alone
        can never vouch for text.
        """
        document = self.documents.get(source_id)
        if document is None:
            return None
        needle = normalize_for_match(quote)
        if len(needle) < MIN_QUOTE_CHARS or needle not in document.normalized:
            return None
        for passage in document.passages:
            if needle in normalize_for_match(passage.text):
                return QuoteMatch(source_id, quote, passage.passage_id)
        return QuoteMatch(source_id, quote, None)

    def summary(self) -> dict[str, Any]:
        return {
            source_id: {
                "label": doc.label,
                "local_copy": doc.local_copy,
                "passages": len(doc.passages),
                "characters": len(doc.normalized),
            }
            for source_id, doc in sorted(self.documents.items())
        }

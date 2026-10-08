from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

import pdfplumber
from docx import Document
from docx.oxml.ns import qn
from pdfplumber.utils import extract_text
from pydantic import BaseModel, Field


class HiddenSpan(BaseModel):
    text: str
    reason: str


class ExtractedResume(BaseModel):
    text_all: str = ""
    text_visible: str = ""
    hidden_spans: list[HiddenSpan] = Field(default_factory=list)
    page_count: int = 0
    file_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def _near_white(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return float(value) >= 0.94
    if isinstance(value, (list, tuple)) and value:
        try:
            channels = [float(channel) for channel in value]
        except (TypeError, ValueError):
            return False
        scale = 255.0 if max(channels) > 1.0 else 1.0
        rgb = channels[:3] if len(channels) >= 3 else channels * 3
        return min(rgb) / scale >= 0.94
    return False


def _chars_text(chars: list[dict[str, Any]]) -> str:
    if not chars:
        return ""
    try:
        return extract_text(chars, x_tolerance=3, y_tolerance=3).strip()
    except Exception:
        return "".join(str(char.get("text", "")) for char in chars).strip()


def _append_spans(spans: list[HiddenSpan], grouped: dict[str, list[dict[str, Any]]]) -> None:
    for reason, chars in grouped.items():
        text = _chars_text(chars)
        if text:
            spans.append(HiddenSpan(text=text, reason=reason))


def _pdf_reason(
    char: dict[str, Any],
    page_width: float,
    page_height: float,
    seen: set[tuple[str, float, float, float, float]],
) -> str | None:
    text = str(char.get("text", ""))
    x0 = float(char.get("x0", 0) or 0)
    x1 = float(char.get("x1", x0) or x0)
    top = float(char.get("top", 0) or 0)
    bottom = float(char.get("bottom", top) or top)
    key = (text, round(x0, 1), round(x1, 1), round(top, 1), round(bottom, 1))
    duplicate = bool(text.strip()) and key in seen
    seen.add(key)

    if _near_white(char.get("non_stroking_color")):
        return "near-white text"
    if float(char.get("size", 0) or 0) < 2 and text.strip():
        return "font size below 2pt"
    if x1 < 0 or x0 > page_width or bottom < 0 or top > page_height:
        return "text outside page bounds"
    if text.strip() and abs(x1 - x0) < 0.05:
        return "zero-width text"
    if duplicate:
        return "overlapping duplicate text"
    return None


def _extract_pdf(data: bytes) -> ExtractedResume:
    spans: list[HiddenSpan] = []
    all_pages: list[str] = []
    visible_pages: list[str] = []
    metadata: dict[str, Any] = {}
    with pdfplumber.open(BytesIO(data)) as pdf:
        raw_metadata = pdf.metadata or {}
        metadata = {str(key): str(value) for key, value in raw_metadata.items() if value is not None}
        for page in pdf.pages:
            all_chars = list(page.chars)
            visible_chars: list[dict[str, Any]] = []
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            seen: set[tuple[str, float, float, float, float]] = set()
            for char in all_chars:
                reason = _pdf_reason(char, float(page.width), float(page.height), seen)
                if reason:
                    grouped[reason].append(char)
                else:
                    visible_chars.append(char)
            _append_spans(spans, grouped)
            all_pages.append(_chars_text(all_chars))
            visible_pages.append(_chars_text(visible_chars))
        return ExtractedResume(
            text_all="\n\n".join(part for part in all_pages if part).strip(),
            text_visible="\n\n".join(part for part in visible_pages if part).strip(),
            hidden_spans=spans,
            page_count=len(pdf.pages),
            file_type="pdf",
            metadata=metadata,
        )


def _docx_run_reason(run_element: Any) -> str | None:
    properties = run_element.find(qn("w:rPr"))
    if properties is not None:
        vanish = properties.find(qn("w:vanish"))
        if vanish is not None and vanish.get(qn("w:val"), "1").lower() not in {"0", "false", "off"}:
            return "vanished text"
        color = properties.find(qn("w:color"))
        if color is not None:
            raw = color.get(qn("w:val"), "").lstrip("#")
            if len(raw) == 6:
                try:
                    if min(int(raw[index : index + 2], 16) for index in (0, 2, 4)) >= 240:
                        return "near-white text"
                except ValueError:
                    pass
        size = properties.find(qn("w:sz"))
        if size is not None:
            try:
                if float(size.get(qn("w:val"), "0")) / 2 < 2:
                    return "font size below 2pt"
            except ValueError:
                pass

    node = run_element
    while node is not None:
        tag = str(getattr(node, "tag", ""))
        if tag.endswith("}txbxContent"):
            ancestor = node.getparent()
            while ancestor is not None:
                style = ancestor.get("style", "").lower()
                if any(marker in style for marker in ("left:-", "margin-left:-", "top:-", "margin-top:-")):
                    return "text in off-page text box"
                for descendant in ancestor.iter():
                    if str(getattr(descendant, "tag", "")).endswith("}off"):
                        try:
                            if int(descendant.get("x", "0")) < 0 or int(descendant.get("y", "0")) < 0:
                                return "text in off-page text box"
                        except ValueError:
                            pass
                ancestor = ancestor.getparent()
            break
        node = node.getparent()
    return None


def _run_text(run_element: Any) -> str:
    pieces: list[str] = []
    for node in run_element.iter():
        if node.tag == qn("w:t"):
            pieces.append(node.text or "")
        elif node.tag == qn("w:tab"):
            pieces.append("\t")
        elif node.tag in {qn("w:br"), qn("w:cr")}:
            pieces.append("\n")
    return "".join(pieces)


def _nearest_paragraph(node: Any) -> Any:
    parent = node.getparent()
    while parent is not None and parent.tag != qn("w:p"):
        parent = parent.getparent()
    return parent


def _extract_docx(data: bytes) -> ExtractedResume:
    document = Document(BytesIO(data))
    spans: list[HiddenSpan] = []
    all_lines: list[str] = []
    visible_lines: list[str] = []
    roots = [document.element.body]
    roots.extend(section.header._element for section in document.sections)
    roots.extend(section.footer._element for section in document.sections)

    seen_paragraphs: set[Any] = set()
    for root in roots:
        for paragraph in root.iter(qn("w:p")):
            if paragraph in seen_paragraphs:
                continue
            seen_paragraphs.add(paragraph)
            all_parts: list[str] = []
            visible_parts: list[str] = []
            hidden_by_reason: dict[str, list[str]] = defaultdict(list)
            for run in paragraph.iter(qn("w:r")):
                if _nearest_paragraph(run) is not paragraph:
                    continue
                text = _run_text(run)
                if not text:
                    continue
                all_parts.append(text)
                reason = _docx_run_reason(run)
                if reason:
                    hidden_by_reason[reason].append(text)
                else:
                    visible_parts.append(text)
            all_line = "".join(all_parts).strip()
            visible_line = "".join(visible_parts).strip()
            if all_line:
                all_lines.append(all_line)
            if visible_line:
                visible_lines.append(visible_line)
            for reason, pieces in hidden_by_reason.items():
                hidden_text = "".join(pieces).strip()
                if hidden_text:
                    spans.append(HiddenSpan(text=hidden_text, reason=reason))

    properties = document.core_properties
    metadata = {
        key: str(value)
        for key, value in {
            "title": properties.title,
            "author": properties.author,
            "subject": properties.subject,
            "keywords": properties.keywords,
        }.items()
        if value
    }
    return ExtractedResume(
        text_all="\n".join(all_lines).strip(),
        text_visible="\n".join(visible_lines).strip(),
        hidden_spans=spans,
        page_count=max(1, len(document.sections)),
        file_type="docx",
        metadata=metadata,
    )


def extract_resume(data: bytes, filename: str) -> ExtractedResume:
    suffix = Path(filename).suffix.lower()
    file_type = suffix.lstrip(".") or "unknown"
    if not data:
        return ExtractedResume(file_type=file_type, metadata={"error": "empty file"})
    try:
        if suffix == ".pdf":
            return _extract_pdf(data)
        if suffix == ".docx":
            return _extract_docx(data)
        if suffix == ".txt":
            text = data.decode("utf-8", errors="replace")
            return ExtractedResume(
                text_all=text,
                text_visible=text,
                page_count=1,
                file_type="txt",
                metadata={},
            )
        return ExtractedResume(file_type=file_type, metadata={"error": f"unsupported file type: {file_type}"})
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        return ExtractedResume(file_type=file_type, metadata={"error": message[:300]})

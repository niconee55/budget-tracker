from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Iterable

from .config import ParserDefinition
from .models import EmailEnvelope, Transaction

AMOUNT_RE = re.compile(r"(?<!\d)(?:USD\s*)?\$?\s*(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?)(?!\d)")
SIGNED_AMOUNT_RE = re.compile(r"(?P<amount>[-+]?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")
LAST4_RE = re.compile(r"(?:ending in|last\s*4(?:\s*digits)?|card)\D{0,12}(?P<last4>\d{4})", re.IGNORECASE)
DATE_PATTERNS = [
    re.compile(r"(?P<date>\b\d{4}-\d{2}-\d{2}\b)"),
    re.compile(r"(?P<date>\b\d{1,2}/\d{1,2}/\d{2,4}\b)"),
    re.compile(
        r"(?P<date>\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b)",
        re.IGNORECASE,
    ),
]
TRANSACTION_HINTS = [
    "purchase",
    "spent",
    "charged",
    "transaction",
    "payment",
    "deposit",
    "direct deposit",
    "posted",
]


@dataclass(slots=True)
class ParsedFields:
    date_text: str | None
    merchant: str
    amount: float
    last4: str | None
    snippet: str


def iter_email_files(root: Path, extensions: Iterable[str] | None = None) -> list[Path]:
    allowed = {ext.lower() for ext in (extensions or [".eml", ".txt", ".email"])}
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in allowed]
    return sorted(files)


def load_envelope(path: Path) -> EmailEnvelope:
    raw_bytes = path.read_bytes()
    if path.suffix.lower() == ".eml":
        message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        subject = _message_header(message, "subject")
        from_address = _message_header(message, "from")
        body_text = _extract_message_body(message)
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        return EmailEnvelope(path, subject, from_address, body_text, raw_text)

    raw_text = raw_bytes.decode("utf-8", errors="replace")
    parsed_message = _parse_plain_text_message(raw_text)
    if parsed_message is not None:
        subject = _message_header(parsed_message, "subject")
        from_address = _message_header(parsed_message, "from")
        body_text = _extract_message_body(parsed_message)
        return EmailEnvelope(path, subject, from_address, body_text, raw_text)
    return EmailEnvelope(path, "", "", raw_text, raw_text)


def parse_transaction(envelope: EmailEnvelope, parser_definitions: list[ParserDefinition]) -> Transaction | None:
    for parser in parser_definitions:
        if not _parser_matches(parser, envelope):
            continue
        fields = _extract_fields(envelope, parser)
        if fields is not None:
            return _build_transaction(envelope, parser.name, fields)

    fields = _extract_fields(envelope, None)
    if fields is None:
        return None
    return _build_transaction(envelope, "generic", fields)


def _build_transaction(envelope: EmailEnvelope, parser_name: str, fields: ParsedFields) -> Transaction:
    transaction_date = _parse_date(fields.date_text) if fields.date_text else None
    return Transaction(
        transaction_date=transaction_date,
        subject=envelope.subject,
        merchant=fields.merchant,
        amount=fields.amount,
        last4=fields.last4,
        source_file=envelope.source_path.name,
        snippet=fields.snippet,
        parser_name=parser_name,
    )


def _parser_matches(parser: ParserDefinition, envelope: EmailEnvelope) -> bool:
    haystack = "\n".join([envelope.subject, envelope.from_address, envelope.body_text]).lower()
    for key, values in parser.match.items():
        if key == "subject_contains" and not all(value in envelope.subject.lower() for value in values):
            return False
        if key == "from_contains" and not all(value in envelope.from_address.lower() for value in values):
            return False
        if key == "body_contains" and not all(value in haystack for value in values):
            return False
    return True


def _extract_fields(envelope: EmailEnvelope, parser: ParserDefinition | None) -> ParsedFields | None:
    body_lines = [line.strip() for line in envelope.body_text.splitlines() if line.strip()]
    searchable = "\n".join([envelope.subject, envelope.body_text])
    snippet = _pick_snippet(body_lines, envelope.body_text)

    date_text = _find_pattern(searchable, parser.field_patterns.get("date", []) if parser else []) if parser else None
    amount_text = _find_pattern(searchable, parser.field_patterns.get("amount", []) if parser else [])
    merchant = _find_pattern(searchable, parser.field_patterns.get("merchant", []) if parser else [])
    last4 = _find_pattern(searchable, parser.field_patterns.get("last4", []) if parser else [])

    if amount_text is None:
        amount_text = _find_amount(body_lines, searchable)
    if merchant is None:
        merchant = _find_merchant(body_lines, searchable, amount_text)
    if date_text is None:
        date_text = _find_date(searchable)
    if last4 is None:
        last4 = _find_last4(searchable)

    amount = _parse_amount(amount_text) if amount_text else None
    if amount is None or not merchant:
        return None
    return ParsedFields(
        date_text=date_text,
        merchant=merchant,
        amount=amount,
        last4=last4,
        snippet=snippet,
    )


def _find_pattern(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if not match:
            continue
        if "amount" in match.groupdict():
            return match.group("amount")
        if "merchant" in match.groupdict():
            return match.group("merchant")
        if "date" in match.groupdict():
            return match.group("date")
        if "last4" in match.groupdict():
            return match.group("last4")
        if match.groups():
            return match.group(1)
        return match.group(0)
    return None


def _find_amount(lines: list[str], searchable: str) -> str | None:
    for line in lines:
        if "$" not in line and not any(hint in line.lower() for hint in TRANSACTION_HINTS):
            continue
        match = AMOUNT_RE.search(line)
        if match:
            return match.group("amount")
        match = SIGNED_AMOUNT_RE.search(line)
        if match:
            return match.group("amount")
    match = AMOUNT_RE.search(searchable)
    if match:
        return match.group("amount")
    match = SIGNED_AMOUNT_RE.search(searchable)
    if match:
        return match.group("amount")
    return None


def _find_merchant(lines: list[str], searchable: str, amount_text: str | None) -> str:
    anchored_patterns = [
        re.compile(
            r"(?:purchase\s+at|spent\s+at|charged\s+by|payment\s+to|transaction\s+at|merchant)\s*[:\-]?\s*(?P<merchant>[A-Za-z0-9&.'\/\- ]{2,})",
            re.IGNORECASE,
        ),
        re.compile(r"(?:at|to|with|via|from)\s+(?P<merchant>[A-Za-z0-9&.'\/\- ]{2,})", re.IGNORECASE),
        re.compile(
            r"(?:your\s+)?(?P<merchant>[A-Za-z0-9&.'\/\- ]{2,})\s+(?:receipt|transaction|confirmation|statement)",
            re.IGNORECASE,
        ),
    ]
    for pattern in anchored_patterns:
        match = pattern.search(searchable)
        if match:
            candidate = match.group("merchant").strip(" -:;,")
            candidate = _clean_merchant(candidate)
            if candidate:
                return candidate

    for line in lines:
        if amount_text and amount_text in line:
            cleaned = _clean_merchant(line)
            if cleaned:
                return cleaned

    for line in lines:
        lowered = line.lower()
        if any(token in lowered for token in ("thank you", "statement", "available balance", "payment due", "due date")):
            continue
        if AMOUNT_RE.search(line) or SIGNED_AMOUNT_RE.search(line):
            candidate = _clean_merchant(line)
            if candidate:
                return candidate

    if lines:
        candidate = _clean_merchant(lines[0])
        if candidate:
            return candidate
    return "Unknown merchant"


def _find_date(text: str) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group("date")
    return None


def _find_last4(text: str) -> str | None:
    match = LAST4_RE.search(text)
    return match.group("last4") if match else None


def _pick_snippet(lines: list[str], text: str) -> str:
    for line in lines:
        if "$" in line or any(hint in line.lower() for hint in TRANSACTION_HINTS):
            return line[:240]
    compact = " ".join(text.split())
    return compact[:240]


def _parse_plain_text_message(raw_text: str) -> Message | None:
    if "subject:" not in raw_text.lower() and "from:" not in raw_text.lower():
        return None
    return BytesParser(policy=policy.default).parsebytes(raw_text.encode("utf-8", errors="ignore"))


def _message_header(message: Message, header_name: str) -> str:
    value = message.get(header_name, "")
    return str(value).strip()


def _extract_message_body(message: Message) -> str:
    if message.is_multipart():
        parts = []
        for part in message.walk():
            if part.get_content_maintype() != "text":
                continue
            if part.get_content_disposition() not in (None, "inline"):
                continue
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                payload_text = part.get_payload()
                if isinstance(payload_text, str):
                    parts.append(_strip_html(payload_text))
                continue
            parts.append(payload.decode(charset, errors="replace"))
        return "\n".join(part.strip() for part in parts if part.strip())

    payload = message.get_payload(decode=True)
    if payload is not None:
        charset = message.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    payload_text = message.get_payload()
    if isinstance(payload_text, str):
        return _strip_html(payload_text)
    return ""


def _strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_merchant(text: str) -> str:
    text = re.sub(r"[\s]+", " ", text).strip()
    text = re.sub(r"^(?:merchant|purchase|transaction|payment|charge|at|to|from)\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:your|thanks|thank you|receipt|transaction|purchase|alert|confirmation|statement)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:receipt|transaction|purchase|alert|confirmation|statement)$", "", text, flags=re.IGNORECASE)
    text = text.strip(" -:;,.")
    if len(text) < 2:
        return ""
    return text


def _parse_amount(amount_text: str) -> float | None:
    if amount_text is None:
        return None
    cleaned = amount_text.replace("$", "").replace(",", "").replace(" ", "")
    cleaned = cleaned.lstrip("+")
    cleaned = cleaned.lstrip("-")
    try:
        return round(abs(float(cleaned)), 2)
    except ValueError:
        return None


def _parse_date(date_text: str) -> datetime.date | None:
    if not date_text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(date_text, fmt).date()
        except ValueError:
            continue
    return None

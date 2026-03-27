from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from .config import load_email_sources


AMOUNT_FALLBACK_RE = re.compile(r"(-?\$?[0-9][0-9,]*\.[0-9]{2})")
DATE_FALLBACK_RE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
LAST4_FALLBACK_RE = re.compile(r"(?:ending in|card ending in|last 4|last4)\s*(?:[:#-]?\s*)?(\d{4})", re.IGNORECASE)
MERCHANT_FALLBACK_PATTERNS = [
    re.compile(
        r"(?:purchase|transaction|payment|charge|charged)\s+(?:at|from|with)\s+([A-Za-z0-9 '&./-]{2,60})(?=$|\n)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:purchase|transaction|payment|charge|charged)\D{0,40}(?:at|from|with)\s+([A-Za-z0-9 '&./-]{2,60}?)(?=\s+on\s+|\s+for\s+|\s+card\s+ending\s+in\s+|[.,]|$)",
        re.IGNORECASE,
    ),
    re.compile(r"merchant\s*[:|-]\s*([A-Za-z0-9 '&./-]{2,60})", re.IGNORECASE),
    re.compile(r"description\s*[:|-]\s*([A-Za-z0-9 '&./-]{2,60})", re.IGNORECASE),
]


@dataclass(slots=True)
class ParsedEmail:
    source_name: str
    date: str
    merchant: str
    amount: Decimal
    account_last4: str | None
    raw_snippet: str
    source_file: str

    @property
    def last4(self) -> str | None:
        return self.account_last4

    @property
    def category(self) -> str:
        from .categorizer import TransactionCategorizer

        return TransactionCategorizer().categorize(self).category


def iter_email_files(input_dir: Path) -> list[Path]:
    return sorted(
        path for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".eml", ".txt"}
    )


def parse_email_file(path: Path) -> dict[str, str]:
    if path.suffix.lower() == ".eml":
        return parse_email_bytes(path.read_bytes())
    return parse_email_text(path.read_text())


def parse_email_bytes(raw_bytes: bytes) -> dict[str, str]:
    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    body = _extract_body_from_message(message)
    return {
        "sender": message.get("From", ""),
        "subject": message.get("Subject", ""),
        "date_header": message.get("Date", ""),
        "body": body,
    }


def parse_email_text(text: str) -> dict[str, str]:
    headers, body = _split_headers(text)
    searchable_text = body or text
    return {
        "sender": headers.get("From", ""),
        "subject": headers.get("Subject", ""),
        "date_header": headers.get("Date", ""),
        "body": searchable_text,
    }


def extract_transactions(input_dir: Path) -> list[ParsedEmail]:
    sources = load_email_sources()["sources"]
    parsed: list[ParsedEmail] = []
    for file_path in iter_email_files(input_dir):
        email_data = parse_email_file(file_path)
        transaction = extract_transaction_from_email_data(email_data, file_path.name, sources)
        if transaction is not None:
            parsed.append(transaction)
    return parsed


def parse_transaction_file(path: Path) -> ParsedEmail:
    if path.is_dir():
        raise IsADirectoryError(path)
    parent = path.parent
    matches = [item for item in extract_transactions(parent) if item.source_file == path.name]
    if not matches:
        raise FileNotFoundError(path)
    return matches[0]


def extract_transaction_from_email_data(
    email_data: dict[str, str],
    source_file: str,
    sources: list[dict[str, Any]] | None = None,
) -> ParsedEmail | None:
    source_catalog = sources or load_email_sources()["sources"]
    source = match_source(email_data, source_catalog)
    amount_text = _first_match(source.get("amount_patterns", []), email_data["body"]) or _first_regex(AMOUNT_FALLBACK_RE, email_data["body"])
    merchant = _first_match(source.get("merchant_patterns", []), email_data["body"])
    if not merchant:
        merchant = _first_regex_list(MERCHANT_FALLBACK_PATTERNS, email_data["body"])
    date_text = _first_match(source.get("date_patterns", []), email_data["body"])
    if not date_text:
        date_text = _parse_date_header(email_data["date_header"]) or _first_regex(DATE_FALLBACK_RE, email_data["body"])
    last4 = _first_match(source.get("last4_patterns", []), email_data["body"]) or _first_regex(LAST4_FALLBACK_RE, email_data["body"])
    if not amount_text or not merchant or not date_text:
        return None
    amount = _parse_amount(amount_text)
    if amount is None:
        return None
    return ParsedEmail(
        source_name=source["name"],
        date=_normalize_date(date_text),
        merchant=_clean_value(merchant),
        amount=amount,
        account_last4=last4,
        raw_snippet=_clip(email_data["body"]),
        source_file=source_file,
    )


def match_source(email_data: dict[str, str], sources: list[dict[str, object]]) -> dict[str, object]:
    generic_source = next(source for source in sources if source["name"] == "generic")
    for source in sources:
        if source["name"] == "generic":
            continue
        if _matches_any(source.get("sender_patterns", []), email_data["sender"]) and _matches_any(source.get("subject_patterns", []), email_data["subject"]):
            return source
    return generic_source


def _extract_body_from_message(message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() != "text":
                continue
            if part.get_content_disposition() not in (None, "inline"):
                continue
            content_type = part.get_content_type()
            content = _part_content(part)
            if not content:
                continue
            if content_type == "text/plain":
                plain_parts.append(content)
            elif content_type == "text/html":
                html_parts.append(_strip_html(content))
        plain_body = "\n".join(part.strip() for part in plain_parts if part.strip())
        html_body = "\n".join(part.strip() for part in html_parts if part.strip())
        if plain_body and html_body:
            return _prefer_richer_body(plain_body, html_body)
        if plain_body:
            return plain_body
        if html_body:
            return html_body
        return ""

    content = _part_content(message)
    if not content:
        return ""
    if message.get_content_type() == "text/html":
        return _strip_html(content)
    return content


def _split_headers(text: str) -> tuple[dict[str, str], str]:
    raw_headers, _, body = text.partition("\n\n")
    headers: dict[str, str] = {}
    for line in raw_headers.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers, body


def _matches_any(patterns: list[str], value: str) -> bool:
    if not patterns:
        return True
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)


def _first_match(patterns: list[str], body: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            value = _matched_group(match)
            if value:
                return value
    return None


def _first_regex(pattern: re.Pattern[str], body: str) -> str | None:
    match = pattern.search(body)
    return _matched_group(match) if match else None


def _first_regex_list(patterns: list[re.Pattern[str]], body: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(body)
        if match:
            value = _matched_group(match)
            if value:
                return value
    return None


def _matched_group(match: re.Match[str]) -> str | None:
    groupdict = match.groupdict()
    for key in ("merchant", "amount", "date", "last4"):
        value = groupdict.get(key)
        if value:
            return value
    for value in match.groups():
        if value:
            return value
    return match.group(0) if match.group(0) else None


def _parse_date_header(value: str) -> str | None:
    try:
        return datetime.strptime(value[:25], "%a, %d %b %Y %H:%M:%S").date().isoformat()
    except ValueError:
        return None


def _normalize_date(value: str) -> str:
    cleaned = value.strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return cleaned


def _parse_amount(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "").replace("$", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _clean_value(value: str) -> str:
    return " ".join(value.strip().rstrip(".,;:").split())


def _clip(value: str, limit: int = 240) -> str:
    single_line = " ".join(value.split())
    return single_line[:limit]


def _part_content(part) -> str:
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = part.get_payload()
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        if isinstance(payload, str):
            content = payload
        else:
            return ""
    if isinstance(content, bytes):
        charset = part.get_content_charset() or "utf-8"
        return content.decode(charset, errors="replace")
    return content.strip() if isinstance(content, str) else ""


def _strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|table|h[1-6])>", "\n", text)
    text = re.sub(r"(?i)<td[^>]*>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _prefer_richer_body(primary: str, alternate: str) -> str:
    primary_score = _body_richness_score(primary)
    alternate_score = _body_richness_score(alternate)
    if alternate_score > primary_score:
        return alternate
    return primary


def _body_richness_score(text: str) -> int:
    patterns = [
        re.compile(r"merchant\s*[:|-][ \t\xa0]*[^\s\r\n]", re.IGNORECASE),
        re.compile(r"date\s*[:|-][ \t\xa0]*[^\s\r\n]", re.IGNORECASE),
        re.compile(r"amount\s*[:|-][ \t\xa0]*\$?[^\s\r\n]", re.IGNORECASE),
        re.compile(r"(?:last 4|ending in|account ending in)\s*(?:[:#-]?|#:\s*)[ \t\xa0]*\d{4}", re.IGNORECASE),
    ]
    return sum(1 for pattern in patterns if pattern.search(text))

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

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None


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
    plain_body, html_body = _extract_bodies_from_message(message)
    body = _prefer_message_body(plain_body, html_body)
    return {
        "sender": message.get("From", ""),
        "subject": message.get("Subject", ""),
        "date_header": message.get("Date", ""),
        "body": body,
        "html_body": html_body,
    }


def parse_email_text(text: str) -> dict[str, str]:
    headers, body = _split_headers(text)
    searchable_text = body or text
    return {
        "sender": headers.get("From", ""),
        "subject": headers.get("Subject", ""),
        "date_header": headers.get("Date", ""),
        "body": searchable_text,
        "html_body": "",
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
    if _should_ignore_email(email_data, source):
        return None
    body = _source_body(email_data, source)
    if source.get("name") == "venmo":
        return _extract_venmo_transaction(email_data, source_file, source)
    amount_text = _first_match(source.get("amount_patterns", []), body) or _first_regex(AMOUNT_FALLBACK_RE, body)
    merchant = _first_match(source.get("merchant_patterns", []), body)
    if not merchant:
        merchant = _first_regex_list(MERCHANT_FALLBACK_PATTERNS, body)
    merchant = _normalize_source_merchant(merchant, source)
    date_text = _first_match(source.get("date_patterns", []), body)
    if not date_text:
        date_text = _parse_date_header(email_data["date_header"]) or _first_regex(DATE_FALLBACK_RE, body)
    last4 = _first_match(source.get("last4_patterns", []), body) or _first_regex(LAST4_FALLBACK_RE, body)
    if not merchant:
        merchant = _default_merchant_for_source(source)
    if not amount_text or not merchant or not date_text:
        return None
    amount = _parse_amount(amount_text)
    if amount is None:
        return None
    subject = email_data.get("subject", "")
    snippet_prefix = _subject_snippet_prefix(subject)
    snippet_source = " ".join(part for part in [snippet_prefix, body] if part)
    return ParsedEmail(
        source_name=_effective_source_name(email_data, source),
        date=_normalize_date(date_text),
        merchant=_clean_value(merchant),
        amount=amount,
        account_last4=last4,
        raw_snippet=_clip(snippet_source),
        source_file=source_file,
    )


def _extract_venmo_transaction(
    email_data: dict[str, str],
    source_file: str,
    source: dict[str, Any],
) -> ParsedEmail | None:
    body = email_data.get("body", "")
    subject = email_data.get("subject", "")
    combined = "\n".join(part for part in [subject, body] if part)
    amount_text = _first_match(source.get("amount_patterns", []), combined) or _first_regex(AMOUNT_FALLBACK_RE, combined)
    date_text = _first_match(source.get("date_patterns", []), body)
    if not date_text:
        date_text = _parse_date_header(email_data.get("date_header", "")) or _first_regex(DATE_FALLBACK_RE, body)
    if not amount_text or not date_text:
        return None

    amount = _parse_amount(amount_text)
    if amount is None:
        return None

    direction, counterpart = _parse_venmo_direction(subject, body)
    note = _extract_venmo_note(subject, body)
    merchant = _choose_venmo_merchant(direction, counterpart, note)
    source_name = source["name"]
    if direction == "incoming":
        source_name = "venmo_incoming"
    elif direction == "outgoing":
        source_name = "venmo_outgoing"
    snippet_parts = [subject]
    if note:
        snippet_parts.append(note)
    snippet_parts.append(body)
    return ParsedEmail(
        source_name=source_name,
        date=_normalize_date(date_text),
        merchant=_clean_value(merchant),
        amount=amount,
        account_last4=None,
        raw_snippet=_clip(" ".join(part for part in snippet_parts if part)),
        source_file=source_file,
    )


def match_source(email_data: dict[str, str], sources: list[dict[str, object]]) -> dict[str, object]:
    generic_source = next(source for source in sources if source["name"] == "generic")
    for source in sources:
        if source["name"] == "generic":
            continue
        if not _matches_any(source.get("sender_patterns", []), email_data["sender"]):
            continue
        if _matches_source_content(email_data, source):
            return source
    return generic_source


def _should_ignore_email(email_data: dict[str, str], source: dict[str, object]) -> bool:
    subject = email_data.get("subject", "").lower()
    body = email_data.get("body", "").lower()
    searchable_body = "\n".join(
        part.lower()
        for part in [email_data.get("body", ""), _strip_html(email_data.get("html_body", ""))]
        if part
    )
    searchable_email = "\n".join(part for part in [subject, searchable_body] if part)
    if _is_ignored_apple_monthly_charge(searchable_email):
        return True
    if source.get("name") == "venmo" and "transaction history" in subject:
        return True
    if "wealthfront brokerage llc" in body and any(
        token in body for token in ("transfer", "deposited to", "transferred has been deposited", "instant payment")
    ):
        return True
    if source.get("name") == "capital_one_placeholder":
        if "synergy fi" in searchable_body:
            return True
    if source.get("name") == "capital_one_withdrawal":
        if "venmo has initiated the following withdrawal" in searchable_body:
            return True
        if "discover has initiated the following withdrawal" in searchable_body:
            return True
        if _contains_amount(searchable_body, Decimal("2700.00")):
            return True
    if source.get("name") == "discover":
        if "new statement online" in subject or "paperless statement is ready" in body:
            return True
        if "received your payment" in subject or "thanks for your payment" in body:
            return True
    return False


def _extract_bodies_from_message(message) -> tuple[str, str]:
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
                html_parts.append(content)
        plain_body = "\n".join(part.strip() for part in plain_parts if part.strip())
        html_body = "\n".join(part.strip() for part in html_parts if part.strip())
        return plain_body, html_body

    content = _part_content(message)
    if not content:
        return "", ""
    if message.get_content_type() == "text/html":
        return "", content
    return content, ""


def _prefer_message_body(plain_body: str, html_body: str) -> str:
    stripped_html = _strip_html(html_body) if html_body else ""
    if plain_body and stripped_html:
        return _prefer_richer_body(plain_body, stripped_html)
    if plain_body:
        return plain_body
    return stripped_html


def _source_body(email_data: dict[str, str], source: dict[str, Any]) -> str:
    html_body = email_data.get("html_body", "")
    selectors: list[str] = []
    body_selectors = source.get("body_selectors", [])
    if isinstance(body_selectors, list):
        selectors.extend(str(item) for item in body_selectors)
    content_block_selector = source.get("content_block_selector")
    if content_block_selector:
        selectors.append(str(content_block_selector))
    selected_body = _extract_html_selectors_text(html_body, selectors)
    if selected_body:
        return selected_body
    return email_data["body"]


def _extract_html_selectors_text(html: str, selectors: list[str]) -> str:
    if not html or not selectors or BeautifulSoup is None:
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""
    for selector in selectors:
        try:
            nodes = soup.select(selector)
        except Exception:
            continue
        texts = [" ".join(node.get_text(" ", strip=True).split()) for node in nodes if node.get_text(" ", strip=True)]
        if texts:
            return _clip(" ".join(texts), limit=500)
    return ""


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


def _matches_source_content(email_data: dict[str, str], source: dict[str, object]) -> bool:
    subject_patterns = source.get("subject_patterns", [])
    body_patterns = source.get("body_match_patterns", [])
    subject_matches = _matches_any(subject_patterns, email_data.get("subject", "")) if subject_patterns else False
    body_text = "\n".join(
        part for part in [email_data.get("body", ""), _strip_html(email_data.get("html_body", ""))] if part
    )
    body_matches = _matches_any(body_patterns, body_text) if body_patterns else False
    if subject_patterns or body_patterns:
        return subject_matches or body_matches
    return True


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


def _parse_venmo_direction(subject: str, body: str) -> tuple[str | None, str | None]:
    subject_matchers = [
        (re.compile(r"^(?P<counterpart>.+?) paid you \$?[0-9][0-9,]*\.[0-9]{2}$", re.IGNORECASE), "incoming"),
        (re.compile(r"^you paid (?P<counterpart>.+?) \$?[0-9][0-9,]*\.[0-9]{2}$", re.IGNORECASE), "outgoing"),
        (re.compile(r"^payment from (?P<counterpart>.+?) for \$?[0-9][0-9,]*\.[0-9]{2}$", re.IGNORECASE), "incoming"),
    ]
    cleaned_subject = " ".join(subject.split())
    for pattern, direction in subject_matchers:
        match = pattern.search(cleaned_subject)
        if match:
            return direction, _clean_value(match.group("counterpart"))

    body_matchers = [
        (re.compile(r"(?P<counterpart>[A-Za-z0-9 '&./-]{2,80}?) paid you \$?[0-9][0-9,]*\.[0-9]{2}", re.IGNORECASE), "incoming"),
        (re.compile(r"you paid (?P<counterpart>[A-Za-z0-9 '&./-]{2,80}?) \$?[0-9][0-9,]*\.[0-9]{2}", re.IGNORECASE), "outgoing"),
        (re.compile(r"payment from (?P<counterpart>[A-Za-z0-9 '&./-]{2,80}?) for \$?[0-9][0-9,]*\.[0-9]{2}", re.IGNORECASE), "incoming"),
    ]
    for pattern, direction in body_matchers:
        match = pattern.search(body)
        if match:
            return direction, _clean_value(match.group("counterpart"))

    lowered_body = body.lower()
    if "paid you" in lowered_body:
        return "incoming", None
    if "you paid" in lowered_body:
        return "outgoing", None
    return None, None


def _extract_venmo_note(subject: str, body: str) -> str | None:
    collapsed_subject = " ".join(subject.split())
    lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines()]
    skip_prefixes = (
        "see transaction",
        "transaction details",
        "date ",
        "status ",
        "transaction id",
        "payment method",
        "sent from",
        "sent to",
        "money credited",
        "for any issues",
        "contact us",
        "venmo is a service",
        "this payment will",
        "if you don't recognize",
        "please do not reply",
    )
    for line in lines:
        if not line:
            continue
        lowered = line.lower()
        if lowered == collapsed_subject.lower():
            continue
        compact = re.sub(r"\s+", "", lowered)
        if compact == re.sub(r"\s+", "", collapsed_subject.lower()):
            continue
        if any(lowered.startswith(prefix) for prefix in skip_prefixes):
            continue
        if "transaction history" in lowered:
            continue
        if line.count("$") or re.fullmatch(r"[A-Za-z0-9_@.\- ]+\$?\s*[0-9 .]+!?", line):
            continue
        return line
    return None


def _choose_venmo_merchant(direction: str | None, counterpart: str | None, note: str | None) -> str:
    if direction == "outgoing" and note:
        return note
    if counterpart:
        return counterpart
    if note:
        return note
    return "Venmo"


def _parse_amount(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "").replace("$", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _contains_amount(value: str, expected: Decimal) -> bool:
    for match in AMOUNT_FALLBACK_RE.finditer(value):
        amount = _parse_amount(match.group(1))
        if amount == expected:
            return True
    return False


def _is_ignored_apple_monthly_charge(value: str) -> bool:
    normalized = value.lower()
    return "apple" in normalized and _contains_amount(normalized, Decimal("0.99"))


def _clean_value(value: str) -> str:
    return " ".join(value.strip().rstrip(".,;:").split())


def _clip(value: str, limit: int = 240) -> str:
    single_line = " ".join(value.split())
    return single_line[:limit]


def _subject_snippet_prefix(subject: str) -> str:
    cleaned = subject.strip()
    if not cleaned:
        return ""
    if cleaned == "Capital One Purchase Alert":
        return "Capital One purchase alert"
    return cleaned


def _default_merchant_for_source(source: dict[str, Any]) -> str | None:
    source_name = str(source.get("name", ""))
    if source_name == "capital_one_deposit":
        return "Direct Deposit"
    if source_name == "capital_one_withdrawal":
        return "Withdrawal"
    return None


def _normalize_source_merchant(merchant: str | None, source: dict[str, Any]) -> str | None:
    if not merchant:
        return None
    source_name = str(source.get("name", ""))
    cleaned = _clean_value(merchant)
    if source_name == "capital_one_deposit" and cleaned.lower() in {"deposit", "direct deposit", "payroll deposit"}:
        return _default_merchant_for_source(source)
    return cleaned


def _effective_source_name(email_data: dict[str, str], source: dict[str, Any]) -> str:
    source_name = str(source.get("name", "generic"))
    if source_name != "generic":
        return source_name
    sender = str(email_data.get("sender", "")).lower()
    if any(token in sender for token in ("discover@services.discover.com", "services.discover.com", "discover.com")):
        return "discover"
    if any(token in sender for token in ("capitalone.com", "captialone.com", "capital one")):
        return "capital_one"
    if "venmo.com" in sender:
        return "venmo"
    return source_name


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

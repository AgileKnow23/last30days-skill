"""Investment-topic contract: ``<Company Name> $<TICKER> <short research objective>``.

Pure helpers with no engine imports, so an external adapter (an investment
workflow that calls the engine) can validate and format topics before it
spends a single request. The generic retrieval engine only consults this
module from the CLI layer, for an opt-in strict check and a non-blocking
advisory; nothing here changes how a non-financial topic is researched.

Why the shape matters: entity grounding keys on the topic's first token
(``rerank._primary_entity`` / ``_entity_grounded``), so a ticker-first topic
demotes every community post that names the company but not the symbol, and
StockTwits trusts an explicit cashtag while a bare company name goes through
a symbol search that can miss (``stocktwits.detect_symbols``). Detailed
research angles belong in the
``--plan`` subqueries, never in the topic: the Reddit lane scores relevance
against the raw topic's tokens, so generic finance vocabulary in the topic
(``earnings guidance backlog outlook``) pulls in off-entity posts about other
companies' earnings, whereas a thesis-shaped objective keeps entity precision
intact. The price is a thin YouTube lane, which searches the raw topic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Z])?)\b")
_BARE_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_ANGLE_LIST_RE = re.compile(r":|;|\bvs\.?\b|(?:,\s*[^,]+){3,}", re.IGNORECASE)

MAX_COMPANY_WORDS = 6
MAX_OBJECTIVE_WORDS = 12
DEFAULT_OBJECTIVE = "material developments affecting the investment thesis"

EXAMPLES: dict[str, str] = {
    "GNRC": "Generac $GNRC material developments affecting the investment thesis",
    "FCEL": "FuelCell Energy $FCEL material developments affecting the investment thesis",
    "WHR": "Whirlpool $WHR material developments affecting the investment thesis",
}


@dataclass(frozen=True)
class InvestmentTopic:
    """A topic that satisfies the contract, split into its three parts."""

    company: str
    ticker: str
    objective: str

    def render(self) -> str:
        return format_investment_topic(self.company, self.ticker, self.objective)


@dataclass(frozen=True)
class InvestmentTopicCheck:
    """Validation outcome. ``problems`` are actionable, one sentence each."""

    ok: bool
    topic: InvestmentTopic | None
    problems: tuple[str, ...]
    suggestion: str | None

    def message(self) -> str:
        lines = [f"Investment topic does not follow '<Company Name> $<TICKER> <short research objective>':"]
        lines.extend(f"  - {problem}" for problem in self.problems)
        if self.suggestion:
            lines.append(f"  Suggested topic: {self.suggestion}")
        lines.append(f"  Example: {EXAMPLES['GNRC']}")
        return "\n".join(lines)


def format_investment_topic(company: str, ticker: str, objective: str = DEFAULT_OBJECTIVE) -> str:
    """Render the canonical topic string; ``ticker`` may be given with or without ``$``."""
    symbol = ticker.strip().lstrip("$").upper()
    parts = [company.strip(), f"${symbol}", objective.strip()]
    return " ".join(part for part in parts if part)


def looks_ticker_first(topic: str) -> bool:
    """True when the topic leads with the symbol rather than the company name.

    A cashtag head (``$WHR Whirlpool``) always counts. A bare all-caps head
    is read as a ticker only when the topic offers no other reading: it has
    no cashtag at all (``WHR Whirlpool earnings``), or the head names the same
    symbol as a cashtag that comes after other words (``WHR Whirlpool $WHR``).
    A head that is immediately followed by its own cashtag (``AMD $AMD``) or
    that differs from the cashtag (``US Bancorp $USB``) is a company name that
    happens to be short and upper-case, and it satisfies the contract.
    """
    tokens = topic.split()
    if not tokens:
        return False
    head = tokens[0].strip(",.:;")
    if CASHTAG_RE.fullmatch(head):
        return True
    if not _BARE_TICKER_RE.fullmatch(head):
        return False
    cashtags = [symbol.upper() for symbol in CASHTAG_RE.findall(topic)]
    if not cashtags:
        return True
    if len(tokens) > 1 and CASHTAG_RE.fullmatch(tokens[1].strip(",.:;")):
        return False
    return head.upper() in cashtags


def parse_investment_topic(topic: str) -> InvestmentTopic | None:
    """Split ``<company> $<TICKER> <objective>``; None when there is no cashtag
    or nothing precedes it."""
    match = CASHTAG_RE.search(topic)
    if not match:
        return None
    company = topic[: match.start()].strip(" ,:;-")
    if not company:
        return None
    objective = topic[match.end():].strip(" ,:;-")
    return InvestmentTopic(company=company, ticker=match.group(1).upper(), objective=objective)


def validate_investment_topic(topic: str, *, require_cashtag: bool = True) -> InvestmentTopicCheck:
    """Check a topic against the contract and explain every violation."""
    text = " ".join(topic.split())
    problems: list[str] = []
    cashtags = CASHTAG_RE.findall(text)
    parsed = parse_investment_topic(text)

    if looks_ticker_first(text):
        if cashtags:
            problems.append(
                "starts with the ticker; put the company name first so entity grounding "
                "keys on the company, not the symbol"
            )
        else:
            head = text.split()[0].strip(",.:;")
            problems.append(
                f"starts with the bare ticker '{head}' and has no cashtag; put the company name "
                f"first and the symbol as a cashtag (Company ${head} ...; when the name is the "
                f"symbol, {head} ${head} ...)"
            )
    if not cashtags:
        if require_cashtag:
            problems.append(
                "no $TICKER cashtag; without one StockTwits falls back to a name search "
                "that can miss the symbol"
            )
    elif len(cashtags) > 1:
        problems.append("more than one cashtag; research one company per topic")
    elif parsed is None:
        problems.append("nothing precedes the cashtag; the company name must come first")

    if parsed is not None:
        company_words = len(parsed.company.split())
        if company_words > MAX_COMPANY_WORDS:
            problems.append(
                f"company name is {company_words} words; keep it to the name itself "
                f"(at most {MAX_COMPANY_WORDS} words)"
            )
        objective_words = len(parsed.objective.split())
        if objective_words > MAX_OBJECTIVE_WORDS:
            problems.append(
                f"research objective is {objective_words} words (max {MAX_OBJECTIVE_WORDS}); "
                "keep the topic short and put the research angles in the --plan subqueries"
            )
        if parsed.objective and _ANGLE_LIST_RE.search(parsed.objective):
            problems.append(
                "objective reads like a list of research angles; move the angles "
                "(earnings, guidance, backlog, ...) into the --plan subqueries"
            )
    elif _ANGLE_LIST_RE.search(text):
        problems.append(
            "topic reads like a list of research angles; move the angles into the --plan subqueries"
        )

    suggestion = _suggest(text, parsed, cashtags) if problems else None
    return InvestmentTopicCheck(
        ok=not problems,
        topic=parsed if not problems else None,
        problems=tuple(problems),
        suggestion=suggestion,
    )


def _suggest(text: str, parsed: InvestmentTopic | None, cashtags: list[str]) -> str | None:
    """Best-effort canonical rewrite for the error message; never applied silently.

    Only offered when the topic already names a symbol (a cashtag or a leading
    bare ticker); a ticker is never invented from a company name.
    """
    if parsed is not None and not looks_ticker_first(text):
        objective = parsed.objective if parsed.objective and len(parsed.objective.split()) <= MAX_OBJECTIVE_WORDS \
            and not _ANGLE_LIST_RE.search(parsed.objective) else DEFAULT_OBJECTIVE
        return format_investment_topic(parsed.company, parsed.ticker, objective)
    tokens = text.split()
    if not tokens or (not cashtags and not looks_ticker_first(text)):
        return None
    ticker = cashtags[0].upper() if cashtags else tokens[0].strip("$,.:;").upper()
    rest = tokens[1:] if looks_ticker_first(text) else tokens
    company_words: list[str] = []
    for token in rest:
        cleaned = token.strip(",.:;")
        if not cleaned or CASHTAG_RE.fullmatch(cleaned) or _BARE_TICKER_RE.fullmatch(cleaned):
            continue
        if cleaned[0].isupper() and len(company_words) < MAX_COMPANY_WORDS:
            company_words.append(cleaned)
            continue
        break
    company = " ".join(company_words) or "<Company Name>"
    if not _BARE_TICKER_RE.fullmatch(ticker.replace(".", "")):
        return None
    return format_investment_topic(company, ticker, DEFAULT_OBJECTIVE)

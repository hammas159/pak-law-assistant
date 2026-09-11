"""Pakistani legal citations: parsing, normalisation, formatting.

Legal writing is unusually citation-dense and unusually precise about it. "Section 302"
alone is meaningless — 302 of *what* — and the same provision appears in half a dozen
written forms:

    Section 302 PPC
    s. 302 of the Pakistan Penal Code, 1860
    sec 302, P.P.C.
    §302 PPC

They must all resolve to the same key, or a retrieval system silently treats them as
different provisions and an answer citing one will not match a query naming another.

Three families are handled, because Pakistani legal argument uses all three and they
behave differently:

  **statutory**    a provision of an Act or the Constitution
  **subordinate**  an SRO, notification or rule made under an Act
  **reported**     a judgment, cited by law report — PLD, SCMR, CLC, YLR

Reported citations matter because a statute's *meaning* frequently lives in the case
law rather than the text, and a system that can parse the text but not the case citation
cannot follow the argument.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Common statute abbreviations, normalised to a canonical key.
STATUTES: dict[str, str] = {
    "ppc": "PPC",
    "p.p.c.": "PPC",
    "pakistan penal code": "PPC",
    "crpc": "CrPC",
    "cr.p.c.": "CrPC",
    "code of criminal procedure": "CrPC",
    "cpc": "CPC",
    "c.p.c.": "CPC",
    "code of civil procedure": "CPC",
    "qso": "QSO",
    "qanun-e-shahadat": "QSO",
    "qanun e shahadat": "QSO",
    "constitution": "CONST",
    "constitution of pakistan": "CONST",
    "companies act": "COMPANIES",
    "income tax ordinance": "ITO",
    "sales tax act": "STA",
    "contract act": "CONTRACT",
    "specific relief act": "SRA",
    "limitation act": "LIMITATION",
    "pdpa": "PDPA",
    "personal data protection act": "PDPA",
    "peca": "PECA",
    "prevention of electronic crimes act": "PECA",
}

# Law reports used in Pakistani practice.
REPORTS = {"PLD", "SCMR", "CLC", "YLR", "MLD", "PTD", "PLC", "CLD", "PCrLJ", "NLR"}

_STATUTE_ALTERNATION = "|".join(sorted((re.escape(k) for k in STATUTES), key=len, reverse=True))

# "Section 302 PPC", "s. 302 of the Pakistan Penal Code", "§302 PPC"
_SECTION = re.compile(
    r"(?:section|sec\.?|s\.|§)\s*"
    r"(?P<number>\d+[A-Z]{0,2}(?:\(\d+\))?(?:\([a-z]\))?)"
    r"(?:\s*,?\s*(?:of\s+the\s+)?(?P<statute>" + _STATUTE_ALTERNATION + r"))?",
    re.I,
)

# "Article 25 of the Constitution", "Art. 199"
_ARTICLE = re.compile(
    r"(?:article|art\.?)\s*"
    r"(?P<number>\d+[A-Z]{0,2}(?:\(\d+\))?(?:\([a-z]\))?)"
    r"(?:\s*,?\s*(?:of\s+the\s+)?(?P<statute>constitution(?:\s+of\s+pakistan)?))?",
    re.I,
)

# "Order XXXIX Rule 1 CPC" - civil procedure is cited by Order and Rule, not section.
_ORDER_RULE = re.compile(
    r"order\s+(?P<order>[IVXLC]+)\s*,?\s*rule\s+(?P<rule>\d+)"
    r"(?:\s*,?\s*(?:of\s+the\s+)?(?P<statute>cpc|c\.p\.c\.|code of civil procedure))?",
    re.I,
)

# "SRO 1125(I)/2011" - subordinate legislation.
_SRO = re.compile(
    r"s\.?r\.?o\.?\s*(?P<number>\d+)\s*(?:\((?P<series>[IVX]+)\))?\s*/\s*(?P<year>\d{4})", re.I
)

# "PLD 2015 SC 401", "2019 SCMR 1234" - both orderings occur in practice.
_REPORT_A = re.compile(
    r"\b(?P<report>" + "|".join(REPORTS) + r")\s+(?P<year>\d{4})\s+"
    r"(?P<court>SC|LHC|SHC|PHC|BHC|FSC|Lah|Kar|Pesh|Quetta)?\s*(?P<page>\d+)\b"
)
_REPORT_B = re.compile(
    r"\b(?P<year>\d{4})\s+(?P<report>" + "|".join(REPORTS) + r")\s+"
    r"(?P<court>SC|LHC|SHC|PHC|BHC|FSC)?\s*(?P<page>\d+)\b"
)


@dataclass(frozen=True)
class Citation:
    kind: str  # "statutory" | "subordinate" | "reported"
    statute: str = ""  # canonical key, e.g. "PPC"
    provision: str = ""  # "302", "25", "XXXIX/1"
    unit: str = ""  # "section" | "article" | "rule"
    report: str = ""
    year: str = ""
    court: str = ""
    page: str = ""
    raw: str = ""

    @property
    def key(self) -> str:
        """Canonical identity. Every written form of one provision maps here."""
        if self.kind == "reported":
            return f"{self.report}:{self.year}:{self.court or '-'}:{self.page}"
        if self.kind == "subordinate":
            return f"SRO:{self.provision}:{self.year}"
        return f"{self.statute or 'UNKNOWN'}:{self.unit}:{self.provision}"

    def pretty(self) -> str:
        if self.kind == "reported":
            return " ".join(x for x in (self.report, self.year, self.court, self.page) if x)
        if self.kind == "subordinate":
            return f"SRO {self.provision}/{self.year}"
        label = {"section": "Section", "article": "Article", "rule": "Rule"}.get(
            self.unit, self.unit.title()
        )
        return f"{label} {self.provision} {self.statute}".strip()


def _canonical_statute(text: str | None, default: str = "") -> str:
    """Resolve any written form of a statute name to its canonical key.

    Both the dotted and undotted forms are looked up, because abbreviations appear
    either way and the table holds only one of them: stripping the trailing dot turns
    the key "p.p.c." into "p.p.c" and silently loses the match, leaving the provision
    attributed to no Act at all.
    """
    if not text:
        return default
    cleaned = text.strip().lower()
    return (
        STATUTES.get(cleaned)
        or STATUTES.get(cleaned.rstrip("."))
        or STATUTES.get(cleaned + ".")
        or default
    )


def parse(text: str) -> list[Citation]:
    """Every citation in a passage, in order of appearance.

    Overlaps are resolved by consuming matched spans: "Order XXXIX Rule 1 CPC" must not
    also yield a bare rule, or one citation becomes two and the count of authorities in
    an answer is wrong.
    """
    found: list[tuple[int, Citation]] = []
    consumed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and end > s for s, e in consumed)

    for match in _ORDER_RULE.finditer(text):
        consumed.append(match.span())
        found.append(
            (
                match.start(),
                Citation(
                    kind="statutory",
                    statute=_canonical_statute(match.group("statute"), "CPC"),
                    provision=f"{match.group('order').upper()}/{match.group('rule')}",
                    unit="rule",
                    raw=match.group(0),
                ),
            )
        )

    for match in _SRO.finditer(text):
        if overlaps(*match.span()):
            continue
        consumed.append(match.span())
        found.append(
            (
                match.start(),
                Citation(
                    kind="subordinate",
                    provision=match.group("number"),
                    year=match.group("year"),
                    raw=match.group(0),
                ),
            )
        )

    for pattern in (_REPORT_A, _REPORT_B):
        for match in pattern.finditer(text):
            if overlaps(*match.span()):
                continue
            consumed.append(match.span())
            found.append(
                (
                    match.start(),
                    Citation(
                        kind="reported",
                        report=match.group("report").upper(),
                        year=match.group("year"),
                        court=(match.group("court") or "").upper(),
                        page=match.group("page"),
                        raw=match.group(0),
                    ),
                )
            )

    for match in _ARTICLE.finditer(text):
        if overlaps(*match.span()):
            continue
        consumed.append(match.span())
        found.append(
            (
                match.start(),
                Citation(
                    kind="statutory",
                    statute="CONST",
                    provision=match.group("number").upper(),
                    unit="article",
                    raw=match.group(0),
                ),
            )
        )

    for match in _SECTION.finditer(text):
        if overlaps(*match.span()):
            continue
        consumed.append(match.span())
        found.append(
            (
                match.start(),
                Citation(
                    kind="statutory",
                    statute=_canonical_statute(match.group("statute")),
                    provision=match.group("number").upper(),
                    unit="section",
                    raw=match.group(0),
                ),
            )
        )

    return [citation for _, citation in sorted(found, key=lambda pair: pair[0])]


def resolve_bare(citations: list[Citation], *, default_statute: str) -> list[Citation]:
    """Attach a statute to citations that did not name one.

    In a document about the Penal Code, "section 302" means 302 PPC. Resolving that from
    context is normal legal reading — but the default must be supplied explicitly by
    whatever knows the context, never guessed, because attributing a provision to the
    wrong Act is a serious error that looks like a correct answer.
    """
    return [
        c
        if (c.statute or c.kind != "statutory")
        else Citation(**{**c.__dict__, "statute": default_statute})
        for c in citations
    ]

"""The statute book, as a thing that changes over time.

The failure this module exists to prevent.

Legal text is **versioned**. Sections are amended, substituted and repealed, and the
text of a provision on one date is not the text on another. A retrieval system that
treats a statute as a flat document will happily answer a question about today's law
using a provision repealed in 2016 — and the answer will be fluent, specific, correctly
cited, and wrong.

That is worse than refusing, because a refusal prompts someone to check and a confident
citation does not. A lawyer acting on a repealed provision, or a citizen told their
conduct is lawful under a section that no longer exists, has been harmed by the system
working exactly as designed.

So every provision carries the dates it was in force, retrieval is always **as of** a
date, and superseded text is retained rather than deleted — because questions about past
conduct are asked against the law as it then stood, and deleting history makes those
unanswerable.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from .citation import Citation


class CorpusError(ValueError):
    pass


def _as_date(value: str | dt.date | None) -> dt.date | None:
    if value is None or isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value)


@dataclass
class Provision:
    """One section, article or rule, as it stood over one interval."""

    statute: str
    unit: str  # "section" | "article" | "rule"
    number: str
    heading: str
    text: str
    in_force_from: dt.date
    # None means still in force.
    in_force_to: dt.date | None = None
    # What replaced it, where a provision was substituted rather than simply repealed.
    superseded_by: str = ""
    # How it ceased: "repealed", "substituted", "omitted".
    manner: str = ""
    amended_by: str = ""
    language: str = "en"
    chapter: str = ""

    def __post_init__(self) -> None:
        self.in_force_from = _as_date(self.in_force_from)
        self.in_force_to = _as_date(self.in_force_to)
        if self.in_force_to and self.in_force_to < self.in_force_from:
            raise CorpusError(f"{self.key}: in_force_to precedes in_force_from")

    @property
    def key(self) -> str:
        return f"{self.statute}:{self.unit}:{self.number}"

    @property
    def version_key(self) -> str:
        return f"{self.key}@{self.in_force_from.isoformat()}"

    def in_force_on(self, date: dt.date) -> bool:
        if date < self.in_force_from:
            return False
        return self.in_force_to is None or date < self.in_force_to

    @property
    def currently_in_force(self) -> bool:
        return self.in_force_to is None

    def citation(self) -> Citation:
        return Citation(
            kind="statutory",
            statute=self.statute,
            unit=self.unit,
            provision=self.number,
            raw=self.key,
        )

    def status_note(self, as_of: dt.date) -> str:
        """What a reader must be told about this text before relying on it."""
        if self.in_force_on(as_of):
            return ""
        if as_of < self.in_force_from:
            return (
                f"not yet in force on {as_of.isoformat()} "
                f"(commenced {self.in_force_from.isoformat()})"
            )
        manner = self.manner or "repealed"
        note = f"{manner} with effect from {self.in_force_to.isoformat()}"
        if self.superseded_by:
            note += f"; see {self.superseded_by}"
        return note


@dataclass
class Corpus:
    provisions: list[Provision] = field(default_factory=list)

    def add(self, provision: Provision) -> Provision:
        self.provisions.append(provision)
        return provision

    def __len__(self) -> int:
        return len(self.provisions)

    def as_of(self, date: str | dt.date) -> list[Provision]:
        """Every provision in force on a date. The only way retrieval should read."""
        date = _as_date(date)
        return [p for p in self.provisions if p.in_force_on(date)]

    def versions(self, key: str) -> list[Provision]:
        """Every version of one provision, oldest first."""
        return sorted(
            (p for p in self.provisions if p.key == key),
            key=lambda p: p.in_force_from,
        )

    def current(self, key: str) -> Provision | None:
        return next((p for p in self.versions(key) if p.currently_in_force), None)

    def version_on(self, key: str, date: str | dt.date) -> Provision | None:
        date = _as_date(date)
        return next((p for p in self.versions(key) if p.in_force_on(date)), None)

    def history(self, key: str) -> list[dict]:
        """The amendment trail, which is itself frequently the answer."""
        return [
            {
                "from": p.in_force_from.isoformat(),
                "to": p.in_force_to.isoformat() if p.in_force_to else None,
                "manner": p.manner or ("in force" if p.currently_in_force else ""),
                "amended_by": p.amended_by,
                "superseded_by": p.superseded_by,
                "heading": p.heading,
            }
            for p in self.versions(key)
        ]

    def validate(self) -> list[str]:
        """Structural problems that would produce wrong answers silently.

        Overlapping versions are the dangerous one: if two versions of a section are
        both in force on a date, retrieval returns whichever it happens to reach first
        and the result is not reproducible.
        """
        problems: list[str] = []
        by_key: dict[str, list[Provision]] = {}
        for p in self.provisions:
            by_key.setdefault(p.key, []).append(p)

        for key, versions in by_key.items():
            ordered = sorted(versions, key=lambda p: p.in_force_from)
            for earlier, later in zip(ordered, ordered[1:], strict=False):
                if earlier.in_force_to is None:
                    problems.append(
                        f"{key}: version from {earlier.in_force_from} never ends, but "
                        f"another begins {later.in_force_from}"
                    )
                elif earlier.in_force_to > later.in_force_from:
                    problems.append(
                        f"{key}: versions overlap between {later.in_force_from} and "
                        f"{earlier.in_force_to}"
                    )
                elif earlier.in_force_to < later.in_force_from:
                    # A gap means the provision was not in force at all for a period,
                    # which happens but is almost always a data error.
                    problems.append(
                        f"{key}: gap in force between {earlier.in_force_to} and "
                        f"{later.in_force_from}"
                    )

            if sum(1 for p in versions if p.currently_in_force) > 1:
                problems.append(f"{key}: more than one version is currently in force")

        return problems

    def __iter__(self) -> Iterator[Provision]:
        return iter(self.provisions)


def build(rows: Sequence[dict]) -> Corpus:
    corpus = Corpus()
    for row in rows:
        corpus.add(Provision(**row))
    return corpus

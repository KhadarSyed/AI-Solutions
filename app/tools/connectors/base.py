"""Connector contract — every source normalizes to the same 9-field record and
declares which filters it can apply source-side."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, field_validator


class RawArticle(BaseModel):
    """The normalized record every connector must produce."""

    publisher_name: str = ""
    title: str
    content: str = ""
    publisher_domain: str = ""
    published_date: date | None = None
    url: str
    author: str = ""
    country: str = "all"          # resolved by the enrichment sub-agent when unknown
    language: str = ""

    # provenance for downstream slicing
    source: str = ""              # connector name
    query_group: str = ""
    original_query: str = ""

    # attribution / classification
    subject_brand: str = ""       # the brand or competitor the query targeted
    medium: str = "news"          # "news" | "social"

    @field_validator("url")
    @classmethod
    def _clean_url(cls, v: str) -> str:
        return v.strip()


@dataclass(frozen=True)
class SearchFilters:
    days_back: int = 7
    language: str | None = "en"
    country: str | None = None    # None → all
    max_results: int = 50


@dataclass(frozen=True)
class Capabilities:
    """Which SearchFilters this source can honor server-side. Anything not
    supported is applied as a post-filter by the ingestion service."""

    date_range: bool = False
    language: bool = False
    country: bool = False
    max_results: bool = True


@dataclass
class ConnectorResult:
    articles: list[RawArticle] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Connector(ABC):
    name: str = "base"
    capabilities: Capabilities = Capabilities()

    @abstractmethod
    def enabled(self) -> bool:
        """Connectors switch on by their env keys being present."""

    @abstractmethod
    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult: ...

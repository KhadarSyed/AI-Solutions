"""tag_articles_batch — the strict 18-field structured output (playbook §3)."""

from pydantic import BaseModel, Field


class ArticleEntities(BaseModel):
    brand_of_interest: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    other_competitors: list[str] = Field(default_factory=list)
    peoples: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)


class TaggedArticle(BaseModel):
    """18 fields per record: 3 labels + priority flag + 4 confidences + 3 reasons
    + 6 entity lists + spokesperson level."""

    index: int = Field(description="Zero-based index of the article within THIS batch")

    xai_sentiment: str = Field(description="POS | NEG | NEU — toward the brand ONLY")
    xai_theme: str = Field(description="Concise 2-5 word topic label")
    xai_section: str = Field(description="One of the project's sections")
    priority_watch: bool = Field(
        default=False, description="True for crisis/negative/regulatory/viral coverage"
    )

    sentiment_confidence: float = Field(ge=0, le=1)
    theme_confidence: float = Field(ge=0, le=1)
    section_confidence: float = Field(ge=0, le=1)
    relevancy_confidence: float = Field(ge=0, le=1)

    xai_sentiment_reason: str = Field(description="Plain-language reason — mandatory")
    xai_theme_reason: str = Field(description="Plain-language reason — mandatory")
    xai_relevancy_reason: str = Field(description="Plain-language reason — mandatory")

    entities: ArticleEntities = Field(default_factory=ArticleEntities)
    spokesperson_level: str = Field(
        default="none", description="c_suite | mid_level | none — highest level quoted/mentioned"
    )


class TagBatchOutput(BaseModel):
    articles: list[TaggedArticle]


TAGGING_SYSTEM_PROMPT = """You are the XAI tagging agent for a PR monitoring platform.
Tag every article in the batch with ALL fields. Golden rules:
- Sentiment is ASPECT-BASED toward the brand of interest only — never the article's
  overall tone. An article about a market downturn that praises the brand is POS.
- Never leave a reason empty; reasons are shown to human reviewers.
- xai_section must be one of the provided sections.
- priority_watch=true for crisis, strongly negative, regulatory or viral coverage.
- Entities: list actual names found in the text; brand mentions under brand_of_interest,
  known competitors under competitors, other rival companies under other_competitors.
Return one TaggedArticle per input article, index matching the batch order."""

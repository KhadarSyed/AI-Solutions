"""tag_articles_batch — the strict structured output (playbook §3), extended with
theme tiers, emotions, signals, and product entities. Controlled vocabularies for
emotions/signals are enforced by validators so the tagger cannot invent a category
(anti-hallucination) and every value aggregates cleanly."""

from pydantic import BaseModel, Field, field_validator, model_validator

# Plutchik primary emotions — the only accepted emotion labels.
EMOTIONS: frozenset[str] = frozenset(
    {"joy", "trust", "fear", "surprise", "sadness", "disgust", "anger", "anticipation"}
)
# PR coverage signals — the only accepted signal labels.
SIGNALS: frozenset[str] = frozenset(
    {"product_launch", "partnership", "regulatory", "crisis", "earnings",
     "leadership_change", "expansion", "award", "lawsuit", "recall", "hiring",
     "research", "other"}
)


def _clean_vocab(values: list[str], allowed: frozenset[str]) -> list[str]:
    """Normalize (lowercase, spaces/hyphens → underscore) and keep only allowed labels,
    de-duplicated in first-seen order. Unknown labels are dropped, never stored."""
    out: list[str] = []
    for v in values or []:
        norm = str(v).strip().lower().replace(" ", "_").replace("-", "_")
        if norm in allowed and norm not in out:
            out.append(norm)
    return out


class ArticleEntities(BaseModel):
    brand_of_interest: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    other_competitors: list[str] = Field(default_factory=list)
    peoples: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)


class TaggedArticle(BaseModel):
    """Per-record tags: sentiment + 3 theme tiers + emotions + signals + priority flag
    + confidences + reasons + entity lists + spokesperson level."""

    index: int = Field(description="Zero-based index of the article within THIS batch")

    xai_sentiment: str = Field(description="POS | NEG | NEU — toward the brand ONLY")

    theme_primary: str = Field(default="", description="Main topic, concise 2-5 words")
    theme_secondary: str = Field(default="", description="Secondary topic, or empty")
    theme_tertiary: str = Field(default="", description="Tertiary topic, or empty")
    xai_theme: str = Field(default="", description="Alias of theme_primary (back-compat)")

    xai_section: str = Field(description="One of the project's sections")
    priority_watch: bool = Field(
        default=False, description="True for crisis/negative/regulatory/viral coverage"
    )

    emotions: list[str] = Field(
        default_factory=list,
        description="Reader emotions evoked, from: joy, trust, fear, surprise, sadness, "
        "disgust, anger, anticipation. Zero or more.",
    )
    signals: list[str] = Field(
        default_factory=list,
        description="PR signals present, from: product_launch, partnership, regulatory, "
        "crisis, earnings, leadership_change, expansion, award, lawsuit, recall, hiring, "
        "research, other. Zero or more.",
    )

    sentiment_confidence: float = Field(ge=0, le=1)
    theme_confidence: float = Field(ge=0, le=1)
    section_confidence: float = Field(ge=0, le=1)
    relevancy_confidence: float = Field(ge=0, le=1)
    emotion_confidence: float = Field(default=0.0, ge=0, le=1)
    signal_confidence: float = Field(default=0.0, ge=0, le=1)

    xai_sentiment_reason: str = Field(description="Plain-language reason — mandatory")
    xai_theme_reason: str = Field(description="Plain-language reason — mandatory")
    xai_relevancy_reason: str = Field(description="Plain-language reason — mandatory")

    is_relevant: bool = Field(
        default=True,
        description="True ONLY if the article is genuinely about the brand, its competitors, "
        "or the monitored industry as the user intends — NOT a mere name/keyword collision "
        "(e.g. a baseball or wrestling story that only mentions a stadium named after an HVAC "
        "brand). Set False to DROP off-topic coverage; such items are excluded from monitoring.")
    summary: str = Field(
        default="",
        description="1–2 sentence factual summary of the article's substance, grounded in its "
        "actual content (who/what/why it matters). No opinion, no marketing, no filler.")

    entities: ArticleEntities = Field(default_factory=ArticleEntities)
    spokesperson_level: str = Field(
        default="none", description="c_suite | mid_level | none — highest level quoted/mentioned"
    )

    @field_validator("emotions")
    @classmethod
    def _clean_emotions(cls, v: list[str]) -> list[str]:
        return _clean_vocab(v, EMOTIONS)

    @field_validator("signals")
    @classmethod
    def _clean_signals(cls, v: list[str]) -> list[str]:
        return _clean_vocab(v, SIGNALS)

    @model_validator(mode="after")
    def _sync_theme_alias(self) -> "TaggedArticle":
        # theme_primary and xai_theme stay mirrored so old charts/CSV/embeddings keep working
        if not self.theme_primary and self.xai_theme:
            self.theme_primary = self.xai_theme
        if not self.xai_theme and self.theme_primary:
            self.xai_theme = self.theme_primary
        return self


class TagBatchOutput(BaseModel):
    articles: list[TaggedArticle]


TAGGING_SYSTEM_PROMPT = """You are the XAI tagging agent for a PR monitoring platform.
Tag every article in the batch with ALL fields. Golden rules:
- Sentiment is ASPECT-BASED toward the brand of interest only — never the article's
  overall tone. An article about a market downturn that praises the brand is POS.
- Themes: theme_primary is the main topic (2-5 words); theme_secondary and theme_tertiary
  are additional topics if present, else empty strings.
- emotions: choose zero or more ONLY from this exact set — joy, trust, fear, surprise,
  sadness, disgust, anger, anticipation. Never invent labels.
- signals: choose zero or more ONLY from this exact set — product_launch, partnership,
  regulatory, crisis, earnings, leadership_change, expansion, award, lawsuit, recall,
  hiring, research, other. Never invent labels.
- Never leave a reason empty; reasons are shown to human reviewers.
- xai_section must be one of the provided sections.
- priority_watch=true for crisis, strongly negative, regulatory or viral coverage.
- Entities: list actual names found in the text; brand mentions under brand_of_interest,
  known competitors under competitors, other rival companies under other_competitors,
  product/service names under products, people under peoples, orgs under organizations.
- summary: 1-2 factual sentences on what the article actually says and why it matters —
  grounded in the content, no opinion or marketing. This is shown on every dashboard card.
- is_relevant: the ROBUST relevance gate. Set FALSE when the article is not genuinely about
  the brand, its competitors, or the monitored industry as the user intends — especially a
  mere name/keyword COLLISION (e.g. a baseball/wrestling story that only mentions a stadium
  named after an HVAC brand, a celebrity who shares a brand's name, an unrelated product with
  the same word). Off-topic items (is_relevant=false) are DROPPED from monitoring, so judge
  strictly against the brand + industry intent, not surface keyword matches.
Return one TaggedArticle per input article, index matching the batch order."""

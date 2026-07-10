"""Listing Copy Agent — pure logic core (spec §10.1).

Input : a repo URL and/or a free-text project description.
Output: Agent Store listing copy — a tagline, exactly 3 selling bullets, and a
        suggested category.

Deterministic and dependency-free so it is unit-testable and cheap to run as a
CAP provider (price ~0.05 USDC). No LLM call is required for the MVP: we derive
the copy from keyword signals in the input. If an LLM is wired later, keep this
as the deterministic fallback so the provider never fails to deliver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Category inference: ordered so earlier, more specific matches win.
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("risk", ("risk", "audit", "security", "compliance", "fraud", "aml", "kyc")),
    ("defi", ("defi", "swap", "liquidity", "yield", "lending", "amm", "dex")),
    ("infra", ("gas", "rpc", "node", "infra", "indexer", "oracle", "uptime")),
    ("research", ("research", "analysis", "report", "brief", "insight", "data")),
    ("trading", ("trade", "trading", "signal", "arbitrage", "market", "price")),
    ("nft", ("nft", "collection", "mint", "art", "collectible")),
    ("social", ("social", "twitter", "discord", "community", "content", "post")),
]

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with",
    "that", "this", "is", "are", "be", "your", "you", "it", "as", "by",
    "agent", "agents", "using", "based", "built", "our", "we", "can", "will",
}


@dataclass
class ListingCopyResult:
    """Structured Agent Store listing copy."""

    tagline: str
    suggested_category: str
    bullets: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


    def to_text(self) -> str:
        """Human-readable deliverable (what the buyer receives)."""
        lines = [
            f"TAGLINE: {self.tagline}",
            f"CATEGORY: {self.suggested_category}",
            "BULLETS:",
        ]
        lines += [f"  • {b}" for b in self.bullets]
        return "\n".join(lines)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _infer_category(text: str) -> str:
    low = text.lower()
    for category, kws in _CATEGORY_KEYWORDS:
        if any(kw in low for kw in kws):
            return category
    return "utility"


def _project_name(repo_url: str | None, description: str) -> str:
    """Best-effort human name for the project."""
    if repo_url:
        slug = repo_url.rstrip("/").split("/")[-1]
        slug = re.sub(r"\.git$", "", slug)
        slug = re.sub(r"[-_]+", " ", slug).strip()
        if slug:
            return slug.title()
    # Fall back to the first few meaningful words of the description.
    words = [w for w in re.findall(r"[A-Za-z0-9]+", description) if w]
    return " ".join(words[:3]).title() if words else "Your Agent"


def _keywords(text: str, limit: int = 6) -> list[str]:
    words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", text)]
    seen: dict[str, int] = {}
    for w in words:
        if w in _STOPWORDS:
            continue
        seen[w] = seen.get(w, 0) + 1
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:limit]]


def generate_listing_copy(
    *,
    repo_url: str | None = None,
    description: str = "",
) -> ListingCopyResult:
    """Generate Agent Store listing copy from a repo URL and/or description.

    Raises ValueError if there is nothing to work with.
    """
    description = _clean(description)
    repo_url = _clean(repo_url) if repo_url else None
    if not repo_url and not description:
        raise ValueError("provide at least a repo_url or a description")

    corpus = " ".join(filter(None, [repo_url, description]))
    name = _project_name(repo_url, description)
    category = _infer_category(corpus)
    keywords = _keywords(description or repo_url or "")

    focus = keywords[0] if keywords else category
    tagline = f"{name} — autonomous {focus} for the CROO agent economy."

    bullets = [
        f"Purpose-built for {category}: {name} turns recurring demand into "
        "on-chain revenue.",
        (
            "CAP-native: discoverable, hireable, and paid in USDC on Base — "
            "no integrations, no invoices."
        ),
        (
            f"Composable supply: plug {name} into standing orders and mini-RFQ "
            "rounds as a first-class provider."
        ),
    ]

    return ListingCopyResult(
        tagline=tagline,
        bullets=bullets,
        suggested_category=category,
        keywords=keywords,
    )

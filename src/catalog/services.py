"""Read-side helpers for the public pages (one place that knows how plans and features combine)."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Feature, Plan


@dataclass(frozen=True)
class FeatureRow:
    feature: Feature
    included: list[bool]  # one entry per active plan, in plan order


@dataclass(frozen=True)
class FeatureSection:
    key: str
    label: str
    rows: list[FeatureRow]


def active_plans() -> list[Plan]:
    return list(Plan.objects.filter(is_active=True))


def feature_sections(plans: list[Plan] | None = None) -> list[FeatureSection]:
    """Features grouped by category, each with a yes/no per plan: the data behind the comparison table and features page."""
    plans = plans if plans is not None else active_plans()
    sections: dict[str, list[FeatureRow]] = {}
    for feature in Feature.objects.select_related("available_from"):
        sections.setdefault(feature.category, []).append(
            FeatureRow(feature, [feature.included_in(plan) for plan in plans])
        )
    return [FeatureSection(key, label, sections[key]) for key, label in Feature.Category.choices if key in sections]


def highlights(limit: int = 6) -> list[Feature]:
    return list(Feature.objects.filter(is_highlight=True).select_related("available_from")[:limit])

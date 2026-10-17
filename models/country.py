from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CountrySummary:
    code: str
    names: dict[str, str]
    feature_count: int

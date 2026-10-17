from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MapChange:
    before: tuple[float, float] | None
    after: tuple[float, float] | None
    render_changed: bool
    counts_changed: bool

from typing import NamedTuple


class OSMCountry(NamedTuple):
    tags: dict[str, str]
    geometry: dict
    representative_point: dict
    timestamp: float

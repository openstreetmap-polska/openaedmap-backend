from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AED:
    id: int
    version: int
    tags: dict[str, str]
    lon: float
    lat: float


@dataclass(frozen=True, slots=True)
class RemovedNode:
    id: int
    version: int


type NodeChange = AED | RemovedNode


class ConflictingNodeVersionError(ValueError):
    pass

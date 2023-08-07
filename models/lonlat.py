from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LonLat:
    lon: float
    lat: float

    def __iter__(self):
        return iter((self.lon, self.lat))

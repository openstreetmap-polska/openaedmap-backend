import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ExportedNode:
    node_id: int
    country_code: Optional[str]
    tags: dict
    longitude: float
    latitude: float

    def as_geojson_feature(self) -> dict:
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [self.longitude, self.latitude]
            },
            "properties": {
                "@osm_type": "node",
                "@osm_id": self.node_id,
                **self.tags,
            },
        }


def generate_geojson(data: list[ExportedNode]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            node.as_geojson_feature()
            for node in data
        ]
    }

def save_geojson_file(file_path: str | Path, data: list[ExportedNode] | dict | None) -> None:
    logger.info(f"Exporting file: {file_path}")
    process_start = time.perf_counter()
    with open(file_path, "w", encoding="utf-8") as fp:
        match data:
            case dict():
                country_geojson = data
            case list():
                country_geojson = generate_geojson(data)
            case None:
                logger.info(f"No data provided for file: {file_path} it will be saved as empty geojson file.")
                country_geojson = generate_geojson([])
            case _:
                raise TypeError(f"Unexpected value of data attribute: {data}")
        json.dump(country_geojson, fp)
    process_end = time.perf_counter()
    process_time = process_end - process_start  # in seconds
    logger.info(f"Finished exporting file: {file_path} it took: {round(process_time, 4)} seconds")

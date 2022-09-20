import requests
from shapely.geometry import shape, Polygon, MultiPolygon, box
from shapely.strtree import STRtree

import csv
import json
from pathlib import Path
from sys import argv
from typing import List, Union, Dict, Sequence

if __name__ == '__main__':
    country_geojson_url = argv[1]
    output_dir_path = Path(argv[2])
    step = int(argv[3]) if len(argv) > 3 else 1

    if country_geojson_url.startswith("http"):
        response = requests.get(country_geojson_url)
        data = response.json()
    else:
        with open(country_geojson_url, "r", encoding="utf-8") as content:
            data = json.load(content)

    countries: List[Union[Polygon, MultiPolygon]] = [shape(feature["geometry"]) for feature in data["features"]]
    iso_codes: List[str] = [feature["properties"]["ISO3166-1:alpha3"] for feature in data["features"]]
    index = STRtree(geoms=countries, items=iso_codes)

    with open(output_dir_path / "simple_lookup.csv", "w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile, delimiter=",", quoting=csv.QUOTE_MINIMAL)

        for x in range(0, 360, step):
            for y in range(0, 180, step):
                bbox = box(minx=x - 180, miny=y - 90, maxx=x - 180 + step, maxy=y - 90 + step)
                codes = index.query_items(bbox)
                writer.writerow((x, y, ";".join(codes)))

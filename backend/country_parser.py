import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Dict, Any, Optional

import requests
from shapely import geometry

DATA_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_sovereignty.geojson"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Feature:
    properties: Dict[str, Any]
    geometry: dict


@dataclass(frozen=True, kw_only=True)
class Country:
    country_code: str
    geometry: str
    country_names: Dict[str, str]


def parse_feature(feature: Feature) -> Optional[Country]:
    if feature.properties["ISO_A2"] == "-99" and feature.properties["ISO_A2_EH"] in {"BR", "GA"}:
        # duplicates/errors in Natural Earth dataset
        logger.warning(f"Ignoring feature with properties: {feature.properties}")
        return None
    country_code: str = feature.properties['ISO_A2_EH']
    if country_code == "-99":
        if feature.properties["NAME"] == "Israel":
            country_code = "IL"
        else:
            logger.warning(f"Missing ISO Code for feature: {feature.properties}")
            return None
    wkt_geometry: str = geometry.shape(feature.geometry).wkt
    country_names: Dict[str, str] = {}
    for k, v in feature.properties.items():
        if k.startswith('NAME_') and k != "NAME_SORT":
            language_code = k.split('_')[1]
            country_names[language_code] = v
        elif k == 'NAME':
            country_names['default'] = v
    return Country(country_code=country_code, geometry=wkt_geometry, country_names=country_names)


def parse_countries(data_url: str | Path = DATA_URL) -> Generator[Country, None, None]:
    url: str = data_url if type(data_url) == str else data_url.as_posix()
    if url.startswith("http"):
        logger.info("Downloading data from Natural Earth github...")
        raw = requests.get(url)
        raw.raise_for_status()
        data: dict = raw.json()
        logger.info("Data downloaded successfully.")
    else:
        logger.info(f"Reading data from local filesystem using provided path: {url} ...")
        raw = open(url, 'r', encoding='utf-8').read()
        data: dict = json.loads(raw)
        logger.info("Read data from filesystem.")

    for idx, feature in enumerate(data['features']):
        logger.debug(f"Parsing record no: {idx} with properties: {feature['properties']}")
        feature = parse_feature(Feature(properties=feature['properties'], geometry=feature['geometry']))
        if feature is not None:
            yield feature

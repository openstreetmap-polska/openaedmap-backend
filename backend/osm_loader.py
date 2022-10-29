import logging
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import floor
from typing import Dict, Generator

import requests

logger = logging.getLogger(__name__)


OVERPASS_URL = "https://lz4.overpass-api.de/api/interpreter"
OVERPASS_QUERY = """
[out:json]
[timeout:1800];                
node[emergency=defibrillator];
out meta qt;
"""
REPLICATION_MINUTE_URL = "https://planet.osm.org/replication/minute/"


@dataclass(frozen=True)
class ReplicationSequence:
    timestamp: datetime
    number: int
    formatted: str


@dataclass(frozen=True)
class Node:
    node_id: int
    version: int
    uid: int
    user: str
    changeset: int
    latitude: float
    longitude: float
    tags: Dict[str, str]


def full_list_from_overpass(
    api_url: str = OVERPASS_URL,
    api_query: str = OVERPASS_QUERY
) -> Generator[Node, None, None]:
    """Returns generator yielding data about all defibrillator nodes from Overpass API."""

    logger.info(f"Sending request to: {api_url} with query: `{api_query}`.")
    response = requests.post(url=api_url, data={"data": api_query})
    response.raise_for_status()
    data = response.json()["elements"]
    logger.info("Request successful returning data...")
    for idx, n in enumerate(data):
        logger.debug(f"Parsing record no: {idx} with properties: {n}")
        yield Node(
            node_id=n["id"],
            version=n["version"],
            uid=n["uid"],
            user=n["user"],
            changeset=n["changeset"],
            latitude=n["lat"],
            longitude=n["lon"],
            tags=n["tags"],
        )


def format_replication_sequence(sequence: int | str) -> str:
    seq = str(sequence)
    if len(seq) < 7:
        return f"000/{seq[1:4]}/{seq[4:7]}"
    elif len(seq) == 11:
        return seq
    else:
        return f"{seq[0].zfill(3)}/{seq[1:4]}/{seq[4:7]}"


def get_replication_sequence(url: str) -> ReplicationSequence:
    logger.info(f"Sending request to: {url}")
    response = requests.get(url)
    response.raise_for_status()
    logger.info(f"Request to: {url} was successful, response: {response.text}")
    data = response.text.splitlines()
    seq = data[1].split("=")[1]
    ts = data[2].split("=")[1]
    dt = datetime.fromisoformat(ts.replace("\\", "").replace("Z", ""))
    return ReplicationSequence(timestamp=dt, number=int(seq), formatted=format_replication_sequence(seq))


def find_newest_replication_sequence(replication_url: str = REPLICATION_MINUTE_URL) -> ReplicationSequence:
    url = urllib.parse.urljoin(replication_url, "state.txt")
    return get_replication_sequence(url)


def estimated_replication_sequence(delta: timedelta) -> ReplicationSequence:
    newest_replication_sequence = find_newest_replication_sequence()
    minutes = floor(delta.total_seconds() / 60)
    new_seq_num = newest_replication_sequence.number + minutes
    new_seq_formatted = format_replication_sequence(new_seq_num)
    new_seq_ts = newest_replication_sequence.timestamp + delta
    return ReplicationSequence(timestamp=new_seq_ts, number=new_seq_num, formatted=new_seq_formatted)

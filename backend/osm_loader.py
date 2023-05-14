import gzip
import json
import logging
import urllib.parse
import xml.dom.pulldom
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from math import floor
from typing import Dict, Generator, Optional
from xml.dom.minidom import Element

import requests
from requests import Response

from backend.utils import print_runtime

logger = logging.getLogger(__name__)


OVERPASS_URL = "https://lz4.overpass-api.de/api/interpreter"
OVERPASS_QUERY = """
[out:json]
[timeout:1800];                
node[emergency=defibrillator];
out meta qt;
"""
REPLICATION_MINUTE_URL = "https://planet.osm.org/replication/minute/"


def make_sure_val_is_simple(
    value: int | float | str | dict | datetime,
) -> int | float | str:
    match value:
        case dict():
            return json.dumps(value)
        case datetime():
            return value.isoformat()
        case _:
            return value


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
    version_timestamp: datetime

    def as_params_dict(self) -> dict:
        return {k: make_sure_val_is_simple(v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class Change:
    type: str
    element: Node

    def as_params_dict(self) -> dict:
        node = {k: make_sure_val_is_simple(v) for k, v in self.element.__dict__.items()}
        return {
            "change_type": self.type,
            **node
        }


@print_runtime(logger)
def send_request(url: str) -> Response:
    logger.info(f"Sending request to: {url}")
    response = requests.get(url)
    response.raise_for_status()
    logger.info(f"Response from: {url} successful, code: {response.status_code}.")
    return response


@print_runtime(logger)
def full_list_from_overpass(
    api_url: str = OVERPASS_URL, api_query: str = OVERPASS_QUERY
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
            version_timestamp=n["timestamp"],
        )


def format_replication_sequence(sequence: int | str) -> str:
    """Formats sequence number to format with slashes (how the files are separated into folders in the server).
    E.g.: 1234567 -> 001/234/567"""

    seq = str(sequence)
    if len(seq) < 7:
        return f"000/{seq[1:4]}/{seq[4:7]}"
    elif len(seq) == 11:
        return seq
    else:
        return f"{seq[0].zfill(3)}/{seq[1:4]}/{seq[4:7]}"


def replication_sequence_to_int(sequence: str) -> int:
    """Convert formatted sequence to integer. E.g.: 001/222/333 -> 1222333."""
    return int(sequence.replace("/", ""))


def get_replication_sequence(url: str) -> ReplicationSequence:
    """Helper method that downloads state file from given url and parses out sequence number and timestamp."""

    response = send_request(url)
    data = response.text.splitlines()
    seq = data[1].split("=")[1]
    ts = data[2].split("=")[1]
    dt = datetime.fromisoformat(ts.replace("\\", "").replace("Z", ""))

    return ReplicationSequence(
        timestamp=dt, number=int(seq), formatted=format_replication_sequence(seq)
    )


def find_newest_replication_sequence(
    replication_url: str = REPLICATION_MINUTE_URL,
) -> ReplicationSequence:
    """Checks what's the newest sequence number in OSM replication log."""
    url = urllib.parse.urljoin(replication_url, "state.txt")
    return get_replication_sequence(url)


def estimated_replication_sequence(delta: timedelta) -> ReplicationSequence:
    """Estimate sequence number based on newest sequence number and timedelta.
    Timedelta should be created with negative values."""

    newest_replication_sequence = find_newest_replication_sequence()
    minutes = floor(delta.total_seconds() / 60)
    new_seq_num = newest_replication_sequence.number + minutes
    new_seq_formatted = format_replication_sequence(new_seq_num)
    new_seq_ts = newest_replication_sequence.timestamp + delta

    return ReplicationSequence(
        timestamp=new_seq_ts, number=new_seq_num, formatted=new_seq_formatted
    )


def contains_tag(element: Element, key: str, value: Optional[str]) -> bool:
    """Checks whether XML element contains tag with given key and (optional) value."""

    for child in element.childNodes:
        child: Element = child  # for typing
        if child.tagName == "tag":
            if child.getAttribute("k") == key:
                if value:
                    return child.getAttribute("v") == value
                else:
                    return True
    return False


def xml_to_node(xml_element: Element) -> Node:
    """Converts XML element from OSC file to Node."""

    tags = {
        k: v
        for k, v in [
            (t.getAttribute("k"), t.getAttribute("v"))
            for t in xml_element.getElementsByTagName("tag")
        ]
    }
    return Node(
        node_id=xml_element.getAttribute("id"),
        version=xml_element.getAttribute("version"),
        uid=xml_element.getAttribute("uid"),
        user=xml_element.getAttribute("user"),
        changeset=xml_element.getAttribute("changeset"),
        latitude=xml_element.getAttribute("lat"),
        longitude=xml_element.getAttribute("lon"),
        tags=tags,
        version_timestamp=xml_element.getAttribute("timestamp"),
    )


def download_and_parse_change_file(url: str) -> Generator[Change, None, None]:
    """Downloads OSC file from URL and parses out Nodes."""

    response = send_request(url)
    logger.info("Decompressing...")
    xml_data = gzip.GzipFile(fileobj=BytesIO(response.content))
    logger.info("Parsing XML...")
    event_stream = xml.dom.pulldom.parse(xml_data)
    counter = 0
    for event, element in event_stream:
        element: Element = element  # just for typing
        if event == xml.dom.pulldom.START_ELEMENT and element.tagName in {
            "create",
            "modify",
            "delete",
        }:
            event_stream.expandNode(element)
            for child in element.childNodes:
                child: Element = child  # for typing
                if type(child) == Element and child.tagName == "node":
                    counter += 1
                    yield Change(type=element.tagName, element=xml_to_node(child))
    logger.info(
        f"Finished parsing file downloaded from: {url} . There were {counter} nodes."
    )


def changes_between_seq(
    start_sequence: str | int,
    end_sequence: str | int,
    replication_url: str = REPLICATION_MINUTE_URL,
    skip_first: bool = False,
) -> Generator[Change, None, None]:
    """Download and parse all changes since provided sequence number. Yields Changes of Nodes."""

    if type(start_sequence) == str:
        start_sequence = replication_sequence_to_int(start_sequence)
    if skip_first:
        start_sequence += 1
    if type(end_sequence) == str:
        end_sequence = replication_sequence_to_int(end_sequence)

    list_of_urls = [
        urllib.parse.urljoin(replication_url, f"{format_replication_sequence(seq)}.osc.gz")
        for seq in range(start_sequence, end_sequence + 1)
    ]
    logger.info(f"There are: {len(list_of_urls)} OSC files to download.")
    with ThreadPoolExecutor() as executor:
        for result in executor.map(download_and_parse_change_file, list_of_urls):
            for change in result:
                yield change
    logger.info("Finished downloading and parsing OSC files.")

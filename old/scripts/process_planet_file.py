import osmium
from osmium.replication.utils import get_replication_header
from osmium.osm import Node

import json
# from copy import copy


class CounterHandler(osmium.SimpleHandler):
    def __init__(self):
        osmium.SimpleHandler.__init__(self)
        self.num_nodes = 0
        self.data = []

    def node(self, n: Node):
        if "emergency" in n.tags and n.tags.get(key="emergency", default="") == "defibrillator":
            self.num_nodes += 1
            self.data.append({
                "osm_id": n.id,
                "lon": n.location.lon,
                "lat": n.location.lat,
                "tags": {
                    "access": n.tags.get(key="access", default="")
                },
            })


if __name__ == '__main__':
    input_fp = "/home/tomasz/Downloads/opolskie-latest.osm.pbf"
    url, seq, ts = get_replication_header(input_fp)
    print(url, seq, ts)
    h = CounterHandler()
    h.apply_file(input_fp)
    print("Number of nodes: %d" % h.num_nodes)
    with open("data.json", "w", encoding="utf-8") as file_obj:
        json.dump(
            obj={
                "url": url,
                "seq": seq,
                "ts": ts.isoformat(),
                "data": h.data,
            },
            fp=file_obj,
        )

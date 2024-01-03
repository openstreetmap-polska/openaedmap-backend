import cython

from models.bbox import BBox
from models.lonlat import LonLat

if cython.compiled:
    from cython.cimports.libc.math import atan, pi, sinh

    print(f'{__name__}: 🐇 compiled')
else:
    from math import atan, pi, sinh

    print(f'{__name__}: 🐌 not compiled')


@cython.cfunc
def _degrees(radians: cython.double) -> cython.double:
    return radians * 180 / pi


@cython.cfunc
def _tile_to_lonlat(z: cython.int, x: cython.int, y: cython.int) -> tuple[cython.double, cython.double]:
    n: cython.double = 2**z
    lon_deg: cython.double = x / n * 360.0 - 180.0
    lat_rad: cython.double = atan(sinh(pi * (1 - 2 * y / n)))
    lat_deg: cython.double = _degrees(lat_rad)
    return lon_deg, lat_deg


def tile_to_bbox(z: cython.int, x: cython.int, y: cython.int) -> BBox:
    p1_lon, p1_lat = _tile_to_lonlat(z, x, y)
    p2_lon, p2_lat = _tile_to_lonlat(z, x + 1, y + 1)
    return BBox(LonLat(p1_lon, p2_lat), LonLat(p2_lon, p1_lat))

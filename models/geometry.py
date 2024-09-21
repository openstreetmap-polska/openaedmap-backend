from abc import ABC
from typing import override

from shapely import from_wkb, get_coordinates
from shapely.geometry.base import BaseGeometry
from sqlalchemy import BindParameter
from sqlalchemy.sql import func
from sqlalchemy.types import UserDefinedType


class _GeometryType(UserDefinedType, ABC):
    geometry_type: str

    def get_col_spec(self, **kw):
        return f'geometry({self.geometry_type}, 4326)'

    @override
    def bind_processor(self, dialect):
        def process(value: BaseGeometry | None):
            if value is None:
                return None
            return value.wkt

        return process

    @override
    def bind_expression(self, bindvalue: BindParameter):
        return func.ST_GeomFromText(bindvalue, 4326, type_=self)

    @override
    def column_expression(self, colexpr):
        return func.ST_AsBinary(colexpr, type_=self)

    @override
    def result_processor(self, dialect, coltype):
        def process(value: bytes | None):
            if value is None:
                return None
            return from_wkb(value)

        return process


class PointType(_GeometryType):
    geometry_type = 'Point'
    cache_ok = True

    @override
    def bind_processor(self, dialect):
        def process(value: BaseGeometry | None):
            if value is None:
                return None
            x, y = get_coordinates(value)[0]
            return f'POINT({x} {y})'  # WKT

        return process


class PolygonType(_GeometryType):
    geometry_type = 'Polygon'
    cache_ok = True

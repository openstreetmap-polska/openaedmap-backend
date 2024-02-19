from pydantic import PlainSerializer, PlainValidator
from shapely.geometry import mapping, shape
from shapely.ops import BaseGeometry


def geometry_validator(value: dict | BaseGeometry) -> BaseGeometry:
    """
    Validate a geometry.
    """

    return shape(value) if isinstance(value, dict) else value


def geometry_serializer(value: BaseGeometry) -> dict:
    """
    Serialize a geometry.
    """

    return mapping(value)


GeometryValidator = PlainValidator(geometry_validator)
GeometrySerializer = PlainSerializer(geometry_serializer, return_type=dict)

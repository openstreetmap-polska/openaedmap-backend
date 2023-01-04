from sqlalchemy import (
    Column,
    String,
    BigInteger,
)

from backend.database.base import Base


class CountriesQueue(Base):
    __tablename__ = 'countries_queue'
    __table_args__ = ()
    countries_queue_id = Column(BigInteger, primary_key=True)
    country_code = Column(String, nullable=False)

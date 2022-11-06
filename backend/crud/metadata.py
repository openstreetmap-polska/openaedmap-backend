from sqlalchemy.orm import Session

from backend.models import Metadata


def get_metadata(db: Session) -> Metadata:
    return db.query(Metadata).one()

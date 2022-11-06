from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.crud.metadata import get_metadata
from backend.models import Metadata

router = APIRouter()


@router.get('/metadata')
async def metadata(db: Session = Depends(get_db)) -> Metadata:
    return get_metadata(db)

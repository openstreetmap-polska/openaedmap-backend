from sentry_sdk import trace
from sqlalchemy.dialects.postgresql import insert

from db import db_read, db_write
from models.db.state import State


class StateService:
    @staticmethod
    @trace
    async def get(key: str) -> dict | None:
        async with db_read() as session:
            instance = await session.get(State, key)
            return instance.data if (instance is not None) else None

    @staticmethod
    @trace
    async def set(key: str, data: dict) -> None:
        async with db_write() as session:
            stmt = (
                insert(State)
                .values({State.key: key, State.data: data})
                .on_conflict_do_update(
                    index_elements=(State.key,),
                    set_={State.data: data},
                )
            )
            await session.execute(stmt)

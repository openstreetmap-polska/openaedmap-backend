import pytest


@pytest.mark.asyncio
async def test_generated_ids_work_for_orm_and_core_inserts(database):
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.ext.asyncio import AsyncSession

    from models.db.photo import Photo
    from models.db.photo_report import PhotoReport

    async with database.begin() as conn:
        await conn.run_sync(Photo.__table__.create)
        await conn.run_sync(PhotoReport.__table__.create)
    try:
        async with AsyncSession(database, expire_on_commit=False) as session:
            first = Photo(node_id=123, user_id=456)
            second = Photo(node_id=789, user_id=456)
            session.add_all([first, second])
            await session.flush()
            assert isinstance(first.id, str)
            assert first.id
            assert isinstance(second.id, str)
            assert second.id != first.id

            result = await session.scalars(
                insert(PhotoReport).values([{'photo_id': first.id}, {'photo_id': second.id}]).returning(PhotoReport.id)
            )
            report_ids = result.all()
            assert len(report_ids) == 2
            assert all(isinstance(id, str) and id for id in report_ids)
            assert len(set(report_ids)) == 2
            await session.commit()
    finally:
        async with database.begin() as conn:
            await conn.run_sync(PhotoReport.__table__.drop)
            await conn.run_sync(Photo.__table__.drop)

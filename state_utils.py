from config import STATE_COLLECTION


async def get_state_doc(name: str, **kwargs) -> dict | None:
    return await STATE_COLLECTION.find_one({'_name': name}, **kwargs)


async def set_state_doc(name: str, doc: dict, **kwargs) -> None:
    await STATE_COLLECTION.replace_one({'_name': name}, doc | {'_name': name}, upsert=True, **kwargs)

from motor.core import AgnosticClientSession

from config import MONGO_CLIENT


class Transaction:
    async def __aenter__(self):
        self.session: AgnosticClientSession = await MONGO_CLIENT.start_session()
        self.context = self.session.start_transaction()
        await self.context.__aenter__()
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.context.__aexit__(exc_type, exc_val, exc_tb)

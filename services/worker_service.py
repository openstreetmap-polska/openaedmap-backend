import fcntl
import os
from typing import Literal

import anyio
from anyio import Path

from config import DATA_DIR
from utils import retry_exponential

_PID_PATH = DATA_DIR / 'worker.pid'
_STATE_PATH = DATA_DIR / 'worker.state'
_LOCK_PATH = DATA_DIR / 'worker.lock'


WorkerState = Literal['startup', 'running']


class WorkerService:
    is_primary: bool

    @retry_exponential(10)
    @staticmethod
    async def init() -> 'WorkerService':
        self = WorkerService()
        self._lock_file = await anyio.open_file(_LOCK_PATH, 'w')

        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.is_primary = True
            await _STATE_PATH.write_text('startup')
            await _PID_PATH.write_text(str(os.getpid()))
        except BlockingIOError:
            self.is_primary = False

            while True:
                if await _PID_PATH.is_file() and await _STATE_PATH.is_file():
                    pid = await _PID_PATH.read_text()
                    if pid and await Path(f'/proc/{pid}').is_dir():
                        break

                await anyio.sleep(0.1)

        return self

    async def set_state(self, state: WorkerState) -> None:
        if not self.is_primary:
            raise AssertionError('Only the primary worker can set the state')
        await _STATE_PATH.write_text(state)

    @retry_exponential(10)
    async def get_state(self) -> WorkerState:
        return await _STATE_PATH.read_text()

    async def wait_for_state(self, state: WorkerState) -> None:
        while await self.get_state() != state:
            await anyio.sleep(0.1)

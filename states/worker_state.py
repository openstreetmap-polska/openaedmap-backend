import fcntl
import os
from datetime import timedelta
from enum import Enum

import anyio
from anyio import Path

from config import NAME
from utils import retry_exponential

_PID_PATH = Path(f'/tmp/{NAME}-worker.pid')
_STATE_PATH = Path(f'/tmp/{NAME}-worker.state')
_LOCK_PATH = Path(f'/tmp/{NAME}-worker.lock')


class WorkerStateEnum(str, Enum):
    STARTUP = 'startup'
    RUNNING = 'running'


class WorkerState:
    is_primary: bool

    @retry_exponential(timedelta(seconds=10))
    async def ainit(self) -> None:
        self._lock_file = await anyio.open_file(_LOCK_PATH, 'w')

        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.is_primary = True
            await _STATE_PATH.write_text(WorkerStateEnum.STARTUP.value)
            await _PID_PATH.write_text(str(os.getpid()))
        except BlockingIOError:
            self.is_primary = False

            while True:
                if await _PID_PATH.is_file() and await _STATE_PATH.is_file():
                    pid = await _PID_PATH.read_text()
                    if pid and await Path(f'/proc/{pid}').is_dir():
                        break

                await anyio.sleep(0.1)

    async def set_state(self, state: WorkerStateEnum) -> None:
        assert self.is_primary
        await _STATE_PATH.write_text(state.value)

    @retry_exponential(timedelta(seconds=10))
    async def get_state(self) -> WorkerStateEnum:
        return WorkerStateEnum(await _STATE_PATH.read_text())

    async def wait_for_state(self, state: WorkerStateEnum) -> None:
        while await self.get_state() != state:
            await anyio.sleep(0.1)

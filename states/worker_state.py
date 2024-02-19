import fcntl
import os
from datetime import timedelta
from enum import Enum

import anyio
import psutil

from config import NAME
from utils import retry_exponential

_PID_PATH = anyio.Path(f'/tmp/{NAME}-worker.pid')
_STATE_PATH = anyio.Path(f'/tmp/{NAME}-worker.state')
_LOCK_PATH = anyio.Path(f'/tmp/{NAME}-worker.lock')


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
                if not await _PID_PATH.is_file() or not await _STATE_PATH.is_file():
                    await anyio.sleep(0.1)
                    continue

                pid = await _PID_PATH.read_text()

                if not pid or not psutil.pid_exists(int(pid)):
                    await anyio.sleep(0.1)
                    continue

                break

    async def set_state(self, state: WorkerStateEnum) -> None:
        assert self.is_primary
        await _STATE_PATH.write_text(state.value)

    @retry_exponential(timedelta(seconds=10))
    async def get_state(self) -> WorkerStateEnum:
        return WorkerStateEnum(await _STATE_PATH.read_text())

    async def wait_for_state(self, state: WorkerStateEnum) -> None:
        while await self.get_state() != state:
            await anyio.sleep(0.1)

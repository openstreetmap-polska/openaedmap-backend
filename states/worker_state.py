import os
import shutil
from datetime import timedelta
from enum import Enum
from typing import Annotated, Self

import anyio
import psutil
from fastapi import Depends
from filelock import FileLock

from config import NAME
from utils import retry_exponential

_PID_PATH = anyio.Path(f'/tmp/{NAME}-worker.pid')
_STATE_PATH = anyio.Path(f'/tmp/{NAME}-worker.state')
_LOCK_PATH = anyio.Path(f'/tmp/{NAME}-worker.lock')


class WorkerStateEnum(Enum):
    STARTUP = 'startup'
    RUNNING = 'running'


class WorkerState:
    is_primary: bool
    _lock: FileLock

    @retry_exponential(timedelta(seconds=10))
    async def ainit(self) -> None:
        self._lock = FileLock(_LOCK_PATH)

        try:
            self._lock.acquire(blocking=False)
            self.is_primary = True
            await _STATE_PATH.write_text(WorkerStateEnum.STARTUP.value)
            await _PID_PATH.write_text(str(os.getpid()))
        except Exception:
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


_instance = WorkerState()


def get_worker_state() -> WorkerState:
    return _instance


WorkerStateDep = Annotated[WorkerState, Depends(get_worker_state)]

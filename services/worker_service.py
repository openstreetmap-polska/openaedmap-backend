import fcntl
import os
from asyncio import sleep
from pathlib import Path
from typing import Literal, cast

from config import DATA_DIR
from utils import retry_exponential

_PID_PATH = DATA_DIR / 'worker.pid'
_STATE_PATH = DATA_DIR / 'worker.state'
_LOCK_PATH = DATA_DIR / 'worker.lock'

WorkerState = Literal['startup', 'running']


class WorkerService:
    is_primary: bool
    _lock_fd: int

    @staticmethod
    @retry_exponential(10)
    async def init() -> 'WorkerService':
        self = WorkerService()
        self._lock_fd = os.open(_LOCK_PATH, os.O_RDONLY | os.O_CREAT)

        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.is_primary = True
            _STATE_PATH.write_text('startup')
            _PID_PATH.write_text(str(os.getpid()))
        except BlockingIOError:
            self.is_primary = False

            while True:
                if _PID_PATH.is_file() and _STATE_PATH.is_file():
                    pid = _PID_PATH.read_text()
                    if pid and Path(f'/proc/{pid}').is_dir():
                        break
                await sleep(0.1)

        return self

    async def set_state(self, state: WorkerState) -> None:
        if not self.is_primary:
            raise AssertionError('Only the primary worker can set the state')
        _STATE_PATH.write_text(state)

    @retry_exponential(10)
    async def get_state(self) -> WorkerState:
        return cast(WorkerState, _STATE_PATH.read_text())

    async def wait_for_state(self, state: WorkerState) -> None:
        while await self.get_state() != state:  # noqa: ASYNC110
            await sleep(0.1)

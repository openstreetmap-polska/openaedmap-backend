import datetime
from typing import Dict


tiles_refresh_interval: Dict[int, datetime.timedelta] = {
    0: datetime.timedelta(hours=24),
    1: datetime.timedelta(hours=12),
    2: datetime.timedelta(hours=6),
    3: datetime.timedelta(hours=1),
    4: datetime.timedelta(hours=1),
    5: datetime.timedelta(hours=1),
    6: datetime.timedelta(minutes=30),
    7: datetime.timedelta(minutes=30),
    8: datetime.timedelta(minutes=30),
    9: datetime.timedelta(minutes=15),
    10: datetime.timedelta(minutes=5),
    11: datetime.timedelta(minutes=3),
    12: datetime.timedelta(minutes=2),
    13: datetime.timedelta(minutes=0),
}

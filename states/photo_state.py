import secrets
from io import BytesIO
from time import time
from typing import Annotated

import anyio
import pymongo
from dacite import from_dict
from fastapi import Depends, UploadFile
from PIL import Image, ImageOps

from config import IMAGE_LIMIT_PIXELS, IMAGE_MAX_FILE_SIZE, PHOTO_COLLECTION
from models.photo_info import PhotoInfo
from utils import as_dict


def _resize_image(img: Image.Image) -> Image.Image:
    width, height = img.size

    if width * height <= IMAGE_LIMIT_PIXELS:
        return img

    ratio = (IMAGE_LIMIT_PIXELS / (width * height)) ** 0.5
    new_width = int(width * ratio)
    new_height = int(height * ratio)
    return img.resize((new_width, new_height), Image.LANCZOS)


def _optimize_image(img: Image.Image, format: str = 'WEBP') -> bytes:
    high, low = 95, 20
    bs_step = 5
    best_quality = None
    best_buffer = None

    with BytesIO() as buffer:
        # initial quick scan
        for quality in (80, 60, 40, 20):
            buffer.seek(0)
            buffer.truncate()

            img.save(buffer, format=format, quality=quality)
            size = buffer.tell()

            print(f'[QS] 🅠 Q{quality}: {size / 1024 / 1024:.2f}MB')

            if size > IMAGE_MAX_FILE_SIZE:
                high = quality - bs_step
            else:
                low = quality + bs_step
                best_quality = quality
                best_buffer = buffer.getvalue()
                break
        else:
            raise ValueError('Image is too big')

        # fine-tune with binary search
        while low <= high:
            quality = ((low + high) // 2) // bs_step * bs_step

            buffer.seek(0)
            buffer.truncate()

            img.save(buffer, format=format, quality=quality)
            size = buffer.tell()

            print(f'[BS] 🅠 Q{quality}: {size / 1024 / 1024:.2f}MB')

            if size > IMAGE_MAX_FILE_SIZE:
                high = quality - bs_step
            else:
                low = quality + bs_step
                best_quality = quality
                best_buffer = buffer.getvalue()

        print(f'🅠 Photo quality: {best_quality}')
        return best_buffer


class PhotoState:
    async def get_photo_by_id(self, id: str, *, check_file: bool = True) -> PhotoInfo | None:
        doc = await PHOTO_COLLECTION.find_one({'id': id}, projection={'_id': False})

        if doc is None:
            return None

        info = from_dict(PhotoInfo, doc)

        if check_file and not await info.path.is_file():
            return None

        return info

    async def get_photo_by_node_id(self, node_id: str) -> PhotoInfo | None:
        cursor = PHOTO_COLLECTION.find(
            {'node_id': node_id},
            projection={'_id': False},
        ).sort('timestamp', pymongo.DESCENDING)

        async for c in cursor:
            info = from_dict(PhotoInfo, c)

            # find newest, non-deleted photo
            # NOTE: maybe delete missing photos from database? if at least one successful
            if await info.path.is_file():
                return info

        return None

    async def set_photo(self, node_id: str, user_id: str, file: UploadFile) -> PhotoInfo:
        info = PhotoInfo(
            id=secrets.token_urlsafe(16),
            node_id=node_id,
            user_id=user_id,
            timestamp=time(),
        )

        img = Image.open(file.file)
        img = await anyio.to_thread.run_sync(ImageOps.exif_transpose, img)
        img = await anyio.to_thread.run_sync(_resize_image, img)
        img_b = await anyio.to_thread.run_sync(_optimize_image, img)

        await info.path.write_bytes(img_b)
        await PHOTO_COLLECTION.insert_one(as_dict(info))
        return info


_instance = PhotoState()


def get_photo_state() -> PhotoState:
    return _instance


PhotoStateDep = Annotated[PhotoState, Depends(get_photo_state)]

import secrets
from io import BytesIO
from time import time

from dacite import from_dict
from fastapi import UploadFile
from PIL import Image, ImageOps
from sentry_sdk import trace

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
    @staticmethod
    @trace
    async def get_photo_by_id(id: str, *, check_file: bool = True) -> PhotoInfo | None:
        doc = await PHOTO_COLLECTION.find_one({'id': id}, projection={'_id': False})

        if doc is None:
            return None

        info = from_dict(PhotoInfo, doc)

        if check_file and not await info.path.is_file():
            return None

        return info

    @staticmethod
    @trace
    async def set_photo(node_id: int, user_id: int, file: UploadFile) -> PhotoInfo:
        info = PhotoInfo(
            id=secrets.token_urlsafe(16),
            node_id=str(node_id),
            user_id=str(user_id),
            timestamp=time(),
        )

        img = Image.open(file.file)
        img = ImageOps.exif_transpose(img)
        img = _resize_image(img)
        img_bytes = _optimize_image(img)

        await info.path.write_bytes(img_bytes)
        await PHOTO_COLLECTION.insert_one(as_dict(info))
        return info

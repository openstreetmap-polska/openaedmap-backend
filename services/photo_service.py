import logging
from io import BytesIO

from fastapi import UploadFile
from PIL import Image, ImageOps
from sentry_sdk import trace

from config import IMAGE_LIMIT_PIXELS, IMAGE_MAX_FILE_SIZE
from db import db_read, db_write
from models.db.photo import Photo


class PhotoService:
    @staticmethod
    @trace
    async def get_by_id(id: str, *, check_file: bool = True) -> Photo | None:
        async with db_read() as session:
            photo = await session.get(Photo, id)

        if photo is None:
            return None
        if check_file and (not await photo.file_path.is_file()):
            return None

        return photo

    @staticmethod
    @trace
    async def upload(node_id: int, user_id: int, file: UploadFile) -> Photo:
        img = Image.open(file.file)
        ImageOps.exif_transpose(img, in_place=True)
        img = _resize_image(img)
        img_bytes = _optimize_quality(img)

        async with db_write() as session:
            photo = Photo(
                node_id=node_id,
                user_id=user_id,
            )
            session.add(photo)

        await photo.file_path.write_bytes(img_bytes)
        return photo


@trace
def _resize_image(img: Image.Image) -> Image.Image:
    width, height = img.size
    if width * height <= IMAGE_LIMIT_PIXELS:
        return img

    ratio = (IMAGE_LIMIT_PIXELS / (width * height)) ** 0.5
    new_width = int(width * ratio)
    new_height = int(height * ratio)
    return img.resize((new_width, new_height), Image.Resampling.LANCZOS)


@trace
def _optimize_quality(img: Image.Image) -> bytes:
    high, low = 95, 20
    bs_step = 5
    best_buffer = None

    with BytesIO() as buffer:
        # initial quick scan
        for quality in (80, 60, 40, 20):
            buffer.seek(0)
            buffer.truncate()

            img.save(buffer, format='WEBP', quality=quality)
            size = buffer.tell()
            logging.debug('Optimizing avatar quality (quick): Q%d -> %.2fMB', quality, size / 1024 / 1024)

            if size > IMAGE_MAX_FILE_SIZE:
                high = quality - bs_step
            else:
                low = quality + bs_step
                best_buffer = buffer.getvalue()
                break
        else:
            raise ValueError('Image is too big')

        # fine-tune with binary search
        while low <= high:
            quality = ((low + high) // 2) // bs_step * bs_step

            buffer.seek(0)
            buffer.truncate()

            img.save(buffer, format='WEBP', quality=quality)
            size = buffer.tell()
            logging.debug('Optimizing avatar quality (fine): Q%d -> %.2fMB', quality, size / 1024 / 1024)

            if size > IMAGE_MAX_FILE_SIZE:
                high = quality - bs_step
            else:
                low = quality + bs_step
                best_buffer = buffer.getvalue()

        return best_buffer

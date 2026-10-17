import logging
import os
from asyncio import Semaphore
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from tempfile import NamedTemporaryFile

from fastapi import UploadFile
from PIL import Image, ImageOps
from sentry_sdk import add_attachment, trace

from config import IMAGE_LIMIT_PIXELS, IMAGE_MAX_FILE_SIZE, PHOTOS_DIR
from db import connection
from models.photo import Photo
from utils import run_blocking

_image_slot = Semaphore(1)


class PhotoService:
    @staticmethod
    @trace
    async def get_by_id(id: str, *, check_file: bool = True) -> Photo | None:
        async with connection() as conn:
            row = await conn.fetchrow('SELECT id, node_id, user_id, created_at FROM photo WHERE id = $1', id)
        photo = Photo(**row) if row else None

        if photo is None:
            return None
        if check_file and not await run_blocking(photo.file_path.is_file):
            return None

        return photo

    @staticmethod
    @trace
    async def upload(node_id: int, user_id: int, file: UploadFile) -> Photo:
        id = token_urlsafe(16)
        async with _image_slot:
            await run_blocking(_publish_file, node_id, user_id, id, file)
            # The durable file precedes its row. An uncertain DB commit may leave
            # a recoverable file, but must never leave a row pointing to lost bytes.
            async with connection() as conn:
                row = await conn.fetchrow(
                    'INSERT INTO photo (id, node_id, user_id) VALUES ($1, $2, $3) RETURNING *',
                    id,
                    node_id,
                    user_id,
                )
        return Photo(**row)


def _publish_file(node_id: int, user_id: int, id: str, file: UploadFile) -> None:
    with Image.open(file.file) as img:
        try:
            ImageOps.exif_transpose(img, in_place=True)
        except Exception:
            file.file.seek(0)
            add_attachment(file.file.read(), filename=file.filename, content_type=file.content_type)
            raise
        content = _optimize_quality(_resize_image(img))
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    destination = PHOTOS_DIR / f'{user_id}_{node_id}_{id}.webp'
    with NamedTemporaryFile(dir=PHOTOS_DIR, suffix='.tmp', delete=False) as pending:
        path = Path(pending.name)
        try:
            pending.write(content)
            pending.flush()
            os.fsync(pending.fileno())
            path.replace(destination)
            directory = os.open(PHOTOS_DIR, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            path.unlink(missing_ok=True)


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

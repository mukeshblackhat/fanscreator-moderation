"""Real file uploads (extra to the graded API).

POST /media/upload           multipart `file` (image or video). Stored privately under
                             MEDIA_STORE/<media id>/. Images are re-encoded to JPEG (max
                             1024 px, EXIF stripped, orientation applied). Videos are
                             sampled at 1 frame per second with ffmpeg, so frame N is
                             second N. The media then goes through the same pipeline as
                             any upload; the worker sends it to the real provider.
GET  /media/{id}/frames/{n}  one stored frame, with strict rules:
                               published         any member of the workspace
                               held (score hold) workspace admins, so they can review it
                               anything else     404 (pending, blocked, unscreened holds)
                             Blocked media is never served to anyone.
GET  /real-mode              what the real provider checks (proxied from its /info)
"""
import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.identity import Caller, require_member
from app.logging import log
from app.routes.media import parse_media_id

router = APIRouter()
Image.MAX_IMAGE_PIXELS = 50_000_000  # refuse decompression bombs
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/avif"}
VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska", "video/x-msvideo"}
MAX_EDGE = 1024


def store_root() -> Path:
    root = Path(settings.media_store)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _image_frame(src: Path, dest_dir: Path) -> int:
    try:
        with Image.open(src) as probe:
            probe.verify()
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((MAX_EDGE, MAX_EDGE))
            img.save(dest_dir / "frame_0000.jpg", "JPEG", quality=90)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"not a readable image ({type(exc).__name__})") from exc
    return 1


def _video_frames(src: Path, dest_dir: Path) -> int:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(src)],
        capture_output=True, text=True, timeout=30)
    try:
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        raise ValueError("not a readable video") from None
    if duration > settings.max_video_seconds:
        raise ValueError(f"video is {duration:.0f} s; the limit is {settings.max_video_seconds} s")
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vf", f"fps=1,scale='min({MAX_EDGE},iw)':-2",
         "-q:v", "3", "-start_number", "0", str(dest_dir / "frame_%04d.jpg")],
        capture_output=True, text=True, timeout=300)
    count = len(list(dest_dir.glob("frame_*.jpg")))
    if result.returncode != 0 or count == 0:
        raise ValueError("could not extract frames from the video")
    return count


async def _save_upload(file: UploadFile, dest: Path, limit_mb: int) -> None:
    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit_mb * 1024 * 1024:
                raise ValueError(f"file is larger than {limit_mb} MB")
            out.write(chunk)
    if size == 0:
        raise ValueError("empty file")


@router.post("/media/upload", status_code=202)
async def upload_media(
    file: UploadFile = File(...),
    caller: Caller = Depends(require_member),
    session: AsyncSession = Depends(get_session),
):
    content_type = (file.content_type or "").lower()
    if content_type in IMAGE_TYPES:
        media_type, limit = "image", settings.max_image_mb
    elif content_type in VIDEO_TYPES:
        media_type, limit = "video", settings.max_video_mb
    else:
        return JSONResponse(status_code=422, content={"reasons": [f"upload:unsupported_type:{content_type or 'unknown'}"]})

    media_id = uuid.uuid4()
    folder = store_root() / str(media_id)
    folder.mkdir(parents=True)
    original = folder / f"original{Path(file.filename or '').suffix.lower()[:10]}"
    try:
        await _save_upload(file, original, limit)
        extract = _image_frame if media_type == "image" else _video_frames
        count = await asyncio.to_thread(extract, original, folder)
    except ValueError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        return JSONResponse(status_code=422, content={"reasons": [f"upload:{exc}"]})
    finally:
        await file.close()
    original.unlink(missing_ok=True)  # keep only the normalised frames we scored

    frames = [{"scores": {}, "image": f"{media_id}/frame_{i:04d}.jpg"} for i in range(count)]
    lane = "image" if media_type == "image" else "video"
    async with session.begin():
        await session.execute(text(
            """
            INSERT INTO media (id, workspace_id, created_by, type, source, frame_count, frames)
            VALUES (:id, :ws, :by, :type, 'upload', :n, CAST(:frames AS jsonb))
            """
        ), {"id": media_id, "ws": caller.workspace_id, "by": caller.user_id, "type": media_type, "n": count,
            "frames": json.dumps(frames)})
        await session.execute(text(
            "INSERT INTO jobs (media_id, workspace_id, lane) VALUES (:m, :ws, :lane)"
        ), {"m": media_id, "ws": caller.workspace_id, "lane": lane})
    log.info("media_received", media_id=str(media_id), workspace_id=caller.workspace_id, type=media_type,
             source="upload", frame_count=count, lane=lane, real_file=True, filename_ext=original.suffix)
    return {"id": media_id, "status": "pending", "frames": count}


@router.get("/media/{media_id}/frames/{n}")
async def get_frame(
    media_id: str,
    n: int,
    caller: Caller = Depends(require_member),
    session: AsyncSession = Depends(get_session),
):
    mid = parse_media_id(media_id)
    row = (await session.execute(text(
        "SELECT status, held_reason_kind, frames FROM media WHERE id = :id AND workspace_id = :ws"
    ), {"id": mid, "ws": caller.workspace_id})).first()
    if row is None or not 0 <= n < len(row.frames) or not row.frames[n].get("image"):
        raise HTTPException(status_code=404, detail="Not found")
    allowed = row.status == "published" or (
        row.status == "held" and row.held_reason_kind == "score" and caller.is_admin)
    if not allowed:
        # Pending, blocked and unscreened media are never shown. Members cannot see holds.
        raise HTTPException(status_code=404, detail="Not found")
    path = (store_root() / row.frames[n]["image"]).resolve()
    if store_root().resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.get("/real-mode")
async def real_mode():
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{settings.real_provider_url}/info")
        if r.status_code == 200:
            return {"available": True, **r.json()}
        return {"available": False, "reason": f"real provider answered {r.status_code} (still loading the model?)"}
    except httpx.HTTPError:
        return {"available": False, "reason": "real provider is not running (start the stack with the 'real' profile)"}

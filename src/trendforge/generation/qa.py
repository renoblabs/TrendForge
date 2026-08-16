from __future__ import annotations

from pathlib import Path


def _png_size(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def review_output(path: Path, *, expect_aspect: str = "9:16") -> dict:
    if not path.exists() or not path.is_file():
        return {"ok": False, "reason": "output file does not exist"}
    size = path.stat().st_size
    if size < 32:
        return {"ok": False, "reason": "output file too small to be media"}
    header = path.read_bytes()[:12]
    mime = "application/octet-stream"
    if header.startswith(b"\x89PNG"):
        mime = "image/png"
    elif header[:3] == b"GIF":
        mime = "image/gif"
    elif header[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    elif header[4:8] == b"ftyp" or path.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        mime = "video/mp4"
    elif header.startswith(b"RIFF") and b"WEBP" in header:
        mime = "image/webp"
    else:
        return {"ok": False, "reason": "unreadable or unsupported media type"}
    width = height = None
    if mime == "image/png":
        dims = _png_size(path)
        if dims:
            width, height = dims
    aspect_ok = True
    aspect_note = ""
    if expect_aspect == "9:16" and width and height:
        ratio = width / height
        aspect_ok = 0.48 <= ratio <= 0.64
        aspect_note = f"{width}x{height}"
        if not aspect_ok:
            return {
                "ok": False,
                "reason": f"aspect {aspect_note} is not ~9:16",
                "mime_type": mime,
                "width": width,
                "height": height,
            }
    return {
        "ok": True,
        "reason": "readable media",
        "mime_type": mime,
        "bytes": size,
        "width": width,
        "height": height,
        "aspect_note": aspect_note,
    }

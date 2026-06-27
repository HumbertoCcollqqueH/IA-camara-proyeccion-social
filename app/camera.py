"""Construcción de la fuente de video y apertura de la cámara."""
from __future__ import annotations

import logging
import urllib.parse

from .settings import settings

log = logging.getLogger(__name__)


def build_source(source: str | None = None) -> "int | str":
    """Devuelve un índice de webcam (int) o una URL/ruta de video (str).

    Si se pasa `source` (viene de la configuración del dashboard) tiene
    prioridad: "0" -> webcam; "rtsp://..."/"http://..." -> cámara IP/stream.

    Si no, se usa el .env:
      1) CAM_IP definido -> arma la URL RTSP según el perfil de cámara.
      2) VIDEO_SOURCE numérico -> webcam (índice).
      3) VIDEO_SOURCE como ruta/URL.
    """
    if source is not None and str(source).strip():
        s = str(source).strip()
        return int(s) if s.isdigit() else s
    if settings.cam_ip:
        return _build_rtsp()
    src = settings.video_source
    return int(src) if str(src).isdigit() else src


def _build_rtsp() -> str:
    user = urllib.parse.quote(settings.cam_user or "", safe="")
    pwd = urllib.parse.quote(settings.cam_password or "", safe="")
    cred = f"{user}:{pwd}@" if user else ""
    ip = settings.cam_ip
    port = settings.cam_rtsp_port
    profile = settings.cam_profile
    if profile == "hikvision":
        return f"rtsp://{cred}{ip}:{port}/Streaming/Channels/{settings.cam_channel}"
    if profile == "dahua":
        return f"rtsp://{cred}{ip}:{port}/cam/realmonitor?channel=1&subtype=0"
    return f"rtsp://{cred}{ip}:{port}/"


def open_capture(source):
    """Abre la cámara con el backend adecuado (DSHOW para webcam, FFMPEG para RTSP)."""
    import cv2

    backend = cv2.CAP_DSHOW if isinstance(source, int) else cv2.CAP_FFMPEG
    cap = cv2.VideoCapture(source, backend)
    if isinstance(source, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap

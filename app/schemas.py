"""Esquemas Pydantic para la API del dashboard (validación de entrada/salida)."""
from __future__ import annotations

import datetime as dt
import re

from pydantic import BaseModel, Field, field_validator

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_PHONE = re.compile(r"^\d{8,15}$")

_DEFAULT_ALERT = (
    "*ALERTA — TOQUE DE QUEDA*\n"
    "Persona detectada{id} en la zona vigilada.\n"
    "Personas en escena: {personas}\n"
    "Confianza: {confianza}\n"
    "Fecha: {fecha}\n"
    "Hora: {hora}"
)


# --------------------------------------------------------------------------
#  Configuración
# --------------------------------------------------------------------------
class ConfigUpdate(BaseModel):
    enabled: bool = True
    curfew_start: str = "22:00"
    curfew_end: str = "04:00"
    model: str = "yolo11s.pt"
    conf_threshold: float = Field(0.90, ge=0.1, le=1.0)
    imgsz: int = Field(640, ge=160, le=1920)
    device: str = ""
    confirm_frames: int = Field(4, ge=1, le=30)
    cooldown_seconds: int = Field(120, ge=0, le=3600)
    night_enhance: bool = True
    send_crop: bool = True
    video_source: str = "0"
    alert_message: str = _DEFAULT_ALERT
    alert_mode: str = "persona"
    capture_window_ms: int = Field(2300, ge=0, le=10000)

    @field_validator("alert_mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in ("persona", "presencia") else "persona"

    @field_validator("video_source")
    @classmethod
    def _clean_source(cls, v: str) -> str:
        v = (v or "").strip()
        return v if v else "0"

    @field_validator("alert_message")
    @classmethod
    def _clean_message(cls, v: str) -> str:
        v = (v or "").strip()
        return v if v else _DEFAULT_ALERT

    @field_validator("curfew_start", "curfew_end")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("Hora inválida, usa formato HH:MM (24h).")
        return v


class ConfigOut(ConfigUpdate):
    updated_at: dt.datetime | None = None


# --------------------------------------------------------------------------
#  Números (destinatarios)
# --------------------------------------------------------------------------
class RecipientIn(BaseModel):
    phone: str
    label: str = ""
    active: bool = True

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, v: str) -> str:
        cleaned = "".join(ch for ch in v if ch.isdigit())
        if not _PHONE.match(cleaned):
            raise ValueError("Número inválido: solo dígitos, con código de país (8-15).")
        return cleaned


class RecipientOut(BaseModel):
    id: int
    phone: str
    label: str
    active: bool
    created_at: dt.datetime


# --------------------------------------------------------------------------
#  Historial
# --------------------------------------------------------------------------
class DetectionOut(BaseModel):
    id: int
    detected_at: dt.datetime
    persons: int
    confidence_max: float
    person_id: int | None = None
    image_full: str
    image_crop: str
    image_thumb: str
    notify_ok: bool
    notify_result: dict[str, str] = {}


# --------------------------------------------------------------------------
#  Estado general
# --------------------------------------------------------------------------
class StatusOut(BaseModel):
    enabled: bool
    in_curfew: bool
    worker_online: bool
    capturing: bool
    frames_processed: int
    worker_heartbeat: dt.datetime | None
    whatsapp_state: str
    active_recipients: int
    detections_total: int
    detections_today: int
    last_detection: DetectionOut | None = None


class WhatsAppConnect(BaseModel):
    state: str
    qr: str = ""
    pairing_code: str = ""
    message: str = ""
    name: str = ""


class CameraTestIn(BaseModel):
    source: str = ""        # vacío = usar la fuente configurada


class WhatsappInstanceIn(BaseModel):
    name: str = ""          # vacío = el servidor genera uno (emisor-N)
    label: str = ""

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        # Solo letras, números, guion y guion bajo. Vacío es válido (autogenera).
        v = re.sub(r"[^A-Za-z0-9_-]", "", (v or "").strip())
        if v and not (2 <= len(v) <= 60):
            raise ValueError("Nombre inválido: usa 2-60 caracteres (letras, números, - o _).")
        return v

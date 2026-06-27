"""Modelos de la base de datos (SQLAlchemy 2.0)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now()


class Config(Base):
    """Configuración operativa del sistema. Siempre hay una sola fila (id=1).

    El dashboard (Fase 2) edita esta fila; el worker la lee en cada ciclo,
    así los cambios se aplican sin reiniciar el programa.
    """

    __tablename__ = "config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Interruptor maestro: el vigilante puede pausar todo desde el dashboard.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Horario de toque de queda (formato "HH:MM", puede cruzar medianoche).
    curfew_start: Mapped[str] = mapped_column(String(5), default="22:00")
    curfew_end: Mapped[str] = mapped_column(String(5), default="04:00")

    # Detección
    model: Mapped[str] = mapped_column(String(64), default="yolo11s.pt")
    conf_threshold: Mapped[float] = mapped_column(Float, default=0.90)
    imgsz: Mapped[int] = mapped_column(Integer, default=640)
    device: Mapped[str] = mapped_column(String(16), default="")
    confirm_frames: Mapped[int] = mapped_column(Integer, default=4)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=120)
    night_enhance: Mapped[bool] = mapped_column(Boolean, default=True)
    send_crop: Mapped[bool] = mapped_column(Boolean, default=True)

    # Fuente de video: "0" = webcam; o una URL RTSP/HTTP de cámara IP.
    video_source: Mapped[str] = mapped_column(String(255), default="0")

    # Mensaje de WhatsApp. Acepta placeholders: {personas} {confianza} {fecha} {hora}
    alert_message: Mapped[str] = mapped_column(
        Text,
        default=(
            "*ALERTA — TOQUE DE QUEDA*\n"
            "Persona detectada en la zona vigilada.\n"
            "Personas: {personas}\n"
            "Confianza: {confianza}\n"
            "Fecha: {fecha}\n"
            "Hora: {hora}"
        ),
    )

    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Recipient(Base):
    """Número de WhatsApp que recibe las alertas (máximo 4 activos)."""

    __tablename__ = "recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)  # solo dígitos, con país
    label: Mapped[str] = mapped_column(String(80), default="")    # ej: "Vigilante turno noche"
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Detection(Base):
    """Historial detallado de cada alerta disparada."""

    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detected_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    persons: Mapped[int] = mapped_column(Integer, default=0)
    confidence_max: Mapped[float] = mapped_column(Float, default=0.0)

    image_full: Mapped[str] = mapped_column(String(255), default="")   # frame anotado completo
    image_crop: Mapped[str] = mapped_column(String(255), default="")   # recorte del cuerpo top
    image_thumb: Mapped[str] = mapped_column(String(255), default="")  # miniatura para el historial

    notify_ok: Mapped[bool] = mapped_column(Boolean, default=False)    # ¿llegó a algún número?
    notify_result: Mapped[str] = mapped_column(Text, default="{}")     # JSON {numero: ok/error}
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class WhatsappInstance(Base):
    """Una conexión de WhatsApp (una 'instancia' de Evolution API).

    Se gestionan desde la web: agregar, conectar por QR, marcar emisora, borrar.
    La instancia con is_sender=True es la que ENVÍA las alertas.
    """

    __tablename__ = "whatsapp_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)   # nombre en Evolution
    label: Mapped[str] = mapped_column(String(80), default="")    # ej: "Emisor principal"
    is_sender: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SystemState(Base):
    """Estado en vivo del worker, para que el dashboard sepa si está corriendo.

    Una sola fila (id=1). El worker actualiza `worker_heartbeat` periódicamente;
    el dashboard considera al worker "en línea" si el latido es reciente.
    """

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    worker_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    worker_heartbeat: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    capturing: Mapped[bool] = mapped_column(Boolean, default=False)   # dentro de horario y leyendo cámara
    frames_processed: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(255), default="")

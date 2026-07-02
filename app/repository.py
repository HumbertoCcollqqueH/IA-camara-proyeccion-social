"""Operaciones de lectura/escritura sobre la base de datos."""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Config, Detection, Recipient, SystemState, WhatsappInstance


def get_config(s: Session) -> Config:
    """Devuelve la fila de configuración (la crea vacía si faltara)."""
    cfg = s.get(Config, 1)
    if cfg is None:
        cfg = Config(id=1)
        s.add(cfg)
        s.flush()
    return cfg


def active_recipients(s: Session) -> list[Recipient]:
    """Hasta 4 números activos, en orden de creación."""
    stmt = (
        select(Recipient)
        .where(Recipient.active.is_(True))
        .order_by(Recipient.created_at)
        .limit(4)
    )
    return list(s.scalars(stmt))


def record_detection(
    s: Session,
    *,
    detected_at: dt.datetime,
    persons: int,
    confidence_max: float,
    image_full: str,
    image_crop: str,
    image_thumb: str,
    notify_result: dict[str, str],
    person_id: int | None = None,
) -> Detection:
    """Guarda una alerta en el historial."""
    notify_ok = any(v == "ok" for v in notify_result.values())
    det = Detection(
        detected_at=detected_at,
        persons=persons,
        confidence_max=confidence_max,
        person_id=person_id,
        image_full=image_full,
        image_crop=image_crop,
        image_thumb=image_thumb,
        notify_ok=notify_ok,
        notify_result=json.dumps(notify_result, ensure_ascii=False),
    )
    s.add(det)
    s.flush()
    return det


def recent_detections(s: Session, limit: int = 50, offset: int = 0) -> list[Detection]:
    """Alertas para el dashboard, de la más reciente a la más antigua."""
    stmt = (
        select(Detection)
        .order_by(Detection.detected_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(s.scalars(stmt))


def count_detections(s: Session) -> int:
    from sqlalchemy import func

    return int(s.scalar(select(func.count(Detection.id))) or 0)


def detections_since(s: Session, since: dt.datetime) -> int:
    from sqlalchemy import func

    stmt = select(func.count(Detection.id)).where(Detection.detected_at >= since)
    return int(s.scalar(stmt) or 0)


# --------------------------------------------------------------------------
#  Estado del sistema (heartbeat del worker)
# --------------------------------------------------------------------------
def get_system_state(s: Session) -> SystemState:
    st = s.get(SystemState, 1)
    if st is None:
        st = SystemState(id=1)
        s.add(st)
        s.flush()
    return st


def touch_heartbeat(s: Session, *, capturing: bool, frames_processed: int) -> None:
    st = get_system_state(s)
    st.worker_heartbeat = dt.datetime.now()
    st.capturing = capturing
    st.frames_processed = frames_processed


def mark_worker_started(s: Session) -> None:
    st = get_system_state(s)
    st.worker_started_at = dt.datetime.now()
    st.worker_heartbeat = dt.datetime.now()
    st.frames_processed = 0
    st.last_error = ""


# --------------------------------------------------------------------------
#  Conexiones de WhatsApp (instancias)
# --------------------------------------------------------------------------
def list_wa_instances(s: Session) -> list[WhatsappInstance]:
    return list(s.scalars(select(WhatsappInstance).order_by(WhatsappInstance.created_at)))


def get_wa_instance(s: Session, name: str) -> WhatsappInstance | None:
    return s.scalar(select(WhatsappInstance).where(WhatsappInstance.name == name))


def sender_instance_name(s: Session, default: str) -> str:
    """Nombre de la instancia que ENVÍA. Cae al default (.env) si no hay marcada."""
    inst = s.scalar(select(WhatsappInstance).where(WhatsappInstance.is_sender.is_(True)))
    if inst:
        return inst.name
    any_inst = s.scalar(select(WhatsappInstance).limit(1))
    return any_inst.name if any_inst else default


def next_instance_name(s: Session) -> str:
    """Genera un nombre único tipo 'emisor-2', 'emisor-3', ..."""
    existing = {i.name for i in list_wa_instances(s)}
    i = len(existing) + 1
    while f"emisor-{i}" in existing:
        i += 1
    return f"emisor-{i}"


def add_wa_instance(s: Session, name: str, label: str = "") -> WhatsappInstance:
    inst = WhatsappInstance(name=name, label=label, is_sender=False)
    # Si es la primera, que sea la emisora por defecto.
    if not s.scalar(select(WhatsappInstance).limit(1)):
        inst.is_sender = True
    s.add(inst)
    s.flush()
    return inst


def set_wa_sender(s: Session, name: str) -> None:
    for inst in list_wa_instances(s):
        inst.is_sender = inst.name == name


def delete_wa_instance(s: Session, name: str) -> bool:
    inst = get_wa_instance(s, name)
    if inst is None:
        return False
    was_sender = inst.is_sender
    s.delete(inst)
    s.flush()
    if was_sender:  # reasignar emisor a la primera que quede
        first = s.scalar(select(WhatsappInstance).order_by(WhatsappInstance.created_at).limit(1))
        if first:
            first.is_sender = True
    return True

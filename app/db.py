"""Conexión a la base de datos, creación de tablas y siembra inicial."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Config, Recipient, WhatsappInstance
from .settings import settings

log = logging.getLogger(__name__)

# `future=True` y pool_pre_ping para reconectar si Postgres se reinicia.
# Para SQLite permitimos uso entre hilos (dashboard + worker en el mismo proceso).
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(
    settings.database_url, pool_pre_ping=True, future=True, connect_args=_connect_args
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión transaccional: hace commit al salir bien, rollback si hay error."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db() -> None:
    """Crea las tablas, aplica migraciones ligeras y siembra config + números."""
    Base.metadata.create_all(engine)
    _auto_migrate()
    with session_scope() as s:
        _seed_config(s)
        _seed_recipients(s)
        _seed_instances(s)


def _sql_default(col):
    """Representa el valor por defecto de una columna como literal SQL (o None)."""
    d = col.default
    if d is None or not getattr(d, "is_scalar", False):
        return None  # sin default escalar (p.ej. callables como datetime.now)
    val = d.arg
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return "'" + val.replace("'", "''") + "'"
    return None


def _auto_migrate() -> None:
    """Migración ligera: agrega a tablas EXISTENTES las columnas nuevas del modelo.

    `create_all` no modifica tablas ya creadas; esto evita el error
    'column ... does not exist' cuando agregamos campos a Config, etc.
    No borra datos: las filas existentes toman el valor por defecto.
    """
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(dialect=engine.dialect)
            ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}'
            default = _sql_default(col)
            if default is not None:
                ddl += f" DEFAULT {default}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                log.info("Migración: columna %s.%s agregada.", table.name, col.name)
            except Exception as e:  # noqa: BLE001
                log.warning("No se pudo agregar %s.%s (%s)", table.name, col.name, e)


def _seed_config(s: Session) -> None:
    cfg = s.get(Config, 1)
    if cfg is not None:
        return
    cfg = Config(
        id=1,
        curfew_start=settings.seed_curfew_start,
        curfew_end=settings.seed_curfew_end,
        model=settings.seed_model,
        conf_threshold=settings.seed_conf_threshold,
        imgsz=settings.seed_imgsz,
        device=settings.seed_device,
        confirm_frames=settings.seed_confirm_frames,
        cooldown_seconds=settings.seed_cooldown_seconds,
        night_enhance=settings.seed_night_enhance,
        send_crop=settings.seed_send_crop,
        video_source=settings.video_source or "0",
    )
    s.add(cfg)
    log.info("Configuración inicial sembrada en la base de datos.")


def _seed_recipients(s: Session) -> None:
    existing = s.scalar(select(Recipient).limit(1))
    if existing is not None:
        return
    for phone in settings.seed_recipients:
        s.add(Recipient(phone=phone, label="Sembrado desde .env", active=True))
    if settings.seed_recipients:
        log.info("Números sembrados: %s", ", ".join(settings.seed_recipients))


def _seed_instances(s: Session) -> None:
    """Registra la instancia del .env como emisora si aún no hay ninguna."""
    if s.scalar(select(WhatsappInstance).limit(1)) is not None:
        return
    name = settings.evolution_instance or "vigilancia"
    s.add(WhatsappInstance(name=name, label="Emisor principal", is_sender=True))
    log.info("Instancia de WhatsApp sembrada: %s", name)

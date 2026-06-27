"""
Configuración de arranque leída desde variables de entorno (.env).

Aquí solo viven los datos que el programa necesita ANTES de tener base de datos:
conexión a la BD, a Evolution API y los valores con que se "siembra" la
configuración la primera vez. La configuración operativa (horario, umbral,
números) vive luego en la base de datos y se edita desde el dashboard.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv es opcional; las env vars del sistema bastan
    pass


BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "salidas_toque_queda"
IMG_DIR = OUT_DIR / "evidencias"
LOG_DIR = OUT_DIR / "logs"
LIVE_DIR = OUT_DIR / "live"
LIVE_PATH = LIVE_DIR / "live.jpg"   # último frame anotado, para la cámara en vivo


def _clean(raw: str | None) -> str:
    """Limpia un valor del .env: quita espacios y comentarios pegados.

    Tolera líneas mal copiadas del .env.example, p.ej.:
        DEVICE=            # "" = auto   ->  ""
        CONFIRM_FRAMES=4   # comentario  ->  "4"
    """
    if raw is None:
        return ""
    raw = raw.strip()
    if raw.startswith("#"):          # la línea quedó solo con el comentario
        return ""
    raw = re.sub(r"\s+#.*$", "", raw)  # quita comentario inline " # ..."
    return raw.strip()


def _env(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return _clean(raw) if raw is not None else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return _clean(raw).lower() in ("1", "true", "yes", "si", "sí", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(_clean(os.getenv(name)) or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_clean(os.getenv(name)) or default)
    except (TypeError, ValueError):
        return default


def _recipients_from_env() -> list[str]:
    raw = _env("RECIPIENTS")
    nums = [n.strip().lstrip("+").replace(" ", "") for n in raw.split(",")]
    return [n for n in nums if n][:4]  # máximo 4


@dataclass(frozen=True)
class Settings:
    # --- Base de datos ---
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", f"sqlite:///{(BASE_DIR / 'vigilancia.db').as_posix()}"
        )
    )

    # --- Evolution API (WhatsApp) ---
    evolution_url: str = field(default_factory=lambda: _env("EVOLUTION_API_URL", "http://localhost:8080"))
    evolution_instance: str = field(default_factory=lambda: _env("EVOLUTION_INSTANCE", "vigilancia"))
    evolution_api_key: str = field(default_factory=lambda: _env("EVOLUTION_API_KEY"))

    # --- Valores para sembrar la configuración la primera vez ---
    seed_recipients: list[str] = field(default_factory=_recipients_from_env)
    seed_curfew_start: str = field(default_factory=lambda: _env("CURFEW_START", "22:00"))
    seed_curfew_end: str = field(default_factory=lambda: _env("CURFEW_END", "04:00"))
    seed_model: str = field(default_factory=lambda: _env("MODEL", "yolo11s.pt"))
    seed_conf_threshold: float = field(default_factory=lambda: _env_float("CONF_THRESHOLD", 0.90))
    seed_imgsz: int = field(default_factory=lambda: _env_int("IMGSZ", 640))
    seed_device: str = field(default_factory=lambda: _env("DEVICE")[:16])
    seed_confirm_frames: int = field(default_factory=lambda: _env_int("CONFIRM_FRAMES", 4))
    seed_cooldown_seconds: int = field(default_factory=lambda: _env_int("COOLDOWN_SECONDS", 120))
    seed_night_enhance: bool = field(default_factory=lambda: _env_bool("NIGHT_ENHANCE", True))
    seed_send_crop: bool = field(default_factory=lambda: _env_bool("SEND_CROP", True))

    # --- Fuente de video ---
    video_source: str = field(default_factory=lambda: _env("VIDEO_SOURCE", "0"))
    cam_ip: str = field(default_factory=lambda: _env("CAM_IP"))
    cam_user: str = field(default_factory=lambda: _env("CAM_USER", "admin"))
    cam_password: str = field(default_factory=lambda: _env("CAM_PASSWORD"))
    cam_profile: str = field(default_factory=lambda: _env("CAM_PROFILE", "hikvision"))
    cam_channel: str = field(default_factory=lambda: _env("CAM_CHANNEL", "101"))
    cam_rtsp_port: int = field(default_factory=lambda: _env_int("CAM_RTSP_PORT", 554))


settings = Settings()

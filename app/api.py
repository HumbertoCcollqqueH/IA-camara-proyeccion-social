"""
API del dashboard (FastAPI).

Sirve el dashboard web y expone endpoints REST para:
  - configuración (horario, umbral, modelo...),
  - gestión de hasta 4 números,
  - historial de detecciones con miniaturas,
  - estado y conexión de WhatsApp (Evolution API) por QR.

Arranque:
    python -m app.main serve
    # o:  uvicorn app.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .curfew import parse_hhmm, within_curfew
from .db import init_db, session_scope
from .models import Detection, Recipient
from .notifier import EvolutionNotifier
from .repository import (
    active_recipients,
    add_wa_instance,
    count_detections,
    delete_wa_instance,
    detections_since,
    get_config,
    get_system_state,
    get_wa_instance,
    list_wa_instances,
    next_instance_name,
    recent_detections,
    sender_instance_name,
    set_wa_sender,
)
from .schemas import (
    CameraTestIn,
    ConfigOut,
    ConfigUpdate,
    DetectionOut,
    RecipientIn,
    RecipientOut,
    StatusOut,
    WhatsAppConnect,
    WhatsappInstanceIn,
)
from .settings import BASE_DIR, IMG_DIR, LIVE_PATH, settings

WEB_DIR = BASE_DIR / "web"
WORKER_ONLINE_WINDOW = 15  # segundos
LIVE_FRESH_WINDOW = 3.0    # segundos para considerar "en vivo" la cámara


log = logging.getLogger(__name__)


def _start_worker_thread() -> None:
    """Lanza la vigilancia (YOLO + cámara) en un hilo de fondo del dashboard.

    Así con un solo comando (`serve`) el sistema enciende la cámara solo cuando
    toca según el horario, y la cámara en vivo funciona automáticamente.
    Se puede desactivar con `serve --no-worker` (variable VTQ_WORKER_IN_SERVE=0).
    """
    from .worker import run_worker

    def _run():
        try:
            run_worker(show=False)
        except Exception as e:  # nunca debe tumbar el dashboard
            log.error("El worker de vigilancia se detuvo: %s", e)

    threading.Thread(target=_run, name="vigilancia-worker", daemon=True).start()
    log.info("Worker de vigilancia iniciado dentro del dashboard.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    if os.getenv("VTQ_WORKER_IN_SERVE", "1") == "1":
        _start_worker_thread()
    yield


app = FastAPI(title="Vigilancia Toque de Queda — Dashboard", lifespan=lifespan)


def notifier(instance: str | None = None) -> EvolutionNotifier:
    """Notificador para una instancia concreta (o la del .env por defecto)."""
    return EvolutionNotifier(
        settings.evolution_url,
        instance or settings.evolution_instance,
        settings.evolution_api_key,
        timeout=15,
    )


def _sender_notifier() -> EvolutionNotifier:
    """Notificador apuntando a la instancia EMISORA (la marcada en la BD)."""
    with session_scope() as s:
        name = sender_instance_name(s, settings.evolution_instance)
    return notifier(name)


# --- cache del estado de WhatsApp (evita llamar a Evolution en cada poll) ---
_wa_cache: dict = {"state": "desconocido", "ts": 0.0}


def whatsapp_state_cached(max_age: float = 8.0) -> str:
    if time.time() - _wa_cache["ts"] > max_age:
        n = _sender_notifier()
        _wa_cache["state"] = n.connection_state() if n.is_configured() else "no_config"
        _wa_cache["ts"] = time.time()
    return _wa_cache["state"]


# ---------------------------------------------------------------------------
#  Conversores ORM -> esquema
# ---------------------------------------------------------------------------
def _detection_out(d: Detection) -> DetectionOut:
    try:
        notify = json.loads(d.notify_result or "{}")
    except json.JSONDecodeError:
        notify = {}
    return DetectionOut(
        id=d.id,
        detected_at=d.detected_at,
        persons=d.persons,
        person_id=d.person_id,
        confidence_max=d.confidence_max,
        image_full=d.image_full,
        image_crop=d.image_crop,
        image_thumb=d.image_thumb,
        notify_ok=d.notify_ok,
        notify_result=notify,
    )


def _recipient_out(r: Recipient) -> RecipientOut:
    return RecipientOut(id=r.id, phone=r.phone, label=r.label, active=r.active, created_at=r.created_at)


# ---------------------------------------------------------------------------
#  Estado general
# ---------------------------------------------------------------------------
@app.get("/api/status", response_model=StatusOut)
def get_status() -> StatusOut:
    now = dt.datetime.now()
    with session_scope() as s:
        cfg = get_config(s)
        st = get_system_state(s)
        recents = recent_detections(s, limit=1)
        last = _detection_out(recents[0]) if recents else None
        total = count_detections(s)
        today = detections_since(s, now.replace(hour=0, minute=0, second=0, microsecond=0))
        n_active = len(active_recipients(s))
        hb = st.worker_heartbeat
        online = hb is not None and (now - hb).total_seconds() < WORKER_ONLINE_WINDOW
        in_curfew = within_curfew(now, parse_hhmm(cfg.curfew_start), parse_hhmm(cfg.curfew_end))
        return StatusOut(
            enabled=cfg.enabled,
            in_curfew=in_curfew,
            worker_online=online,
            capturing=st.capturing and online,
            frames_processed=st.frames_processed,
            worker_heartbeat=hb,
            whatsapp_state=whatsapp_state_cached(),
            active_recipients=n_active,
            detections_total=total,
            detections_today=today,
            last_detection=last,
        )


# ---------------------------------------------------------------------------
#  Configuración
# ---------------------------------------------------------------------------
@app.get("/api/config", response_model=ConfigOut)
def read_config() -> ConfigOut:
    with session_scope() as s:
        c = get_config(s)
        return ConfigOut(
            enabled=c.enabled, curfew_start=c.curfew_start, curfew_end=c.curfew_end,
            model=c.model, conf_threshold=c.conf_threshold, imgsz=c.imgsz, device=c.device,
            confirm_frames=c.confirm_frames, cooldown_seconds=c.cooldown_seconds,
            night_enhance=c.night_enhance, send_crop=c.send_crop,
            video_source=c.video_source, alert_message=c.alert_message,
            alert_mode=c.alert_mode, capture_window_ms=c.capture_window_ms,
            updated_at=c.updated_at,
        )


@app.put("/api/config", response_model=ConfigOut)
def update_config(body: ConfigUpdate) -> ConfigOut:
    with session_scope() as s:
        c = get_config(s)
        for field, value in body.model_dump().items():
            setattr(c, field, value)
        s.flush()
    return read_config()


# ---------------------------------------------------------------------------
#  Números (máximo 4 activos)
# ---------------------------------------------------------------------------
@app.get("/api/recipients", response_model=list[RecipientOut])
def list_recipients() -> list[RecipientOut]:
    with session_scope() as s:
        rows = s.query(Recipient).order_by(Recipient.created_at).all()
        return [_recipient_out(r) for r in rows]


def _active_count(s, exclude_id: int | None = None) -> int:
    q = s.query(Recipient).filter(Recipient.active.is_(True))
    if exclude_id is not None:
        q = q.filter(Recipient.id != exclude_id)
    return q.count()


@app.post("/api/recipients", response_model=RecipientOut, status_code=201)
def add_recipient(body: RecipientIn) -> RecipientOut:
    with session_scope() as s:
        if s.query(Recipient).filter(Recipient.phone == body.phone).first():
            raise HTTPException(409, "Ese número ya existe.")
        if body.active and _active_count(s) >= 4:
            raise HTTPException(400, "Máximo 4 números activos. Desactiva uno primero.")
        r = Recipient(phone=body.phone, label=body.label, active=body.active)
        s.add(r)
        s.flush()
        return _recipient_out(r)


@app.put("/api/recipients/{rid}", response_model=RecipientOut)
def edit_recipient(rid: int, body: RecipientIn) -> RecipientOut:
    with session_scope() as s:
        r = s.get(Recipient, rid)
        if r is None:
            raise HTTPException(404, "Número no encontrado.")
        dup = s.query(Recipient).filter(Recipient.phone == body.phone, Recipient.id != rid).first()
        if dup:
            raise HTTPException(409, "Ese número ya existe en otro registro.")
        if body.active and not r.active and _active_count(s, exclude_id=rid) >= 4:
            raise HTTPException(400, "Máximo 4 números activos. Desactiva uno primero.")
        r.phone, r.label, r.active = body.phone, body.label, body.active
        s.flush()
        return _recipient_out(r)


@app.delete("/api/recipients/{rid}", status_code=204)
def delete_recipient(rid: int) -> None:
    with session_scope() as s:
        r = s.get(Recipient, rid)
        if r is None:
            raise HTTPException(404, "Número no encontrado.")
        s.delete(r)


@app.post("/api/recipients/verify")
def verify_recipients() -> dict:
    """Pregunta a WhatsApp si los números activos están registrados.

    Corrige automáticamente el formato si Evolution devuelve un número distinto.
    """
    n = _sender_notifier()
    if not n.is_configured():
        raise HTTPException(400, "EVOLUTION_API_KEY no configurada.")
    if n.connection_state() != "open":
        raise HTTPException(400, "La conexión emisora no está conectada. Escanea el QR primero.")
    with session_scope() as s:
        recs = active_recipients(s)
        if not recs:
            raise HTTPException(400, "No hay números activos.")
        numbers = [r.phone for r in recs]
        try:
            checks = n.check_numbers(numbers)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"No se pudo verificar con WhatsApp: {e}")
        by_num = {c["number"]: c for c in checks}
        out = []
        for idx, r in enumerate(recs):
            c = by_num.get(r.phone) or (checks[idx] if idx < len(checks) else None)
            exists = bool(c and c["exists"])
            corrected = False
            if c and exists and c.get("jid"):
                jid_num = "".join(ch for ch in c["jid"].split("@")[0] if ch.isdigit())
                if jid_num and jid_num != r.phone and 8 <= len(jid_num) <= 15:
                    r.phone = jid_num
                    corrected = True
            out.append({"id": r.id, "phone": r.phone, "exists": exists, "corrected": corrected})
    return {"results": out}


# ---------------------------------------------------------------------------
#  Historial
# ---------------------------------------------------------------------------
@app.get("/api/detections", response_model=list[DetectionOut])
def list_detections(limit: int = 30, offset: int = 0) -> list[DetectionOut]:
    limit = max(1, min(limit, 200))
    with session_scope() as s:
        return [_detection_out(d) for d in recent_detections(s, limit=limit, offset=offset)]


@app.get("/api/detections/{did}", response_model=DetectionOut)
def get_detection(did: int) -> DetectionOut:
    with session_scope() as s:
        d = s.get(Detection, did)
        if d is None:
            raise HTTPException(404, "Detección no encontrada.")
        return _detection_out(d)


@app.delete("/api/detections/{did}", status_code=204)
def delete_detection(did: int) -> None:
    with session_scope() as s:
        d = s.get(Detection, did)
        if d is None:
            raise HTTPException(404, "Detección no encontrada.")
        s.delete(d)


# ---------------------------------------------------------------------------
#  WhatsApp (Evolution API)
# ---------------------------------------------------------------------------
@app.get("/api/whatsapp/state")
def whatsapp_state() -> dict:
    _wa_cache["ts"] = 0.0  # forzar refresco
    return {"state": whatsapp_state_cached()}


@app.get("/api/whatsapp/instances")
def whatsapp_instances() -> dict:
    """Lista las conexiones desde la BD, con el estado en vivo de cada una.

    No usa fetchInstances (devuelve 404 en algunas versiones); consulta el
    estado por instancia, que sí funciona.
    """
    configured = notifier().is_configured()
    with session_scope() as s:
        rows = list_wa_instances(s)
        items = [{"name": i.name, "label": i.label, "is_sender": i.is_sender} for i in rows]
    for it in items:
        it["state"] = notifier(it["name"]).connection_state() if configured else "no_config"
    return {"instances": items, "configured": configured}


@app.post("/api/whatsapp/instances", response_model=WhatsAppConnect)
def whatsapp_add_instance(body: WhatsappInstanceIn) -> WhatsAppConnect:
    """Agrega una conexión nueva (la registra y devuelve el QR para escanear).

    Si no se da nombre, se genera uno solo (emisor-2, emisor-3, ...).
    """
    if not notifier().is_configured():
        raise HTTPException(400, "EVOLUTION_API_KEY no configurada en el .env.")
    with session_scope() as s:
        name = body.name or next_instance_name(s)
        if get_wa_instance(s, name):
            raise HTTPException(409, "Ya existe una conexión con ese nombre.")
        add_wa_instance(s, name, body.label)
    res = notifier(name).ensure_connection()
    res["name"] = name  # devolver el nombre real para el frontend
    _wa_cache["ts"] = 0.0
    return WhatsAppConnect(**res)


@app.post("/api/whatsapp/instances/{name}/connect", response_model=WhatsAppConnect)
def whatsapp_connect_instance(name: str) -> WhatsAppConnect:
    res = notifier(name).ensure_connection()
    _wa_cache["ts"] = 0.0
    return WhatsAppConnect(**res)


@app.get("/api/whatsapp/instances/{name}/state")
def whatsapp_instance_state(name: str) -> dict:
    return {"state": notifier(name).connection_state()}


@app.post("/api/whatsapp/instances/{name}/logout")
def whatsapp_logout_instance(name: str) -> dict:
    ok = notifier(name).logout()
    _wa_cache["ts"] = 0.0
    return {"ok": ok}


@app.post("/api/whatsapp/instances/{name}/sender")
def whatsapp_set_sender(name: str) -> dict:
    with session_scope() as s:
        if not get_wa_instance(s, name):
            raise HTTPException(404, "Conexión no encontrada.")
        set_wa_sender(s, name)
    _wa_cache["ts"] = 0.0
    return {"ok": True, "sender": name}


@app.delete("/api/whatsapp/instances/{name}", status_code=204)
def whatsapp_delete_instance(name: str) -> None:
    notifier(name).delete_instance()  # intenta borrarla en Evolution (no crítico)
    with session_scope() as s:
        if not delete_wa_instance(s, name):
            raise HTTPException(404, "Conexión no encontrada.")
    _wa_cache["ts"] = 0.0


@app.post("/api/whatsapp/test")
def whatsapp_test() -> dict:
    n = _sender_notifier()
    if not n.is_configured():
        raise HTTPException(400, "EVOLUTION_API_KEY no configurada.")
    with session_scope() as s:
        phones = [r.phone for r in active_recipients(s)]
    if not phones:
        raise HTTPException(400, "No hay números activos.")
    msg = (
        "*PRUEBA — Vigilancia toque de queda*\n"
        "Si lees esto, las alertas por WhatsApp funcionan.\n"
        f"Hora: {dt.datetime.now():%Y-%m-%d %H:%M:%S}"
    )
    result = n.notify_all(phones, msg)
    return {"result": result}


# ---------------------------------------------------------------------------
#  Cámara en vivo (frames anotados que escribe el worker)
# ---------------------------------------------------------------------------
@app.get("/api/camera/status")
def camera_status() -> dict:
    if LIVE_PATH.exists():
        age = time.time() - LIVE_PATH.stat().st_mtime
        return {"online": age < LIVE_FRESH_WINDOW, "age": round(age, 1)}
    return {"online": False, "age": None}


def _probe_camera(source: str, timeout: float = 8.0) -> dict:
    """Intenta abrir la fuente y leer un frame. Devuelve {ok, message, ...}."""
    import base64

    result = {"ok": False, "message": "", "width": 0, "height": 0, "thumb": ""}

    def _work():
        import cv2
        # Limita el tiempo de espera de RTSP (evita cuelgues largos).
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000"
        )
        cap = None
        try:
            src = int(source) if str(source).strip().isdigit() else source
            backend = cv2.CAP_DSHOW if isinstance(src, int) else cv2.CAP_FFMPEG
            cap = cv2.VideoCapture(src, backend)
            if not cap.isOpened():
                result["message"] = "No se pudo abrir la fuente (¿URL/índice o red incorrectos?)."
                return
            ok, frame = cap.read()
            if not ok or frame is None:
                result["message"] = "Se abrió la fuente pero no llegó imagen."
                return
            h, w = frame.shape[:2]
            result["width"], result["height"] = int(w), int(h)
            tw = 360
            th = max(1, int(h * tw / w))
            small = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
            okj, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if okj:
                result["thumb"] = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
            result["ok"] = True
            result["message"] = f"¡Conectó! Imagen recibida a {w}x{h}."
        except Exception as e:  # noqa: BLE001
            result["message"] = f"Error: {e}"
        finally:
            if cap is not None:
                cap.release()

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return {"ok": False, "message": "Tiempo agotado: la cámara/URL no respondió.",
                "width": 0, "height": 0, "thumb": ""}
    return result


@app.post("/api/camera/test")
def camera_test(body: CameraTestIn) -> dict:
    """Prueba de conexión de la cámara (webcam o IP), para diagnóstico."""
    source = (body.source or "").strip()
    if not source:
        with session_scope() as s:
            source = get_config(s).video_source or "0"
    return _probe_camera(source)


@app.get("/api/camera/stream")
async def camera_stream() -> StreamingResponse:
    """Transmite el último frame anotado como MJPEG (multipart/x-mixed-replace).

    Lo consume un <img src="/api/camera/stream">. No abre la cámara: solo
    reenvía lo que el worker va escribiendo, así no hay conflicto por la cámara.
    """
    async def generate():
        last_mtime = 0.0
        while True:
            try:
                if LIVE_PATH.exists():
                    mtime = LIVE_PATH.stat().st_mtime
                    if mtime != last_mtime:          # solo enviar frames NUEVOS
                        last_mtime = mtime
                        data = LIVE_PATH.read_bytes()
                        if data:
                            yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                                   b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                                   + data + b"\r\n")
            except Exception:
                pass
            await asyncio.sleep(0.04)              # hasta ~25 fps si hay frames

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


# ---------------------------------------------------------------------------
#  Estáticos: dashboard + evidencias
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/media", StaticFiles(directory=str(IMG_DIR), check_dir=False), name="media")
app.mount("/static", StaticFiles(directory=str(WEB_DIR), check_dir=False), name="static")

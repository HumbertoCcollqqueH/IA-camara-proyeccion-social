"""
Bucle principal de vigilancia.

Flujo:
  cámara -> (¿toque de queda activo?) -> YOLO -> ¿conf >= umbral por N frames?
        -> guarda frame completo + recorte del cuerpo -> WhatsApp -> historial.

La configuración se lee de la base de datos y se refresca cada pocos segundos,
así los cambios del dashboard (Fase 2) se aplican sin reiniciar.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from dataclasses import dataclass

from . import camera
from .curfew import parse_hhmm, seconds_until_curfew, within_curfew
from .db import session_scope
from .detection import PersonDetector
from .notifier import EvolutionNotifier
from .repository import (
    active_recipients,
    get_config,
    mark_worker_started,
    record_detection,
    sender_instance_name,
    touch_heartbeat,
)
from .settings import IMG_DIR, LIVE_DIR, LIVE_PATH, settings

log = logging.getLogger(__name__)

CONFIG_REFRESH_SECONDS = 5


@dataclass
class ConfigSnapshot:
    enabled: bool
    curfew_start: str
    curfew_end: str
    model: str
    conf_threshold: float
    imgsz: int
    device: str
    confirm_frames: int
    cooldown_seconds: int
    night_enhance: bool
    send_crop: bool
    video_source: str
    alert_message: str
    alert_mode: str
    capture_window_ms: int


def _load_snapshot() -> tuple[ConfigSnapshot, list[str]]:
    with session_scope() as s:
        c = get_config(s)
        snap = ConfigSnapshot(
            enabled=c.enabled,
            curfew_start=c.curfew_start,
            curfew_end=c.curfew_end,
            model=c.model,
            conf_threshold=c.conf_threshold,
            imgsz=c.imgsz,
            device=c.device,
            confirm_frames=c.confirm_frames,
            cooldown_seconds=c.cooldown_seconds,
            night_enhance=c.night_enhance,
            send_crop=c.send_crop,
            video_source=c.video_source or "0",
            alert_message=c.alert_message or "",
            alert_mode=(c.alert_mode or "persona"),
            capture_window_ms=int(c.capture_window_ms or 0),
        )
        phones = [r.phone for r in active_recipients(s)]
    return snap, phones


_DEFAULT_ALERT = (
    "*ALERTA — TOQUE DE QUEDA*\n"
    "Persona detectada{id} en la zona vigilada.\n"
    "Personas en escena: {personas}\n"
    "Confianza: {confianza}\n"
    "Fecha: {fecha}\n"
    "Hora: {hora}"
)


def _build_message(template: str, now: dt.datetime, persons: int, conf: float,
                   person_id: int | None = None) -> str:
    """Sustituye los placeholders del mensaje (tolerante a llaves sueltas)."""
    tpl = template.strip() if template and template.strip() else _DEFAULT_ALERT
    id_txt = f" (ID {person_id})" if person_id is not None else ""
    return (
        tpl.replace("{personas}", str(persons))
        .replace("{confianza}", f"{conf:.0%}")
        .replace("{fecha}", f"{now:%Y-%m-%d}")
        .replace("{hora}", f"{now:%H:%M:%S}")
        .replace("{id}", id_txt)
    )


def _best_score(clean, box, conf: float) -> float:
    """Puntaje de calidad de un fotograma de la persona.

    Combina: confianza de la IA (0.6) + nitidez del recorte (0.25, evita fotos
    borrosas/movidas) + tamaño de la persona en el cuadro (0.15, 'más completa').
    """
    import cv2

    try:
        h, w = clean.shape[:2]
        x1, y1, x2, y2 = box
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return float(conf)
        crop = clean[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharp = min(1.0, cv2.Laplacian(gray, cv2.CV_64F).var() / 300.0)
        size = min(1.0, ((x2 - x1) * (y2 - y1)) / float(w * h) / 0.30)
        return 0.6 * float(conf) + 0.25 * sharp + 0.15 * size
    except Exception:
        return float(conf)


def run_worker(test_minutes: int = 0, show: bool = True) -> None:
    import cv2

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    snap, phones = _load_snapshot()
    notifier = EvolutionNotifier(
        settings.evolution_url, settings.evolution_instance, settings.evolution_api_key
    )

    detector = PersonDetector(snap.model, snap.imgsz, snap.device, snap.night_enhance)
    source = camera.build_source(snap.video_source)

    _banner(snap, phones, notifier, source, test_minutes)

    start_t = parse_hhmm(snap.curfew_start)
    end_t = parse_hhmm(snap.curfew_end)
    test_deadline = (
        dt.datetime.now() + dt.timedelta(minutes=test_minutes) if test_minutes > 0 else None
    )

    cap = None
    consec = 0
    last_alert = 0.0
    last_cfg_refresh = time.time()
    last_live = 0.0
    frames_processed = 0
    tracked: dict[int, dict] = {}   # estado por ID de persona (modo "persona")

    with session_scope() as s:
        mark_worker_started(s)

    try:
        while True:
            now = dt.datetime.now()

            # Refrescar configuración y latir periódicamente (cambios del dashboard).
            if time.time() - last_cfg_refresh >= CONFIG_REFRESH_SECONDS:
                snap, phones = _load_snapshot()
                start_t = parse_hhmm(snap.curfew_start)
                end_t = parse_hhmm(snap.curfew_end)
                detector.night_enhance = snap.night_enhance
                last_cfg_refresh = time.time()
                # Si cambió la fuente de cámara desde el dashboard, reconectar.
                new_source = camera.build_source(snap.video_source)
                if new_source != source:
                    source = new_source
                    if cap is not None:
                        cap.release()
                        cap = None
                    log.info("Fuente de cámara cambiada a: %s", source)
                _heartbeat(capturing=cap is not None, frames=frames_processed)

            # ¿Está activo el sistema?
            if test_deadline is not None:
                active = now < test_deadline
                if not active:
                    log.info("Fin del modo prueba (%s min).", test_minutes)
                    break
            else:
                active = snap.enabled and within_curfew(now, start_t, end_t)

            if not active:
                if cap is not None:
                    cap.release()
                    cap = None
                reason = "sistema en pausa" if not snap.enabled else "fuera de horario"
                # Despertar pronto (<=10s) para reaccionar a cambios del dashboard.
                wait = 5 if not snap.enabled else min(10, max(1, seconds_until_curfew(now, start_t)))
                log.info("[%s] %s. Esperando...", now.strftime("%H:%M:%S"), reason)
                time.sleep(wait)
                continue

            # Abrir cámara si hace falta.
            if cap is None:
                cap = camera.open_capture(source)
                if not cap.isOpened():
                    log.warning("No se pudo abrir la cámara. Reintento en 3s...")
                    cap = None
                    time.sleep(3)
                    continue
                log.info("[%s] Cámara activa. Vigilando...", now.strftime("%H:%M:%S"))

            ret, frame = cap.read()
            if not ret or frame is None:
                log.warning("Sin frame. Reconectando en 3s...")
                cap.release()
                cap = None
                time.sleep(3)
                continue

            if show:
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break

            # ---- Detección según el modo ----
            person_mode = snap.alert_mode == "persona"
            if person_mode:
                tr = detector.track(frame, predict_conf=0.25)
                annotated, clean = tr.annotated, tr.clean
                persons, conf_max = tr.persons, tr.conf_max
            else:
                result = detector.infer(frame, predict_conf=0.25)
                annotated, clean = result.annotated, result.clean
                persons, conf_max = result.persons, result.conf_max
            frames_processed += 1

            # Overlays informativos en el frame anotado.
            etiqueta = "PRUEBA" if test_deadline else "TOQUE DE QUEDA ACTIVO"
            cv2.putText(annotated, f"{now:%Y-%m-%d %H:%M:%S}  Personas: {persons}",
                        (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(annotated, f"{etiqueta}  (umbral {snap.conf_threshold:.0%})",
                        (15, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            # Frame en vivo para el dashboard (throttle ~25 fps; el límite real
            # suele ser la velocidad de la detección en CPU).
            if time.time() - last_live >= 0.04:
                _write_live(annotated, cv2)
                last_live = time.time()

            ahora = time.time()
            if person_mode:
                win_s = snap.capture_window_ms / 1000.0
                # 1) Personas visibles: confirmar ID y recolectar su MEJOR fotograma.
                for p in tr.people:
                    if p.track_id is None or p.conf < snap.conf_threshold:
                        continue
                    st = tracked.setdefault(p.track_id, {
                        "frames": 0, "alerted": False, "last_seen": ahora,
                        "collecting": False, "win_start": 0.0, "best": None,
                    })
                    st["frames"] += 1
                    st["last_seen"] = ahora
                    if st["alerted"]:
                        continue
                    if win_s <= 0:
                        # Sin ventana: enviar de inmediato al confirmar.
                        if st["frames"] >= snap.confirm_frames:
                            _dispatch_alert(now, annotated, clean, p.box, persons, p.conf,
                                            p.track_id, snap, phones, notifier, cv2)
                            st["alerted"] = True
                        continue
                    # Con ventana: quedarnos solo con el mejor fotograma (en memoria).
                    if not st["collecting"]:
                        if st["frames"] >= snap.confirm_frames:
                            st["collecting"] = True
                            st["win_start"] = ahora
                            st["best"] = {
                                "score": _best_score(clean, p.box, p.conf), "conf": p.conf,
                                "box": p.box, "persons": persons, "when": now,
                                "annotated": annotated.copy(), "clean": clean.copy(),
                            }
                    else:
                        sc = _best_score(clean, p.box, p.conf)
                        if sc > st["best"]["score"]:
                            st["best"] = {
                                "score": sc, "conf": p.conf, "box": p.box,
                                "persons": persons, "when": now,
                                "annotated": annotated.copy(), "clean": clean.copy(),
                            }
                # 2) Cerrar ventanas cumplidas (aunque ya no se vea a la persona) y alertar.
                for pid, st in tracked.items():
                    if st.get("collecting") and not st["alerted"] and (ahora - st["win_start"]) >= win_s:
                        b = st["best"]
                        _dispatch_alert(b["when"], b["annotated"], b["clean"], b["box"],
                                        b["persons"], b["conf"], pid, snap, phones, notifier, cv2)
                        st["alerted"] = True
                        st["collecting"] = False
                        st["best"] = None  # liberar memoria
                # 3) Olvidar IDs ausentes (permite re-alertar si la persona regresa).
                gap = max(30, snap.cooldown_seconds)
                for pid in [k for k, v in tracked.items() if ahora - v["last_seen"] > gap]:
                    del tracked[pid]
            else:
                # Modo presencia: una alerta y luego enfriamiento.
                qualifies = persons > 0 and conf_max >= snap.conf_threshold
                consec = consec + 1 if qualifies else 0
                if consec >= snap.confirm_frames and (ahora - last_alert) >= snap.cooldown_seconds:
                    _dispatch_alert(now, annotated, clean, result.best_box, persons, conf_max,
                                    None, snap, phones, notifier, cv2)
                    last_alert = ahora
                    consec = 0

            if show:
                cv2.imshow("Vigilancia toque de queda", annotated)

    except KeyboardInterrupt:
        log.info("Detenido por el usuario (Ctrl+C).")
    finally:
        if cap is not None:
            cap.release()
        if show:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
        _heartbeat(capturing=False, frames=frames_processed)


def _write_live(frame, cv2) -> None:
    """Guarda el último frame anotado para la cámara en vivo del dashboard.

    Escribe a un archivo temporal y lo renombra (os.replace es atómico), así el
    dashboard nunca lee un JPEG a medio escribir.
    """
    try:
        h, w = frame.shape[:2]
        if w > 720:
            frame = cv2.resize(frame, (720, int(h * 720 / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ok:
            return
        tmp = LIVE_PATH.with_suffix(".tmp")
        tmp.write_bytes(buf.tobytes())
        os.replace(tmp, LIVE_PATH)
    except Exception:
        pass


def _heartbeat(capturing: bool, frames: int) -> None:
    """Actualiza el latido del worker (el dashboard lo usa para saber si vive)."""
    try:
        with session_scope() as s:
            touch_heartbeat(s, capturing=capturing, frames_processed=frames)
    except Exception as e:
        log.debug("No se pudo actualizar el heartbeat: %s", e)


def _dispatch_alert(now, annotated, clean, box, persons, conf, person_id,
                    snap, phones, notifier, cv2) -> None:
    """Guarda evidencia (frame + recorte + miniatura) y dispara la notificación.

    Sirve para ambos modos: en 'persona' recibe la caja y el ID de esa persona;
    en 'presencia' recibe la caja de mayor confianza y person_id=None.
    """
    stamp = f"{now:%Y%m%d_%H%M%S}"
    suffix = f"_id{person_id}" if person_id is not None else ""
    full_path = IMG_DIR / f"persona_{stamp}{suffix}.jpg"
    cv2.imwrite(str(full_path), annotated)

    crop_path = ""
    if snap.send_crop:
        body = PersonDetector.crop_body(clean, box)
        if body is not None and body.size > 0:
            crop_path = str(IMG_DIR / f"persona_{stamp}{suffix}_crop.jpg")
            cv2.imwrite(crop_path, body)

    # Miniatura para el historial del dashboard (a partir del frame anotado).
    thumb_name = f"persona_{stamp}{suffix}_thumb.jpg"
    try:
        h, w = annotated.shape[:2]
        tw = 320
        th = max(1, int(h * tw / w))
        thumb = cv2.resize(annotated, (tw, th), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(IMG_DIR / thumb_name), thumb)
    except Exception:
        thumb_name = ""

    text = _build_message(snap.alert_message, now, persons, conf, person_id)
    # Imagen a enviar: preferimos el recorte del cuerpo; si no, el frame completo.
    image_for_wa = crop_path or str(full_path)

    log.info("ALERTA -> persona%s, conf %.0f%%. Notificando a %d número(s).",
             f" #{person_id}" if person_id is not None else "", conf * 100, len(phones))

    threading.Thread(
        target=_send_and_log,
        args=(notifier, list(phones), text, image_for_wa, now, persons,
              conf, full_path.name, crop_path, thumb_name, person_id),
        daemon=True,
    ).start()


def _send_and_log(notifier, phones, text, image_for_wa, now, persons, conf,
                  full_name, crop_path, thumb_name, person_id=None):
    if phones and notifier.is_configured():
        # Enviar siempre desde la instancia EMISORA marcada en el dashboard.
        with session_scope() as s:
            sender = sender_instance_name(s, settings.evolution_instance)
        send_notifier = EvolutionNotifier(
            settings.evolution_url, sender, settings.evolution_api_key
        )
        notify_result = send_notifier.notify_all(phones, text, image_for_wa)
    else:
        notify_result = {p: "sin_configurar" for p in phones} or {"-": "sin_destinatarios"}
        log.warning("No se notificó: faltan números o EVOLUTION_API_KEY.")

    crop_name = crop_path.split("/")[-1].split("\\")[-1] if crop_path else ""
    try:
        with session_scope() as s:
            record_detection(
                s,
                detected_at=now,
                persons=persons,
                confidence_max=conf,
                person_id=person_id,
                image_full=full_name,
                image_crop=crop_name,
                image_thumb=thumb_name,
                notify_result=notify_result,
            )
    except Exception as e:  # el historial nunca debe tumbar la vigilancia
        log.error("No se pudo guardar en el historial: %s", e)


def _banner(snap, phones, notifier, source, test_minutes) -> None:
    estado = notifier.connection_state() if notifier.is_configured() else "no configurado"
    print("=" * 64)
    print("   VIGILANCIA DE TOQUE DE QUEDA — YOLO + WhatsApp (Evolution API)")
    print("=" * 64)
    print(f"  Modo               : {'PRUEBA ' + str(test_minutes) + ' min' if test_minutes else 'horario de toque de queda'}")
    print(f"  Horario            : {snap.curfew_start} a {snap.curfew_end}")
    print(f"  Modelo YOLO        : {snap.model}  (umbral notif. {snap.conf_threshold:.0%})")
    print(f"  Fuente de video    : {source}")
    print(f"  WhatsApp (Evolution): instancia '{settings.evolution_instance}' -> estado: {estado}")
    print(f"  Números activos    : {', '.join(phones) if phones else '(ninguno)'}")
    print(f"  Evidencias en      : {IMG_DIR}")
    print("=" * 64)
    print("  Tecla Q = salir (si la ventana está visible)\n")

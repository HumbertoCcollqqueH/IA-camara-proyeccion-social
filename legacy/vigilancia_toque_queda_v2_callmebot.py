#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
===========================================================================
 VIGILANCIA DE TOQUE DE QUEDA  -  Detección de personas con YOLO + WhatsApp
 Versión 2  (configuración de horas fácil + modos de prueba)
===========================================================================
 Curso de IA - UPeU
 Ruta sugerida: F:\Upeu-2026\IA\yolo\vigilancia_toque_queda.py

 ¿QUÉ HACE?
 ----------
 1. Lee una cámara (IP/RTSP o webcam) apuntando a un pasillo.
 2. Solo trabaja dentro de un horario de toque de queda (ej. 22:00 a 04:00).
 3. Detecta PERSONAS con YOLO (por defecto YOLO11, el más estable y recomendado).
 4. Cuando confirma una persona, guarda la foto con fecha y hora.
 5. Envía una alerta GRATIS por WhatsApp (CallMeBot) — sin abrir navegador.
 6. (Opcional) Envía también la foto por Telegram (gratis, con imagen).
 7. Registra todo en un CSV para tu informe.

 -------------------------------------------------------------------------
 LO MÁS IMPORTANTE PARA TI (resumen rápido)
 -------------------------------------------------------------------------
 * DÓNDE PONER TU NÚMERO DE WHATSAPP:  abajo, en CONFIG["wa_phone"]
 * DÓNDE PONER TU APIKEY:              abajo, en CONFIG["wa_apikey"]
 * DÓNDE CONFIGURAR LAS HORAS:         abajo, en CONFIG["curfew_start"] y
                                       CONFIG["curfew_end"]  (formato "HH:MM")
 * VERIFICAR QUE WHATSAPP FUNCIONA:    python vigilancia_toque_queda.py --send-test
 * PROBAR YA POR 5 MINUTOS:            python vigilancia_toque_queda.py --test-minutes 5
 * CAMBIAR HORAS SIN EDITAR ARCHIVO:   python vigilancia_toque_queda.py --start 22:00 --end 04:00

 (La guía completa paso a paso está en el documento Word que acompaña a este archivo.)
===========================================================================
"""

import argparse
import csv
import datetime as dt
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

# ===========================================================================
# >>>>>>>>>>>>>>>>>>>>>  CONFIGURACIÓN  -  EDITA SOLO ESTO  <<<<<<<<<<<<<<<<<<<
# ===========================================================================
CONFIG = {
    # ====================================================================
    # 1) HORARIO DEL TOQUE DE QUEDA    <<< AQUÍ CONFIGURAS LAS HORAS >>>
    # ====================================================================
    # Formato 24 horas "HH:MM".  Ejemplo real: de 22:00 (10 pm) a 04:00 (4 am).
    # Cruzar la medianoche está permitido y bien manejado.
    # PARA PROBAR rápido, lo más cómodo es el comando:  --test-minutes 5
    "curfew_start": "22:00",     # <-- cambia esta hora de inicio
    "curfew_end":   "04:00",     # <-- cambia esta hora de fin

    # Si True, FUERA del horario el script NO procesa video (ahorra CPU/energía)
    # y solo "despierta" cuando entra el horario. Ponlo en False si quieres que
    # la ventana de cámara esté siempre visible mientras pruebas.
    "sleep_outside_curfew": True,

    # ====================================================================
    # 2) WHATSAPP GRATIS (CallMeBot)  <<< AQUÍ PONES TU NÚMERO Y APIKEY >>>
    # ====================================================================
    "use_whatsapp": True,
    "wa_phone":  "+51XXXXXXXXX",          # <-- TU número, con código país (+51 Perú)
    "wa_apikey": "PEGA_AQUI_TU_APIKEY",   # <-- la APIKEY que te dio el bot CallMeBot

    # ====================================================================
    # 3) TELEGRAM (opcional, sirve para que llegue la FOTO de evidencia)
    # ====================================================================
    "use_telegram": False,                # ponlo en True si configuras Telegram
    "tg_token":   "PEGA_AQUI_TU_TOKEN",
    "tg_chat_id": "PEGA_AQUI_TU_CHAT_ID",

    # ====================================================================
    # 4) MODELO YOLO Y DETECCIÓN
    # ====================================================================
    # Recomendado: "yolo11s.pt" (balance). Más precisión: "yolo11m.pt".
    # PC muy lenta: "yolo11n.pt". Evita yolo12* (más lento e inestable en CPU).
    "model": "yolo11s.pt",
    "conf":  0.40,          # Confianza mínima. Sube a 0.50 si hay falsas alarmas.
    "imgsz": 640,           # Resolución de inferencia. 640 va bien.
    "device": "",           # "" = auto. "cpu" fuerza CPU. "0" usa GPU NVIDIA.

    # ====================================================================
    # 5) ANTI-SPAM Y MEJORA NOCTURNA
    # ====================================================================
    "confirm_frames":   4,       # Frames seguidos con persona antes de alertar.
    "cooldown_seconds": 120,     # Segundos mínimos entre alertas (evita repetir).
    "night_enhance":    True,    # Aclara la imagen en pasillos oscuros (CLAHE).

    # ====================================================================
    # 6) CÁMARA IP  (si usas --ip se arma la URL RTSP automáticamente)
    # ====================================================================
    "ip_profile": "hikvision",   # hikvision | dahua | generic
    "ip_channel": "101",         # Hikvision: 101 = principal, 102 = substream.
    "rtsp_port":  554,
}
# ===========================================================================
# >>>>>>>>>>>>>>>>>>>  FIN DE LA CONFIGURACIÓN A EDITAR  <<<<<<<<<<<<<<<<<<<<<<
# ===========================================================================


# Carpetas de salida
BASE = Path.cwd()
OUT_DIR = BASE / "salidas_toque_queda"
IMG_DIR = OUT_DIR / "evidencias"
LOG_DIR = OUT_DIR / "logs"

REQUIRED = ["ultralytics", "opencv-python", "numpy", "requests"]


def setup():
    print("Instalando dependencias necesarias (puede tardar unos minutos)...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", *REQUIRED])
    print("\nListo. Siguientes pasos:")
    print("  1) Configura tu numero/APIKEY en CONFIG.")
    print("  2) Verifica el envio:   python vigilancia_toque_queda.py --send-test")
    print("  3) Prueba 5 minutos:    python vigilancia_toque_queda.py --test-minutes 5")


def ensure_dirs():
    for d in (OUT_DIR, IMG_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ===========================================================================
#  HORARIO: lógica de toque de queda (maneja cruce de medianoche)
# ===========================================================================
def parse_hhmm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def within_curfew(now: dt.datetime, start: dt.time, end: dt.time) -> bool:
    t = now.time()
    if start <= end:                 # mismo día (ej. 08:00 a 18:00)
        return start <= t < end
    return t >= start or t < end     # cruza medianoche (ej. 22:00 a 04:00)


def seconds_until_curfew(now: dt.datetime, start: dt.time) -> float:
    today_start = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if today_start <= now:
        today_start += dt.timedelta(days=1)
    return (today_start - now).total_seconds()


# ===========================================================================
#  CÁMARA IP: construir URL RTSP a partir de --ip
# ===========================================================================
def build_source(args):
    if not args.ip:
        return int(args.source) if str(args.source).isdigit() else args.source
    user = urllib.parse.quote(args.user or "", safe="")
    pwd = urllib.parse.quote(args.password or "", safe="")
    cred = f"{user}:{pwd}@" if user else ""
    ip, port, prof = args.ip, CONFIG["rtsp_port"], CONFIG["ip_profile"]
    if prof == "hikvision":
        return f"rtsp://{cred}{ip}:{port}/Streaming/Channels/{CONFIG['ip_channel']}"
    if prof == "dahua":
        return f"rtsp://{cred}{ip}:{port}/cam/realmonitor?channel=1&subtype=0"
    return f"rtsp://{cred}{ip}:{port}/"


# ===========================================================================
#  ENVÍO DE ALERTAS
# ===========================================================================
def send_whatsapp(text: str) -> bool:
    import requests
    phone = CONFIG["wa_phone"].replace(" ", "")
    apikey = CONFIG["wa_apikey"]
    if not phone or apikey.startswith("PEGA_"):
        print("[WHATSAPP] Falta configurar wa_phone / wa_apikey en CONFIG.")
        return False
    url = (
        "https://api.callmebot.com/whatsapp.php?"
        f"phone={urllib.parse.quote(phone)}"
        f"&text={urllib.parse.quote(text)}"
        f"&apikey={urllib.parse.quote(apikey)}"
    )
    try:
        r = requests.get(url, timeout=25)
        ok = r.status_code == 200
        print(f"[WHATSAPP] {'Enviado' if ok else 'Respuesta'} status={r.status_code}")
        if not ok:
            print("           Detalle:", r.text[:200])
        return ok
    except Exception as e:
        print(f"[WHATSAPP] Error: {e}")
        return False


def send_telegram(text: str, image_path: str = None) -> bool:
    import requests
    token, chat_id = CONFIG["tg_token"], CONFIG["tg_chat_id"]
    if token.startswith("PEGA_") or chat_id.startswith("PEGA_"):
        print("[TELEGRAM] Falta configurar tg_token / tg_chat_id en CONFIG.")
        return False
    try:
        if image_path and Path(image_path).exists():
            with open(image_path, "rb") as f:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={"chat_id": chat_id, "caption": text},
                    files={"photo": f}, timeout=30,
                )
        else:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text}, timeout=20,
            )
        print("[TELEGRAM] Enviado.")
        return True
    except Exception as e:
        print(f"[TELEGRAM] Error: {e}")
        return False


def dispatch_alert(text: str, image_path: str):
    def _run():
        if CONFIG["use_whatsapp"]:
            send_whatsapp(text)
        if CONFIG["use_telegram"]:
            send_telegram(text, image_path)
    threading.Thread(target=_run, daemon=True).start()


def send_test():
    """Manda un mensaje de prueba por los canales activos y termina."""
    now = dt.datetime.now()
    msg = (f"PRUEBA - Vigilancia toque de queda\n"
           f"Si lees esto, las alertas funcionan.\nHora: {now:%Y-%m-%d %H:%M:%S}")
    print("Enviando mensaje(s) de prueba...")
    okw = send_whatsapp(msg) if CONFIG["use_whatsapp"] else None
    okt = send_telegram(msg) if CONFIG["use_telegram"] else None
    print("\nResultado:")
    print("  WhatsApp:", "OK" if okw else ("no enviado" if okw is False else "desactivado"))
    print("  Telegram:", "OK" if okt else ("no enviado" if okt is False else "desactivado"))
    print("\nRevisa tu celular. Si no llego, verifica numero/APIKEY en CONFIG.")


# ===========================================================================
#  MEJORA NOCTURNA DE IMAGEN (CLAHE)
# ===========================================================================
def enhance_night(frame, cv2):
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


# ===========================================================================
#  BANNER DE INICIO  (muestra toda la configuración antes de arrancar)
# ===========================================================================
def print_banner(args, start_t, end_t, test_deadline):
    now = dt.datetime.now()
    if test_deadline:
        activo = "SI (modo prueba)"
        ventana = f"prueba activa hasta {test_deadline:%H:%M:%S}"
    else:
        activo = "SI" if within_curfew(now, start_t, end_t) else "no (esperando horario)"
        ventana = f"{CONFIG['curfew_start']} a {CONFIG['curfew_end']}"
    wa = "configurado" if (CONFIG["use_whatsapp"] and not CONFIG["wa_apikey"].startswith("PEGA_")) else "NO configurado / desactivado"
    tg = "configurado" if (CONFIG["use_telegram"] and not CONFIG["tg_token"].startswith("PEGA_")) else "desactivado"
    print("=" * 64)
    print("   VIGILANCIA DE TOQUE DE QUEDA - YOLO + WhatsApp")
    print("=" * 64)
    print(f"  Hora actual del sistema  : {now:%Y-%m-%d %H:%M:%S}")
    print(f"  Ventana de toque de queda: {ventana}")
    print(f"  Activo ahora?            : {activo}")
    print(f"  Modelo YOLO              : {CONFIG['model']}  (conf={CONFIG['conf']})")
    print(f"  WhatsApp                 : {wa}")
    print(f"  Telegram                 : {tg}")
    print(f"  Mejora nocturna          : {'ON' if CONFIG['night_enhance'] else 'OFF'}")
    print(f"  Evidencias en            : {IMG_DIR}")
    print("=" * 64)
    print("  Teclas:  Q = salir   |   P = pausar/continuar\n")


# ===========================================================================
#  BUCLE PRINCIPAL
# ===========================================================================
def run(args):
    import cv2
    from ultralytics import YOLO

    ensure_dirs()

    # Las horas pueden venir por CONFIG o ser sobrescritas por la línea de comandos.
    if args.start:
        CONFIG["curfew_start"] = args.start
    if args.end:
        CONFIG["curfew_end"] = args.end
    if args.model:
        CONFIG["model"] = args.model

    start_t = parse_hhmm(CONFIG["curfew_start"])
    end_t = parse_hhmm(CONFIG["curfew_end"])

    # Modo prueba por minutos: activa la vigilancia ya, durante N minutos.
    test_deadline = None
    if args.test_minutes and args.test_minutes > 0:
        test_deadline = dt.datetime.now() + dt.timedelta(minutes=args.test_minutes)

    print_banner(args, start_t, end_t, test_deadline)

    print("Cargando modelo YOLO:", CONFIG["model"], "(la 1a vez se descarga)")
    model = YOLO(CONFIG["model"])

    source = build_source(args)
    print("Fuente de video:", source, "\n")

    csv_path = LOG_DIR / f"alertas_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    csv_file = open(csv_path, "w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    writer.writerow(["fecha_hora", "personas", "confianza_max", "evidencia"])

    cap = None
    consec = 0
    last_alert = 0.0
    paused = False

    def open_cap():
        c = cv2.VideoCapture(source, cv2.CAP_DSHOW if isinstance(source, int) else cv2.CAP_FFMPEG)
        if isinstance(source, int):
            c.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            c.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return c

    try:
        while True:
            now = dt.datetime.now()

            # ¿Estamos en modo prueba con cuenta regresiva?
            if test_deadline is not None:
                active = now < test_deadline
                if not active:
                    print(f"\n[PRUEBA] Terminaron los {args.test_minutes} min de prueba.")
                    break
            else:
                active = within_curfew(now, start_t, end_t)

            # --- Fuera de horario: dormir ---
            if not active:
                if cap is not None:
                    cap.release()
                    cap = None
                if CONFIG["sleep_outside_curfew"]:
                    falta_min = int(seconds_until_curfew(now, start_t) // 60)
                    print(f"[{now:%H:%M:%S}] Fuera de toque de queda. "
                          f"Proximo inicio en ~{falta_min} min. (Ctrl+C para salir)")
                    time.sleep(min(60, max(1, seconds_until_curfew(now, start_t))))
                    continue
                time.sleep(1)
                continue

            # --- Dentro de horario: cámara ---
            if cap is None:
                cap = open_cap()
                if not cap.isOpened():
                    print("[CAMARA] No se pudo abrir. Reintentando en 3s...")
                    cap = None
                    time.sleep(3)
                    continue
                print(f"[{now:%H:%M:%S}] Camara activa. Vigilando...")

            ret, frame = cap.read()
            if not ret or frame is None:
                print("[CAMARA] Sin frame. Reconectando en 3s...")
                cap.release()
                cap = None
                time.sleep(3)
                continue

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("p"):
                paused = not paused
                print("[PAUSA]" if paused else "[CONTINUA]")
            if paused:
                continue

            proc = enhance_night(frame, cv2) if CONFIG["night_enhance"] else frame

            results = model.predict(
                source=proc, conf=CONFIG["conf"], classes=[0],
                imgsz=CONFIG["imgsz"], device=CONFIG["device"] or None, verbose=False,
            )
            r = results[0]
            n = len(r.boxes)
            conf_max = float(r.boxes.conf.max()) if n > 0 else 0.0

            annotated = r.plot()
            etiqueta = "PRUEBA" if test_deadline else "TOQUE DE QUEDA ACTIVO"
            cv2.putText(annotated, f"{now:%Y-%m-%d %H:%M:%S}  Personas: {n}",
                        (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(annotated, etiqueta, (15, 65),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            consec = consec + 1 if n > 0 else 0

            ahora = time.time()
            if consec >= CONFIG["confirm_frames"] and (ahora - last_alert) >= CONFIG["cooldown_seconds"]:
                stamp = f"{now:%Y%m%d_%H%M%S}"
                evid = IMG_DIR / f"persona_{stamp}.jpg"
                cv2.imwrite(str(evid), annotated)
                msg = (
                    "ALERTA TOQUE DE QUEDA\n"
                    f"Se detecto {n} persona(s) en el pasillo.\n"
                    f"Hora: {now:%Y-%m-%d %H:%M:%S}\n"
                    f"Confianza: {conf_max:.0%}\n"
                    f"Evidencia: {evid.name}"
                )
                print("\n" + "=" * 50 + f"\n{msg}\n" + "=" * 50 + "\n")
                dispatch_alert(msg, str(evid))
                writer.writerow([f"{now:%Y-%m-%d %H:%M:%S}", n, f"{conf_max:.2f}", evid.name])
                csv_file.flush()
                last_alert = ahora
                consec = 0

            if args.show:
                cv2.imshow("Vigilancia toque de queda", annotated)

    except KeyboardInterrupt:
        print("\nDetenido por el usuario (Ctrl+C).")
    finally:
        if cap is not None:
            cap.release()
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        csv_file.close()
        print(f"Registro guardado en: {csv_path}")


# ===========================================================================
#  ENTRADA / LÍNEA DE COMANDOS
# ===========================================================================
def main():
    p = argparse.ArgumentParser(
        description="Vigilancia de toque de queda con YOLO + WhatsApp gratis",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--setup", action="store_true", help="Instala dependencias y termina.")
    p.add_argument("--send-test", action="store_true",
                   help="Envia un WhatsApp/Telegram de prueba y termina (verifica tu config).")
    p.add_argument("--test-minutes", type=int, default=0,
                   help="Activa la vigilancia AHORA durante N minutos (ignora el horario).")
    p.add_argument("--start", default="", help="Sobrescribe la hora de inicio, ej: 22:00")
    p.add_argument("--end", default="", help="Sobrescribe la hora de fin, ej: 04:00")
    p.add_argument("--model", default="", help="Sobrescribe el modelo, ej: yolo11m.pt")
    p.add_argument("--source", default="0", help="0 = webcam, o ruta/URL de video.")
    p.add_argument("--ip", default="", help="IP de camara (arma RTSP automatico).")
    p.add_argument("--user", default="", help="Usuario de la camara IP.")
    p.add_argument("--password", default="", help="Clave de la camara IP.")
    p.add_argument("--show", action="store_true", default=True, help="Mostrar ventana de video.")
    p.add_argument("--no-show", dest="show", action="store_false",
                   help="No mostrar ventana (modo servidor/desatendido).")
    args = p.parse_args()

    if args.setup:
        setup()
        return
    if args.send_test:
        send_test()
        return
    run(args)


if __name__ == "__main__":
    main()

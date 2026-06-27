"""
Punto de entrada por línea de comandos.

Ejemplos:
    python -m app.main init-db                 # crea tablas y siembra config
    python -m app.main status                  # estado de WhatsApp y config
    python -m app.main send-test               # manda un WhatsApp de prueba
    python -m app.main run --test-minutes 5    # vigila ya, 5 minutos
    python -m app.main run                      # vigila según el horario
    python -m app.main run --no-show           # sin ventana (modo servidor)
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

from .db import init_db, session_scope
from .notifier import EvolutionNotifier
from .repository import active_recipients, get_config
from .settings import settings


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _notifier() -> EvolutionNotifier:
    return EvolutionNotifier(
        settings.evolution_url, settings.evolution_instance, settings.evolution_api_key
    )


def cmd_status(_args) -> None:
    init_db()
    n = _notifier()
    with session_scope() as s:
        cfg = get_config(s)
        phones = [r.phone for r in active_recipients(s)]
    print("Evolution API URL :", settings.evolution_url)
    print("Instancia         :", settings.evolution_instance)
    print("Configurada        :", "sí" if n.is_configured() else "NO (revisa EVOLUTION_API_KEY)")
    print("Estado de conexión:", n.connection_state() if n.is_configured() else "-")
    print("Horario            :", cfg.curfew_start, "a", cfg.curfew_end)
    print("Umbral confianza   :", f"{cfg.conf_threshold:.0%}")
    print("Modelo             :", cfg.model)
    print("Números activos    :", ", ".join(phones) if phones else "(ninguno)")


def cmd_send_test(_args) -> None:
    init_db()
    n = _notifier()
    if not n.is_configured():
        print("ERROR: EVOLUTION_API_KEY no configurada. Edita tu .env.")
        return
    with session_scope() as s:
        phones = [r.phone for r in active_recipients(s)]
    if not phones:
        print("ERROR: no hay números activos. Agrega RECIPIENTS en .env y corre init-db.")
        return
    msg = (
        "*PRUEBA — Vigilancia toque de queda*\n"
        "Si lees esto, las alertas por WhatsApp funcionan.\n"
        f"Hora: {dt.datetime.now():%Y-%m-%d %H:%M:%S}"
    )
    print(f"Enviando prueba a: {', '.join(phones)}")
    result = n.notify_all(phones, msg)
    for phone, status in result.items():
        print(f"  {phone}: {status}")


def cmd_init_db(_args) -> None:
    init_db()
    print("Base de datos lista (tablas creadas y configuración sembrada).")


def cmd_run(args) -> None:
    init_db()
    from .worker import run_worker

    run_worker(test_minutes=args.test_minutes, show=not args.no_show)


def cmd_serve(args) -> None:
    import os
    import uvicorn

    # Por defecto el dashboard también corre la vigilancia (enciende la cámara
    # según el horario). Con --no-worker solo sirve el panel.
    os.environ["VTQ_WORKER_IN_SERVE"] = "0" if args.no_worker else "1"
    init_db()
    print(f"Dashboard en  http://localhost:{args.port}")
    if not args.no_worker:
        print("Vigilancia integrada: la cámara se encenderá sola según el horario.")
    uvicorn.run("app.api:app", host=args.host, port=args.port, reload=args.reload)


def main() -> None:
    _setup_logging()
    p = argparse.ArgumentParser(
        prog="app.main",
        description="Vigilancia de toque de queda — YOLO + WhatsApp (Evolution API).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Crea las tablas y siembra la configuración.").set_defaults(func=cmd_init_db)
    sub.add_parser("status", help="Muestra el estado de WhatsApp y la configuración.").set_defaults(func=cmd_status)
    sub.add_parser("send-test", help="Envía un WhatsApp de prueba a los números activos.").set_defaults(func=cmd_send_test)

    runp = sub.add_parser("run", help="Inicia la vigilancia.")
    runp.add_argument("--test-minutes", type=int, default=0,
                      help="Vigila YA durante N minutos (ignora el horario).")
    runp.add_argument("--no-show", action="store_true",
                      help="No mostrar la ventana de video (modo servidor).")
    runp.set_defaults(func=cmd_run)

    servep = sub.add_parser("serve", help="Inicia el dashboard web (con vigilancia integrada).")
    servep.add_argument("--host", default="127.0.0.1", help="Host (0.0.0.0 para la red local).")
    servep.add_argument("--port", type=int, default=8000, help="Puerto del dashboard.")
    servep.add_argument("--reload", action="store_true", help="Recarga en caliente (desarrollo).")
    servep.add_argument("--no-worker", action="store_true",
                        help="Solo el panel, sin encender la cámara (vigilancia aparte con 'run').")
    servep.set_defaults(func=cmd_serve)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

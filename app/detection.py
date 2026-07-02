"""
Detección de personas con YOLO.

Responsabilidades:
  - cargar el modelo YOLO una sola vez,
  - (opcional) aclarar la imagen de noche con CLAHE,
  - detectar personas (clase 0) y reportar cuántas hay y la confianza máxima,
  - producir el frame anotado completo y el RECORTE del cuerpo completo de la
    persona con mayor confianza (lo que se enviará por WhatsApp).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    persons: int            # cuántas personas se ven (a cualquier confianza)
    conf_max: float         # confianza de la mejor detección
    best_box: tuple | None  # (x1, y1, x2, y2) de la persona con mayor confianza
    annotated: object       # frame completo con las cajas dibujadas (BGR)
    clean: object           # frame procesado SIN cajas (para recortar)


@dataclass
class TrackedPerson:
    track_id: int | None    # ID de seguimiento (persistente entre frames)
    conf: float
    box: tuple              # (x1, y1, x2, y2)


@dataclass
class TrackResult:
    persons: int
    conf_max: float
    people: list            # lista de TrackedPerson
    annotated: object
    clean: object


def enhance_night(frame, cv2):
    """Realza el contraste/luminosidad en pasillos oscuros (CLAHE en canal L)."""
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


class PersonDetector:
    def __init__(self, model_path: str, imgsz: int, device: str, night_enhance: bool):
        from ultralytics import YOLO

        log.info("Cargando modelo YOLO: %s (la 1ª vez se descarga)", model_path)
        self.model = YOLO(model_path)
        self.imgsz = imgsz
        self.device = device or None
        self.night_enhance = night_enhance

    def infer(self, frame, predict_conf: float = 0.25) -> DetectionResult:
        import cv2

        proc = enhance_night(frame, cv2) if self.night_enhance else frame
        results = self.model.predict(
            source=proc,
            conf=predict_conf,
            classes=[0],            # 0 = persona
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )
        r = results[0]
        n = len(r.boxes)

        conf_max = 0.0
        best_box = None
        if n > 0:
            confs = r.boxes.conf
            idx = int(confs.argmax())
            conf_max = float(confs[idx])
            best_box = tuple(int(v) for v in r.boxes.xyxy[idx].tolist())

        return DetectionResult(
            persons=n,
            conf_max=conf_max,
            best_box=best_box,
            annotated=r.plot(),
            clean=proc,
        )

    def track(self, frame, predict_conf: float = 0.25) -> TrackResult:
        """Como infer(), pero asigna un ID de seguimiento a cada persona.

        Usa ByteTrack (integrado en Ultralytics) con persist=True para mantener
        los IDs entre frames. Permite alertar una vez por cada persona nueva.
        """
        import cv2

        proc = enhance_night(frame, cv2) if self.night_enhance else frame
        results = self.model.track(
            source=proc,
            conf=predict_conf,
            classes=[0],
            imgsz=self.imgsz,
            device=self.device,
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )
        r = results[0]
        people: list[TrackedPerson] = []
        for b in r.boxes:
            tid = int(b.id.item()) if b.id is not None else None
            people.append(TrackedPerson(
                track_id=tid,
                conf=float(b.conf.item()),
                box=tuple(int(v) for v in b.xyxy[0].tolist()),
            ))
        conf_max = max((p.conf for p in people), default=0.0)
        return TrackResult(
            persons=len(people),
            conf_max=conf_max,
            people=people,
            annotated=r.plot(),
            clean=proc,
        )

    @staticmethod
    def crop_body(clean_frame, box: tuple, margin: float = 0.08):
        """Recorta el cuerpo completo (la caja de la persona) con un margen.

        `box` es (x1, y1, x2, y2). Devuelve el subframe BGR o None.
        """
        if box is None:
            return None
        h, w = clean_frame.shape[:2]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        mx, my = int(bw * margin), int(bh * margin)
        x1 = max(0, x1 - mx)
        y1 = max(0, y1 - my)
        x2 = min(w, x2 + mx)
        y2 = min(h, y2 + my)
        if x2 <= x1 or y2 <= y1:
            return None
        return clean_frame[y1:y2, x1:x2]

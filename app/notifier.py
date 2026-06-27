"""
Cliente de Evolution API (WhatsApp).

Evolution API es un gateway open-source que se autohospeda en Docker. A
diferencia de CallMeBot (que solo manda texto a tu propio número), permite
enviar texto E IMÁGENES a varios números, gratis, usando el protocolo de
WhatsApp Web (Baileys).

Endpoints usados (Evolution API v2):
    POST {url}/message/sendText/{instance}
    POST {url}/message/sendMedia/{instance}
Autenticación por cabecera:  apikey: <EVOLUTION_API_KEY>
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class EvolutionNotifier:
    def __init__(self, base_url: str, instance: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.instance = instance
        self.api_key = api_key
        self.timeout = timeout

    # -- helpers -----------------------------------------------------------
    @property
    def _headers(self) -> dict[str, str]:
        return {"apikey": self.api_key, "Content-Type": "application/json"}

    @staticmethod
    def _clean_number(number: str) -> str:
        """Solo dígitos, sin '+', espacios ni guiones."""
        return "".join(ch for ch in number if ch.isdigit())

    def is_configured(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("cambia-")

    # -- conexión / estado -------------------------------------------------
    def connection_state(self) -> str:
        """Devuelve 'open' si la instancia está conectada a WhatsApp."""
        url = f"{self.base_url}/instance/connectionState/{self.instance}"
        try:
            r = requests.get(url, headers=self._headers, timeout=self.timeout)
            if r.status_code == 200:
                return r.json().get("instance", {}).get("state", "unknown")
            if r.status_code == 404:
                return "not_found"
            return f"http_{r.status_code}"
        except requests.RequestException as e:
            return f"error: {e}"

    def _create_instance(self) -> dict:
        """Crea la instancia de WhatsApp (devuelve también el primer QR)."""
        url = f"{self.base_url}/instance/create"
        payload = {
            "instanceName": self.instance,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
        }
        r = requests.post(url, json=payload, headers=self._headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _connect(self) -> dict:
        """Pide el QR de una instancia ya existente."""
        url = f"{self.base_url}/instance/connect/{self.instance}"
        r = requests.get(url, headers=self._headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _extract_qr(data: dict) -> str:
        """Saca el QR en base64 (data-URI) de las distintas formas que devuelve la API."""
        qr = data.get("qrcode") or data
        base64_qr = qr.get("base64") or ""
        if base64_qr and not base64_qr.startswith("data:"):
            base64_qr = f"data:image/png;base64,{base64_qr}"
        return base64_qr

    def ensure_connection(self) -> dict:
        """Orquesta la conexión para el dashboard.

        Devuelve un dict:
          {state, qr (data-URI o ""), pairing_code, message}
        Si ya está conectado -> state='open'. Si no, intenta dar un QR para
        escanear (creando la instancia si aún no existe).
        """
        if not self.is_configured():
            return {"state": "no_config", "qr": "", "pairing_code": "",
                    "message": "Falta configurar EVOLUTION_API_KEY."}
        try:
            state = self.connection_state()
            if state == "open":
                return {"state": "open", "qr": "", "pairing_code": "",
                        "message": "WhatsApp ya está conectado."}

            try:
                data = self._connect()
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    data = self._create_instance()  # no existía: crear
                else:
                    raise

            qr = self._extract_qr(data)
            pairing = (data.get("qrcode") or data).get("pairingCode", "") or ""
            if qr or pairing:
                return {"state": "connecting", "qr": qr, "pairing_code": pairing,
                        "message": "Escanea el QR con WhatsApp ▸ Dispositivos vinculados."}
            return {"state": state, "qr": "", "pairing_code": "",
                    "message": "No se obtuvo QR. Revisa el panel en :8080/manager."}
        except requests.RequestException as e:
            return {"state": "error", "qr": "", "pairing_code": "",
                    "message": f"No se pudo contactar Evolution API: {e}"}

    def logout(self) -> bool:
        """Desvincula el WhatsApp de la instancia."""
        url = f"{self.base_url}/instance/logout/{self.instance}"
        try:
            r = requests.delete(url, headers=self._headers, timeout=self.timeout)
            return r.status_code in (200, 201)
        except requests.RequestException:
            return False

    def delete_instance(self) -> bool:
        """Elimina por completo la instancia en Evolution API."""
        url = f"{self.base_url}/instance/delete/{self.instance}"
        try:
            r = requests.delete(url, headers=self._headers, timeout=self.timeout)
            return r.status_code in (200, 201)
        except requests.RequestException:
            return False

    def list_instances(self) -> list[dict]:
        """Lista las instancias (WhatsApps) registradas en Evolution API.

        Devuelve [{name, state, number, profile}]. Cada instancia = un WhatsApp.
        """
        url = f"{self.base_url}/instance/fetchInstances"
        r = requests.get(url, headers=self._headers, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        out: list[dict] = []
        for item in data if isinstance(data, list) else []:
            inst = item.get("instance", item)  # v1 anida en "instance"; v2 es plano
            owner = inst.get("ownerJid") or inst.get("owner") or ""
            number = owner.split("@")[0] if "@" in owner else (owner or inst.get("number") or "")
            out.append({
                "name": inst.get("name") or inst.get("instanceName") or "?",
                "state": inst.get("connectionStatus") or inst.get("state") or inst.get("status") or "?",
                "number": number,
                "profile": inst.get("profileName") or "",
            })
        return out

    # -- envío -------------------------------------------------------------
    def send_text(self, number: str, text: str) -> tuple[bool, str]:
        url = f"{self.base_url}/message/sendText/{self.instance}"
        payload = {"number": self._clean_number(number), "text": text}
        return self._post(url, payload, number, "texto")

    def send_image(self, number: str, image_path: str, caption: str = "") -> tuple[bool, str]:
        path = Path(image_path)
        if not path.exists():
            log.warning("[WA->%s] imagen no encontrada: %s", number, image_path)
            return self.send_text(number, caption)  # al menos que llegue el texto

        media_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        url = f"{self.base_url}/message/sendMedia/{self.instance}"
        payload = {
            "number": self._clean_number(number),
            "mediatype": "image",
            "mimetype": "image/jpeg",
            "caption": caption,
            "media": media_b64,
            "fileName": path.name,
        }
        return self._post(url, payload, number, "imagen")

    def _post(self, url: str, payload: dict, number: str, kind: str) -> tuple[bool, str]:
        try:
            r = requests.post(url, json=payload, headers=self._headers, timeout=self.timeout)
            if r.status_code in (200, 201):
                log.info("[WA->%s] %s enviado.", number, kind)
                return True, ""
            detail = self._error_detail(r)
            log.error("[WA->%s] %s falló (%s): %s", number, kind, r.status_code, detail)
            return False, detail
        except requests.RequestException as e:
            log.error("[WA->%s] %s error: %s", number, kind, e)
            return False, str(e)

    @staticmethod
    def _error_detail(r) -> str:
        """Devuelve el motivo REAL que reporta Evolution (sin adivinar)."""
        try:
            data = r.json()
            msg = data.get("response") or data.get("message") or data.get("error") or data
            if isinstance(msg, dict):
                msg = msg.get("message") or msg
            if isinstance(msg, list):
                msg = "; ".join(str(m) for m in msg)
            text = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
        except Exception:
            text = r.text or f"HTTP {r.status_code}"
        return text.strip()[:200] or f"HTTP {r.status_code}"

    def check_numbers(self, numbers: list[str]) -> list[dict]:
        """Pregunta a WhatsApp si cada número está registrado.

        Usa POST /chat/whatsappNumbers/{instance}. Devuelve
        [{number, exists, jid}] donde jid trae el número con el formato correcto.
        Requiere una instancia conectada (la emisora).
        """
        url = f"{self.base_url}/chat/whatsappNumbers/{self.instance}"
        payload = {"numbers": [self._clean_number(n) for n in numbers]}
        r = requests.post(url, json=payload, headers=self._headers, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        out: list[dict] = []
        for item in data if isinstance(data, list) else []:
            out.append({
                "number": self._clean_number(str(item.get("number", ""))),
                "exists": bool(item.get("exists")),
                "jid": item.get("jid") or "",
            })
        return out

    def notify_all(
        self,
        numbers: list[str],
        text: str,
        image_path: str | None = None,
    ) -> dict[str, str]:
        """Envía a cada número. Devuelve {numero: 'ok' | '<motivo del error>'}.

        Si hay imagen, se envía como foto con el texto de pie (una sola
        notificación por número). Si no, se envía solo texto.
        """
        result: dict[str, str] = {}
        for number in numbers:
            if image_path:
                ok, detail = self.send_image(number, image_path, caption=text)
            else:
                ok, detail = self.send_text(number, text)
            result[number] = "ok" if ok else (detail or "error")
        return result

# 🎥 Vigilancia de Toque de Queda — IA (YOLO) + Notificación a WhatsApp

Sistema de vigilancia que **detecta personas con Inteligencia Artificial (YOLO)**
durante un horario de toque de queda y, cuando confirma una persona con alta
confianza, envía una **alerta con foto por WhatsApp** a varios encargados. Todo
se administra desde un **dashboard web** (sin tocar código).

> Proyecto del curso de Inteligencia Artificial — Universidad Peruana Unión (UPeU).

---

## ✨ ¿Qué hace?

- 📷 Lee una **cámara** (webcam o cámara IP/RTSP) y solo vigila dentro del
  **horario** que configures.
- 🧠 Detecta **personas** con YOLO y alerta solo si la confianza supera tu
  **umbral** (por defecto 90%).
- 📲 Envía **alerta con imagen** (recorte del cuerpo + foto completa) por
  **WhatsApp** a **hasta 4 números**, gratis, mediante **Evolution API**.
- 🖥️ **Dashboard web** para configurar horario, números, modelo, ver la
  **cámara en vivo** y un **historial** con miniaturas — todo desde el navegador.
- 🟢 La cámara **se enciende y apaga sola** según el horario.

---

## ✅ Requisitos (qué necesitas)

| Necesitas | Para qué |
|---|---|
| **Windows 10/11** | El sistema fue probado en Windows |
| **Python 3.10 o superior** | Correr la IA y el dashboard ([descargar](https://www.python.org/downloads/) — marca *"Add Python to PATH"*) |
| **Docker Desktop** | Correr WhatsApp (Evolution API) + base de datos. [Descargar](https://www.docker.com/products/docker-desktop/) |
| **Una cámara** | Webcam de la laptop o cámara IP (Hikvision/Dahua) |
| **Un teléfono con WhatsApp** | Será el que **envía** las alertas (se vincula con un QR) |
| **Internet** | La primera vez descarga la IA y las librerías |

---

## 🚀 Instalación (primera vez que clonas el proyecto)

Abre **CMD** (símbolo del sistema) y ejecuta paso a paso:

### 1) Clonar el proyecto
```cmd
git clone https://github.com/HumbertoCcollqqueH/IA-camara-proyeccion-social.git
cd IA-camara-proyeccion-social
```

### 2) Crear el entorno de Python e instalar librerías
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```
> Si `activate.bat` te da error, puedes saltarlo y usar siempre
> `.venv\Scripts\python.exe -m ...` en lugar de `python -m ...`.
>
> La **primera vez** esto descarga PyTorch, OpenCV y YOLO (varios cientos de MB,
> puede tardar). El modelo de IA (`yolo11s.pt`) se descarga solo al primer uso.

### 3) Configurar tus datos (archivo `.env`)
```cmd
copy .env.example .env
notepad .env
```
En el `.env` edita:
- **`EVOLUTION_API_KEY`** — una contraseña que **tú inventas** (la misma se usa
  para Docker y para la app). Genera una aleatoria con:
  ```cmd
  .venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(24))"
  ```
- **`RECIPIENTS`** — tus números a notificar (solo dígitos, con código de país;
  formato Perú: `51XXXXXXXXX`, reemplaza las X por tu número). También puedes
  agregarlos después desde la web.

> ⚠️ El `.env` contiene tus claves y números: **nunca lo subas a internet**.
> Ya está excluido del repositorio.

### 4) Levantar la infraestructura (Docker)
Con **Docker Desktop abierto**:
```cmd
docker compose up -d
```
Esto inicia **Evolution API** (WhatsApp), **PostgreSQL** (datos) y **Redis**.

### 5) Instalar dependencias (si no lo hiciste en el paso 2)
Ya están instaladas. Continúa.

---

## 🖥️ Cómo prender la web

```cmd
.venv\Scripts\python.exe -m app.main serve
```
Abre 👉 **http://localhost:8000**

> Con un **solo comando** arranca el dashboard **y** la vigilancia: la cámara se
> encenderá sola cuando entre el horario configurado. Para acceder desde otra PC
> de la red local: `... serve --host 0.0.0.0`.

### Conectar tu WhatsApp (la primera vez)
1. En el dashboard, entra a la pestaña **WhatsApp**.
2. En tu conexión, pulsa **Conectar / QR**.
3. Escanea el QR con el teléfono que **enviará** las alertas
   (WhatsApp ▸ *Dispositivos vinculados* ▸ *Vincular dispositivo*).
4. Cuando diga **Conectado**, ya está listo. Prueba con **Enviar prueba**.

---

## 🧭 Uso del dashboard

| Pestaña | Para qué sirve |
|---|---|
| **Resumen** | Estado de WhatsApp, cámara y horario; alertas de hoy y la última alerta |
| **Historial** | Todas las detecciones con miniatura; clic para ver foto completa, recorte y a quién se notificó |
| **Configuración** | Horario, **umbral de confianza**, modelo de IA, fuente de cámara (webcam o IP), mensaje de la alerta… (cada campo explica qué hace) |
| **Números** | Agregar/activar **hasta 4** números y **verificar** si tienen WhatsApp |
| **WhatsApp** | Conectar uno o varios teléfonos emisores por **QR**, elegir cuál envía |
| **Cámara en vivo** | Ver el video con las **detecciones de YOLO en tiempo real** |

> El **interruptor "Sistema"** (abajo a la izquierda) pausa o activa todo.

### Probar la detección ya mismo (sin esperar al horario)
En otra ventana de CMD:
```cmd
.venv\Scripts\python.exe -m app.main run --test-minutes 30
```

---

## 🧠 Modelos de IA (YOLO)

Puedes elegir el modelo desde **Configuración**. Se descarga solo la primera vez:

- **YOLO11** (recomendado): `n` rápido · `s` equilibrio ⭐ · `m` más preciso.
- **YOLOv8**: muy probado y estable.
- **YOLOv10**: más rápido (sin NMS), ideal para más fluidez.

Para detectar *solo personas*, el **tamaño** (n/s/m) y la **resolución** influyen
más que la versión. En CPU usa `yolo11s`; con GPU NVIDIA, `yolo11m`/`yolov8m`.

---

## 📦 Comandos útiles

| Comando | Qué hace |
|---|---|
| `python -m app.main serve` | Inicia el **dashboard + vigilancia** (`:8000`) |
| `python -m app.main serve --no-worker` | Solo el panel (sin encender la cámara) |
| `python -m app.main run --test-minutes 30` | Vigila YA durante 30 min (para probar) |
| `python -m app.main run --no-show` | Vigilancia sin ventana (proceso aparte) |
| `python -m app.main status` | Muestra el estado en la consola |
| `python -m app.main init-db` | Crea/actualiza las tablas de la base de datos |

(Antepón `.venv\Scripts\` a `python` si no activaste el entorno.)

---

## 🗂️ Estructura del proyecto

```
.
├── docker-compose.yml      # Evolution API + PostgreSQL + Redis
├── .env.example            # plantilla de configuración (cópiala a .env)
├── requirements.txt        # librerías de Python
├── app/                    # backend (Python)
│   ├── settings.py         # configuración desde .env
│   ├── db.py               # base de datos + migraciones automáticas
│   ├── models.py           # tablas (config, números, detecciones, WhatsApp)
│   ├── repository.py       # consultas
│   ├── notifier.py         # cliente de Evolution API (texto, imagen, QR)
│   ├── camera.py           # webcam / cámara IP (RTSP)
│   ├── curfew.py           # lógica del horario
│   ├── detection.py        # YOLO + recorte del cuerpo
│   ├── worker.py           # motor de vigilancia (bucle de detección)
│   ├── schemas.py          # validación de la API
│   ├── api.py              # dashboard + API REST (FastAPI)
│   └── main.py             # comandos (serve, run, status, init-db)
├── web/                    # dashboard (index.html, styles.css, app.js)
├── legacy/                 # versión anterior (referencia)
└── salidas_toque_queda/    # evidencias generadas (no se sube)
```

---

## 🔧 Solución de problemas

- **"Falta configurar EVOLUTION_API_KEY"** → no creaste el `.env` o dejaste el
  valor de ejemplo. Crea el `.env` y pon tu clave (paso 3). Reinicia `serve`.
- **No conecta WhatsApp / no llega la prueba** → revisa que `docker compose ps`
  muestre los 3 servicios arriba, y que la conexión diga **Conectado**.
- **Un número falla al enviar** → en **Números** pulsa *Verificar en WhatsApp*:
  te dirá si ese número está registrado (y corrige el formato si hace falta).
- **La cámara en vivo dice "Sin señal"** → el sistema vigila solo dentro del
  horario; para verla ya, usa `run --test-minutes 30`, o ajusta el horario.
- **Va lento / pocos cuadros** → en *Configuración* baja la **Resolución (imgsz)**
  a 480 y/o usa el modelo `yolo11n`.

---

## 🔒 Notas y seguridad

- El dashboard **no tiene contraseña**: úsalo en `localhost` o red interna de
  confianza. Si lo expones a internet, ponlo detrás de un proxy con autenticación.
- **No subas tu `.env`** (ya está excluido).
- **Evolution API** usa el protocolo de WhatsApp Web (Baileys): gratis, pero no
  oficial. Para un despliegue institucional formal, valórese la *WhatsApp Cloud
  API* de Meta.
- Toda captura de imágenes de personas debe respetar las normas de la institución
  sobre videovigilancia y protección de datos.

---

## 🧰 Tecnologías

Python · FastAPI · Ultralytics YOLO11 · OpenCV · PostgreSQL · Redis ·
Evolution API · Docker · HTML/CSS/JS (sin frameworks).

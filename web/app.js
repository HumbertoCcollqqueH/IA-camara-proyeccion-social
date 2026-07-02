/* ===========================================================================
   Dashboard — Vigilancia Toque de Queda
   Vanilla JS, sin dependencias. Habla con la API REST de FastAPI.
   =========================================================================== */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

let configCache = null;
let hist = { offset: 0, limit: 30, done: false };
let qrPoll = null;
let camTimer = null;
let verifyMap = {};   // { recipientId: {exists, corrected} }

/* ---------------------------- API helpers ---------------------------- */
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail || data.message || `Error ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}
const apiGet = (p) => api("GET", p);

/* ---------------------------- Toasts ---------------------------- */
function toast(msg, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 250); }, 3200);
}

/* ---------------------------- Utils ---------------------------- */
const mediaUrl = (name) => name ? `/media/${name}` : "";
const pad = (n) => String(n).padStart(2, "0");

function fmtDateTime(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function fmtTime(iso) {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function confClass(c) { return c >= 0.9 ? "hi" : c >= 0.7 ? "mid" : "lo"; }
function pct(c) { return Math.round(c * 100) + "%"; }

const WA_LABELS = {
  open: ["Conectado", "ok"], connecting: ["Conectando…", "warn"],
  close: ["Desconectado", "bad"], not_found: ["Sin vincular", "warn"],
  no_config: ["Sin configurar", "bad"],
};
function waLabel(state) {
  if (WA_LABELS[state]) return WA_LABELS[state];
  if (typeof state === "string" && (state.startsWith("http_") || state.startsWith("error")))
    return ["Sin conexión a Evolution", "bad"];
  return [state || "—", "neutral"];
}

/* ---------------------------- Navegación ---------------------------- */
const TITLES = { resumen: "Resumen", historial: "Historial", config: "Configuración", numeros: "Números", whatsapp: "WhatsApp", camara: "Cámara en vivo" };
function showView(view) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  $("#pageTitle").textContent = TITLES[view] || "";
  stopCamera();  // detén el stream al salir de cualquier vista
  if (view === "historial") loadHistory(true);
  if (view === "numeros") loadRecipients();
  if (view === "whatsapp") loadWaInstances();
  if (view === "config" && configCache) fillConfigForm(configCache);
  if (view === "camara") startCamera();
}
$$(".nav-item").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
$$("[data-goto]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.goto)));

/* ---------------------------- Reloj ---------------------------- */
setInterval(() => {
  const d = new Date();
  $("#clock").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}, 1000);

/* ---------------------------- Estado (poll) ---------------------------- */
async function loadStatus() {
  let st;
  try { st = await apiGet("/api/status"); } catch { return; }

  // Sidebar
  $("#masterToggle").checked = st.enabled;
  $("#masterState").textContent = st.enabled ? "Activo" : "Pausado";
  $("#workerDot").className = "status-dot " + (st.worker_online ? "on" : "off");
  $("#workerText").textContent = "Worker: " + (st.worker_online ? "en línea" : "detenido");

  // Tarjetas
  const [waText, waKind] = waLabel(st.whatsapp_state);
  const waEl = $("#statWa");
  waEl.textContent = waText;
  waEl.className = "stat-value conf " + (waKind === "ok" ? "hi" : waKind === "warn" ? "mid" : "lo");
  $("#statWorker").textContent = st.worker_online ? "En línea" : "Detenido";
  $("#statWorkerSub").textContent = st.capturing ? "capturando video" : "en espera";
  $("#statCurfew").textContent = st.in_curfew ? "Activo" : "Inactivo";
  if (configCache) $("#statCurfewSub").textContent = `${configCache.curfew_start} – ${configCache.curfew_end}`;
  $("#statToday").textContent = st.detections_today;
  $("#statTotal").textContent = `${st.detections_total} en total`;

  renderLastAlert(st.last_detection);
}
function setStat(id, text) { $("#" + id).textContent = text; }

function renderLastAlert(d) {
  const box = $("#lastAlert");
  if (!d) { box.className = "last-alert empty"; box.textContent = "Sin alertas todavía."; return; }
  box.className = "last-alert";
  const img = mediaUrl(d.image_thumb || d.image_full);
  box.innerHTML = `
    ${img ? `<img src="${img}" alt="evidencia" data-full="${d.id}">` : ""}
    <div class="la-meta">
      <span class="big">${d.persons} persona(s)</span>
      <span class="conf ${confClass(d.confidence_max)}">Confianza ${pct(d.confidence_max)}</span>
      <span class="muted">${fmtDateTime(d.detected_at)}</span>
      <span class="pill ${d.notify_ok ? "ok" : "bad"}">${d.notify_ok ? "Notificado" : "No notificado"}</span>
    </div>`;
  const im = $("img", box);
  if (im) im.addEventListener("click", () => openDetection(d.id));
}

/* ---------------------------- Actividad reciente ---------------------------- */
async function loadRecent() {
  let rows;
  try { rows = await apiGet("/api/detections?limit=6"); } catch { return; }
  const list = $("#recentList");
  if (!rows.length) { list.innerHTML = `<div class="muted">Aún no hay detecciones.</div>`; return; }
  list.innerHTML = rows.map((d) => {
    const img = mediaUrl(d.image_thumb || d.image_full);
    return `<div class="recent-row">
      ${img ? `<img src="${img}" data-id="${d.id}" alt="">` : ""}
      <div class="r-main">
        <div class="r-time">${fmtTime(d.detected_at)} · ${d.persons} pers.</div>
        <div class="r-sub">Confianza ${pct(d.confidence_max)} · ${d.notify_ok ? "notificado" : "sin enviar"}</div>
      </div>
      <span class="conf ${confClass(d.confidence_max)}">${pct(d.confidence_max)}</span>
    </div>`;
  }).join("");
  $$("#recentList img").forEach((im) => im.addEventListener("click", () => openDetection(+im.dataset.id)));
}

/* ---------------------------- Historial ---------------------------- */
async function loadHistory(reset) {
  if (reset) { hist = { offset: 0, limit: 30, done: false }; $("#histGrid").innerHTML = ""; }
  if (hist.done) return;
  let rows;
  try { rows = await apiGet(`/api/detections?limit=${hist.limit}&offset=${hist.offset}`); }
  catch (e) { toast(e.message, "err"); return; }

  const grid = $("#histGrid");
  if (reset && !rows.length) { grid.innerHTML = `<div class="muted">Aún no hay detecciones registradas.</div>`; }
  grid.insertAdjacentHTML("beforeend", rows.map(histCard).join(""));
  hist.offset += rows.length;
  hist.done = rows.length < hist.limit;
  $("#loadMore").hidden = hist.done;
  $("#histCount").textContent = `${hist.offset} mostradas`;

  $$("#histGrid .hist-card img").forEach((im) => {
    if (im.dataset.bound) return; im.dataset.bound = "1";
    im.addEventListener("click", () => openDetection(+im.dataset.id));
  });
  $$("#histGrid [data-del]").forEach((b) => {
    if (b.dataset.bound) return; b.dataset.bound = "1";
    b.addEventListener("click", () => deleteDetection(+b.dataset.del));
  });
}
function histCard(d) {
  const img = mediaUrl(d.image_thumb || d.image_full);
  return `<div class="hist-card">
    ${img ? `<img src="${img}" data-id="${d.id}" alt="">` : `<div class="muted" style="padding:20px">sin imagen</div>`}
    <div class="hc-body">
      <div class="hc-time">${fmtDateTime(d.detected_at)} ${d.person_id != null ? `<span class="vbadge ok">ID ${d.person_id}</span>` : ""}</div>
      <div class="hc-row">
        <span>${d.persons} persona(s)</span>
        <span class="conf ${confClass(d.confidence_max)}">${pct(d.confidence_max)}</span>
      </div>
      <div class="hc-row">
        <span class="pill ${d.notify_ok ? "ok" : "bad"}">${d.notify_ok ? "Notificado" : "No enviado"}</span>
      </div>
      <div class="hc-actions">
        <button class="btn ghost sm" data-id="${d.id}" onclick="openDetection(${d.id})">Ver</button>
        <button class="btn danger ghost sm" data-del="${d.id}">Eliminar</button>
      </div>
    </div>
  </div>`;
}
$("#loadMore").addEventListener("click", () => loadHistory(false));

async function deleteDetection(id) {
  if (!confirm("¿Eliminar esta detección del historial?")) return;
  try { await api("DELETE", `/api/detections/${id}`); toast("Detección eliminada."); loadHistory(true); loadRecent(); }
  catch (e) { toast(e.message, "err"); }
}

/* ---------------------------- Modal detalle ---------------------------- */
async function openDetection(id) {
  let d;
  try { d = await apiGet(`/api/detections/${id}`); } catch (e) { return toast(e.message, "err"); }
  const full = mediaUrl(d.image_full);
  const crop = mediaUrl(d.image_crop);
  const notify = Object.entries(d.notify_result || {})
    .map(([num, st]) => `<span class="pill ${st === "ok" ? "ok" : "bad"}">${num}: ${st}</span>`).join(" ") || "<span class='muted'>—</span>";
  $("#modalContent").innerHTML = `
    <div class="modal-grid">
      <div>${full ? `<img src="${full}" alt="evidencia completa">` : "<div class='muted'>sin imagen</div>"}</div>
      <div>
        <div class="kv"><span>Fecha y hora</span><strong>${fmtDateTime(d.detected_at)}</strong></div>
        ${d.person_id != null ? `<div class="kv"><span>ID de persona</span><strong>#${d.person_id}</strong></div>` : ""}
        <div class="kv"><span>Personas</span><strong>${d.persons}</strong></div>
        <div class="kv"><span>Confianza</span><strong class="conf ${confClass(d.confidence_max)}">${pct(d.confidence_max)}</strong></div>
        <div class="kv"><span>Notificación</span><strong>${d.notify_ok ? "enviada" : "no enviada"}</strong></div>
        <div style="margin-top:10px">${notify}</div>
        ${crop ? `<div class="crop" style="margin-top:14px"><div class="muted">Recorte enviado:</div><img src="${crop}" alt="recorte"></div>` : ""}
      </div>
    </div>`;
  openModal();
}
function openModal() { $("#modal").hidden = false; }
function closeModal() { $("#modal").hidden = true; $("#modalContent").innerHTML = ""; }
$$("#modal [data-close]").forEach((el) => el.addEventListener("click", closeModal));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* ---------------------------- Configuración ---------------------------- */
async function loadConfig() {
  try { configCache = await apiGet("/api/config"); fillConfigForm(configCache); }
  catch (e) { toast(e.message, "err"); }
}
function fillConfigForm(c) {
  const f = $("#configForm");
  f.curfew_start.value = c.curfew_start;
  f.curfew_end.value = c.curfew_end;
  f.conf_threshold.value = Math.round(c.conf_threshold * 100);
  $("#confOut").value = Math.round(c.conf_threshold * 100) + "%";
  f.model.value = c.model;
  f.confirm_frames.value = c.confirm_frames;
  f.cooldown_seconds.value = c.cooldown_seconds;
  f.imgsz.value = c.imgsz;
  f.device.value = c.device || "";
  f.video_source.value = c.video_source || "0";
  f.alert_message.value = c.alert_message || "";
  f.alert_mode.value = c.alert_mode || "persona";
  f.capture_window_s.value = ((c.capture_window_ms ?? 2300) / 1000).toFixed(1);
  syncCameraMode(c.video_source || "0");
  f.night_enhance.checked = c.night_enhance;
  f.send_crop.checked = c.send_crop;
  if (c.updated_at) $("#configUpdated").textContent = "Última edición: " + fmtDateTime(c.updated_at);
}
function syncCameraMode(v) {
  const cm = $("#cameraMode");
  v = (v || "0").trim();
  cm.value = /^\d+$/.test(v) ? (["0", "1", "2"].includes(v) ? v : "0") : "ip";
}
$("#cameraMode").addEventListener("change", (e) => {
  const f = $("#configForm");
  if (e.target.value === "ip") {
    if (/^\d+$/.test(f.video_source.value.trim()) || !f.video_source.value.trim())
      f.video_source.value = "rtsp://usuario:clave@192.168.1.64:554/Streaming/Channels/101";
    f.video_source.focus();
  } else {
    f.video_source.value = e.target.value;
  }
});
$("#camTestBtn").addEventListener("click", async () => {
  const btn = $("#camTestBtn"); const msg = $("#camTestMsg"); const thumb = $("#camTestThumb");
  const source = $("#configForm").video_source.value.trim();
  btn.disabled = true; btn.textContent = "Probando…"; msg.textContent = "Conectando…"; thumb.innerHTML = "";
  try {
    const r = await api("POST", "/api/camera/test", { source });
    msg.innerHTML = `<span class="pill ${r.ok ? "ok" : "bad"}">${r.ok ? "✓" : "✕"} ${r.message}</span>`;
    if (r.ok && r.thumb) thumb.innerHTML = `<img src="${r.thumb}" alt="prueba de cámara" class="cam-test-img">`;
  } catch (e) {
    msg.innerHTML = `<span class="pill bad">✕ ${e.message}</span>`;
  } finally { btn.disabled = false; btn.textContent = "Probar cámara"; }
});
$("#configForm").conf_threshold.addEventListener("input", (e) => { $("#confOut").value = e.target.value + "%"; });
$$("#configForm .chip").forEach((ch) => ch.addEventListener("click", () => {
  const f = $("#configForm");
  f.curfew_start.value = ch.dataset.start;
  f.curfew_end.value = ch.dataset.end;
}));

$("#configForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    enabled: configCache ? configCache.enabled : true,
    curfew_start: f.curfew_start.value,
    curfew_end: f.curfew_end.value,
    conf_threshold: parseInt(f.conf_threshold.value, 10) / 100,
    model: f.model.value,
    confirm_frames: parseInt(f.confirm_frames.value, 10),
    cooldown_seconds: parseInt(f.cooldown_seconds.value, 10),
    imgsz: parseInt(f.imgsz.value, 10),
    device: f.device.value.trim(),
    video_source: f.video_source.value.trim() || "0",
    alert_message: f.alert_message.value,
    alert_mode: f.alert_mode.value,
    capture_window_ms: Math.round((parseFloat(f.capture_window_s.value) || 0) * 1000),
    night_enhance: f.night_enhance.checked,
    send_crop: f.send_crop.checked,
  };
  try { configCache = await api("PUT", "/api/config", body); fillConfigForm(configCache); toast("Configuración guardada."); loadStatus(); }
  catch (e2) { toast(e2.message, "err"); }
});

/* ---------------------------- Master switch ---------------------------- */
$("#masterToggle").addEventListener("change", async (e) => {
  if (!configCache) configCache = await apiGet("/api/config");
  const body = { ...configCache, enabled: e.target.checked };
  delete body.updated_at;
  try { configCache = await api("PUT", "/api/config", body); toast(e.target.checked ? "Sistema activado." : "Sistema en pausa."); loadStatus(); }
  catch (err) { toast(err.message, "err"); }
});

/* ---------------------------- Números ---------------------------- */
async function loadRecipients() {
  let rows;
  try { rows = await apiGet("/api/recipients"); } catch (e) { return toast(e.message, "err"); }
  const activos = rows.filter((r) => r.active).length;
  $("#recipCount").textContent = `${activos}/4 activos`;
  const list = $("#recipList");
  if (!rows.length) { list.innerHTML = `<div class="muted">No hay números. Agrega al menos uno.</div>`; return; }
  list.innerHTML = rows.map((r) => {
    const v = verifyMap[r.id];
    const badge = v
      ? `<span class="vbadge ${v.exists ? "ok" : "bad"}">${v.exists ? "✓ WhatsApp" : "✕ sin WhatsApp"}</span>`
      : "";
    return `<div class="recip">
      <div class="recip-main">
        <div class="r-phone">+${r.phone}${badge}</div>
        <div class="r-label">${r.label || "—"}</div>
      </div>
      <div class="spacer"></div>
      <label class="switch" title="activo">
        <input type="checkbox" data-toggle="${r.id}" ${r.active ? "checked" : ""}>
        <span class="slider"></span>
      </label>
      <button class="icon-btn" data-del="${r.id}" title="Eliminar" aria-label="Eliminar"><svg class="ico"><use href="#i-trash"/></svg></button>
    </div>`;
  }).join("");

  $$("#recipList [data-toggle]").forEach((c) => c.addEventListener("change", () => toggleRecipient(rows, +c.dataset.toggle, c.checked, c)));
  $$("#recipList [data-del]").forEach((b) => b.addEventListener("click", () => deleteRecipient(+b.dataset.del)));
}
async function toggleRecipient(rows, id, active, cb) {
  const r = rows.find((x) => x.id === id);
  try { await api("PUT", `/api/recipients/${id}`, { phone: r.phone, label: r.label, active }); toast("Actualizado."); loadRecipients(); loadStatus(); }
  catch (e) { toast(e.message, "err"); cb.checked = !active; loadRecipients(); }
}
async function deleteRecipient(id) {
  if (!confirm("¿Eliminar este número?")) return;
  try { await api("DELETE", `/api/recipients/${id}`); toast("Número eliminado."); loadRecipients(); loadStatus(); }
  catch (e) { toast(e.message, "err"); }
}
$("#recipForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("POST", "/api/recipients", { phone: f.phone.value, label: f.label.value, active: true });
    f.reset(); toast("Número agregado."); loadRecipients(); loadStatus();
  } catch (e2) { toast(e2.message, "err"); }
});
$("#verifyBtn").addEventListener("click", async () => {
  const btn = $("#verifyBtn"); btn.disabled = true; btn.textContent = "Verificando…";
  try {
    const { results } = await api("POST", "/api/recipients/verify");
    verifyMap = {};
    results.forEach((r) => { verifyMap[r.id] = { exists: r.exists, corrected: r.corrected }; });
    const ok = results.filter((r) => r.exists).length;
    const corr = results.filter((r) => r.corrected).length;
    await loadRecipients();   // re-render con la insignia al lado de cada número
    toast(`Verificados: ${ok}/${results.length} con WhatsApp${corr ? `, ${corr} corregido(s)` : ""}.`);
  } catch (e) {
    toast(e.message, "err");
  } finally { btn.disabled = false; btn.textContent = "Verificar en WhatsApp"; }
});

/* ---------------------------- WhatsApp (multi-instancia) ---------------------------- */
async function loadWaInstances() {
  const box = $("#waList");
  try {
    const d = await apiGet("/api/whatsapp/instances");
    if (!d.configured) { box.innerHTML = `<span class="muted">Falta EVOLUTION_API_KEY en el .env.</span>`; return; }
    renderWaList(d.instances || []);
  } catch (e) { box.innerHTML = `<span class="muted">${e.message}</span>`; }
}
function renderWaList(list) {
  const box = $("#waList");
  if (!list.length) { box.innerHTML = `<span class="muted">Aún no hay conexiones. Agrega una abajo.</span>`; return; }
  box.innerHTML = list.map((i) => {
    const [txt, kind] = waLabel(i.state);
    const open = i.state === "open";
    return `<div class="wa-item">
      <div class="wa-info">
        <div class="wa-name">${i.name} ${i.is_sender ? '<span class="badge sender">Emisor</span>' : ""}</div>
        <div class="wa-sub">${i.label ? i.label + " · " : ""}<span class="pill ${kind}">${txt}</span></div>
      </div>
      <div class="wa-actions">
        ${open ? "" : `<button class="btn primary sm" data-connect="${i.name}">Conectar / QR</button>`}
        ${i.is_sender ? "" : `<button class="btn ghost sm" data-sender="${i.name}">Hacer emisor</button>`}
        ${open ? `<button class="btn ghost sm" data-logout="${i.name}">Desvincular</button>` : ""}
        <button class="icon-btn" data-del="${i.name}" title="Eliminar" aria-label="Eliminar"><svg class="ico"><use href="#i-trash"/></svg></button>
      </div>
    </div>`;
  }).join("");
  $$("#waList [data-connect]").forEach((b) => b.addEventListener("click", () => connectInstance(b.dataset.connect)));
  $$("#waList [data-sender]").forEach((b) => b.addEventListener("click", () => setSender(b.dataset.sender)));
  $$("#waList [data-logout]").forEach((b) => b.addEventListener("click", () => logoutInstance(b.dataset.logout)));
  $$("#waList [data-del]").forEach((b) => b.addEventListener("click", () => deleteInstance(b.dataset.del)));
}
function showQr(res, name) {
  const box = $("#qrBox");
  box.hidden = false;
  if (res.state === "open") {
    box.innerHTML = `<div class="pill ok" style="font-size:14px">✓ “${name}” ya está conectado</div>`;
    return;
  }
  if (res.qr) {
    box.innerHTML = `<div><div class="muted" style="margin-bottom:8px">Escanea con el teléfono <strong>${name}</strong>:
      WhatsApp ▸ Dispositivos vinculados ▸ Vincular dispositivo</div>
      <img src="${res.qr}" alt="QR WhatsApp">
      ${res.pairing_code ? `<div class="pairing">${res.pairing_code}</div>` : ""}</div>`;
    pollInstance(name);
  } else {
    box.innerHTML = `<p class="muted">${res.message || "No se obtuvo QR."}</p>`;
  }
}
async function connectInstance(name) {
  try { const res = await api("POST", `/api/whatsapp/instances/${name}/connect`); showQr(res, name); }
  catch (e) { toast(e.message, "err"); }
}
function pollInstance(name) {
  clearInterval(qrPoll);
  let tries = 0;
  qrPoll = setInterval(async () => {
    tries++;
    let st;
    try { st = (await apiGet(`/api/whatsapp/instances/${name}/state`)).state; } catch { return; }
    if (st === "open") {
      clearInterval(qrPoll);
      $("#qrBox").innerHTML = `<div class="pill ok" style="font-size:14px">✓ ¡“${name}” conectado!</div>`;
      toast("WhatsApp conectado.");
      loadWaInstances(); loadStatus();
    }
    if (tries > 30) clearInterval(qrPoll);
  }, 3000);
}
async function setSender(name) {
  try { await api("POST", `/api/whatsapp/instances/${name}/sender`); toast(`Emisor: ${name}`); loadWaInstances(); loadStatus(); }
  catch (e) { toast(e.message, "err"); }
}
async function logoutInstance(name) {
  if (!confirm(`¿Desvincular el WhatsApp de “${name}”?`)) return;
  try { await api("POST", `/api/whatsapp/instances/${name}/logout`); toast("Desvinculado."); loadWaInstances(); loadStatus(); }
  catch (e) { toast(e.message, "err"); }
}
async function deleteInstance(name) {
  if (!confirm(`¿Eliminar la conexión “${name}”?`)) return;
  try { await api("DELETE", `/api/whatsapp/instances/${name}`); toast("Conexión eliminada."); $("#qrBox").hidden = true; loadWaInstances(); loadStatus(); }
  catch (e) { toast(e.message, "err"); }
}
$("#waRefresh").addEventListener("click", loadWaInstances);
$("#waAddForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    const res = await api("POST", "/api/whatsapp/instances", { label: f.label.value });
    f.reset(); toast("Conexión creada. Escanea el QR.");
    loadWaInstances();
    showQr(res, res.name || "nueva");
  } catch (e2) { toast(e2.message, "err"); }
});
$("#testBtn").addEventListener("click", async () => {
  const btn = $("#testBtn"); btn.disabled = true; btn.textContent = "Enviando…";
  try {
    const { result } = await api("POST", "/api/whatsapp/test");
    $("#testResult").innerHTML = Object.entries(result).map(([num, st]) =>
      `<div class="test-row">
         <span class="pill ${st === "ok" ? "ok" : "bad"}">${st === "ok" ? "✓" : "✕"} +${num}</span>
         ${st === "ok" ? "" : `<span class="muted">${st}</span>`}
       </div>`).join("");
    toast("Prueba enviada.");
  } catch (e) { toast(e.message, "err"); $("#testResult").innerHTML = `<span class="muted">${e.message}</span>`; }
  finally { btn.disabled = false; btn.textContent = "Enviar prueba"; }
});

/* ---------------------------- Cámara en vivo ---------------------------- */
function startCamera() {
  const img = $("#camFeed");
  img.src = "/api/camera/stream?t=" + Date.now();   // abre el stream MJPEG
  pollCam();
  clearInterval(camTimer);
  camTimer = setInterval(pollCam, 2000);
}
function stopCamera() {
  clearInterval(camTimer); camTimer = null;
  const img = $("#camFeed");
  if (img) img.src = "";   // cierra la conexión del stream
}
async function pollCam() {
  let s;
  try { s = await apiGet("/api/camera/status"); } catch { return; }
  const badge = $("#camStatus");
  const overlay = $("#camOverlay");
  const feed = $("#camFeed");
  if (s.online) {
    badge.textContent = "● EN VIVO";
    badge.className = "badge cam-live";
    overlay.style.display = "none";
    feed.style.display = "block";
  } else {
    badge.textContent = "sin señal";
    badge.className = "badge";
    overlay.style.display = "grid";
    feed.style.display = "none";
  }
}

/* ---------------------------- Refresh global ---------------------------- */
function refreshAll() { loadStatus(); loadRecent(); }
$("#refreshBtn").addEventListener("click", () => { refreshAll(); toast("Actualizado."); });

/* ---------------------------- Init ---------------------------- */
(async function init() {
  await loadConfig();
  refreshAll();
  setInterval(loadStatus, 5000);
  setInterval(loadRecent, 8000);
})();

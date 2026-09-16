// Panel de casa. Solo lectura: las acciones van por el chat, que pasa por la
// capa de seguridad. Los nombres que llegan del backend se insertan con
// textContent, nunca con innerHTML: vienen de aparatos y de Home Assistant, y
// son datos, no marcado. Los iconos se construyen con createElementNS a partir
// de trazos fijos de este fichero, por la misma razon.
"use strict";

const CLAVE = "casa-ai-token";
const CLAVE_VISTA = "casa-ai-vista";
const REFRESCO_MS = 10000;

const $ = (id) => document.getElementById(id);
let temporizador = null;
let ultimosDatos = null;

// --- Acceso -----------------------------------------------------------------

function token() {
  try { return sessionStorage.getItem(CLAVE) || ""; } catch { return ""; }
}

function guardarToken(valor) {
  try { sessionStorage.setItem(CLAVE, valor); } catch { /* modo privado */ }
}

function olvidarToken() {
  try { sessionStorage.removeItem(CLAVE); } catch { /* nada */ }
  if (temporizador) { clearInterval(temporizador); temporizador = null; }
  $("contenido").classList.add("oculto");
  $("pestanas").classList.add("oculto");
  $("salir").classList.add("oculto");
  $("acceso").classList.remove("oculto");
}

// Rutas relativas a proposito: por el ingress de Home Assistant la pagina
// vive bajo /api/hassio_ingress/<token>/, y una ruta absoluta se saldria.
async function pedir(ruta, opciones = {}) {
  // Sin token no se manda cabecera: por el ingress de Home Assistant el
  // backend ya sabe quien es por el login de HA, y el token no hace falta.
  const cabeceras = { ...(opciones.headers || {}) };
  if (token()) cabeceras.Authorization = `Bearer ${token()}`;
  const r = await fetch(ruta, { ...opciones, headers: cabeceras });
  if (r.status === 401) { olvidarToken(); throw new Error("token invalido"); }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r;
}

// --- DOM --------------------------------------------------------------------

function el(tag, clase, texto) {
  const nodo = document.createElement(tag);
  if (clase) nodo.className = clase;
  if (texto !== undefined) nodo.textContent = texto;
  return nodo;
}

function visible(id, hay) {
  $(id).classList.toggle("oculto", !hay);
  return hay;
}

const hayDatos = (x) => Boolean(x) && !x.no_disponible;

// --- Iconos -----------------------------------------------------------------
// Trazos de 24x24, a mano y fijos. Se dibujan con createElementNS: ningun
// texto del backend pasa por aqui.

const ICONOS = {
  sol: ["M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4", "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z"],
  casa: ["M3 11 12 4l9 7", "M5 10v10h14V10", "M10 20v-6h4v6"],
  bateria: ["M3 8h14v8H3z", "M17 10h3v4h-3", "M6 11v2M9 11v2M12 11v2"],
  red: ["M12 3v18", "M7 21 12 12l5 9", "M8.5 8.5h7", "M9.5 5.5h5"],
  luz: ["M9 18h6", "M10 21h4", "M12 3a6 6 0 0 0-3.6 10.8c.7.5 1.1 1.3 1.1 2.2h5c0-.9.4-1.7 1.1-2.2A6 6 0 0 0 12 3z"],
  candado: ["M5 11h14v10H5z", "M8 11V7a4 4 0 0 1 8 0v4", "M12 15v2"],
  persiana: ["M4 3h16v18H4z", "M4 8h16M4 13h16M8 13v8"],
  clima: ["M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z", "M12 10v6"],
  musica: ["M9 18V6l11-2v12", "M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z", "M20 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z"],
  coche: ["M3 13l2-5h14l2 5v6H3z", "M3 13h18", "M7 18.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM17 18.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z"],
  agua: ["M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"],
  spa: ["M3 10c2-2 4-2 6 0s4 2 6 0 4-2 6 0", "M3 16c2-2 4-2 6 0s4 2 6 0 4-2 6 0"],
  riego: ["M12 21v-8", "M7 13h10", "M12 13 7 7M12 13l5-6M12 13V4"],
  cine: ["M3 5h18v14H3z", "M7 5v14M17 5v14", "M3 9h4M3 15h4M17 9h4M17 15h4"],
  lavadora: ["M4 3h16v18H4z", "M12 18a5 5 0 1 0 0-10 5 5 0 0 0 0 10z", "M8 6h.01M11 6h3"],
  timbre: ["M7 3h10v18H7z", "M12 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4z", "M10 14h4M10 17h4"],
  personas: ["M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M3 20a6 6 0 0 1 12 0", "M17 11.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z", "M21 20a4.5 4.5 0 0 0-6-4.2"],
  camara: ["M3 8h4l2-3h6l2 3h4v11H3z", "M12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"],
  wifi: ["M2 9a14 14 0 0 1 20 0", "M5.5 12.5a10 10 0 0 1 13 0", "M9 16a5 5 0 0 1 6 0", "M12 19.5h.01"],
  aparatos: ["M9 2v5M15 2v5", "M6 7h12v4a6 6 0 0 1-12 0z", "M12 17v5"],
  jarvis: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  ventana: ["M4 4h16v16H4z", "M12 4v16M4 12h16"],
  escudo: ["M12 3 4 6v6c0 5 3.5 8.5 8 9 4.5-.5 8-4 8-9V6l-8-3z", "M9 12l2 2 4-4"],
  alerta: ["M12 3 2 20h20L12 3z", "M12 10v4M12 17h.01"],
  bien: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M8.5 12.5l2.5 2.5 4.5-5"],
};

function icono(nombre) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  for (const d of ICONOS[nombre] || ICONOS.aparatos) {
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d);
    svg.appendChild(p);
  }
  return svg;
}

// --- Formato ----------------------------------------------------------------

const vatios = (w) => {
  const n = Number(w) || 0;
  return Math.abs(n) >= 1000
    ? `${(n / 1000).toLocaleString("es-ES", { maximumFractionDigits: 1 })} kW`
    : `${Math.round(n)} W`;
};

const grados = (t) => `${Number(t).toLocaleString("es-ES", { maximumFractionDigits: 1 })}°`;

function indicador(etiqueta, valor, unidad, pie, clase) {
  const d = el("div", clase ? `indicador ${clase}` : "indicador");
  const v = el("div", "valor", valor);
  if (unidad) v.appendChild(el("span", "unidad", ` ${unidad}`));
  d.append(el("div", "etiqueta", etiqueta), v);
  if (pie) d.appendChild(el("div", "pie", pie));
  return d;
}

// Una fila de lista: algo a la izquierda (punto, avatar, ondas), nombre que
// crece y, a la derecha, un texto o una pastilla de estado.
function fila(color, texto, secundario, opciones = {}) {
  const li = el("li", "fila");
  if (opciones.izquierda) {
    li.appendChild(opciones.izquierda);
  } else if (color) {
    const punto = el("span", "punto");
    punto.style.background = color;
    li.appendChild(punto);
  }
  li.appendChild(el("span", "crece", texto));
  if (opciones.pastilla) {
    li.appendChild(el("span", `pastilla ${opciones.pastilla}`, secundario));
  } else if (opciones.derecha) {
    li.appendChild(opciones.derecha);
  } else if (secundario !== undefined) {
    li.appendChild(el("span", "sec", secundario));
  }
  return li;
}

function lista(elementos, aFila) {
  const ul = el("ul");
  for (const x of elementos) ul.appendChild(aFila(x));
  return ul;
}

// --- Pista flotante ---------------------------------------------------------

const pista = $("pista-flotante");

function mostrarPista(evento, nombre, valor, color) {
  pista.textContent = "";
  const n = el("div", "n");
  const trazo = el("s");
  trazo.style.background = color;
  n.append(trazo, document.createTextNode(nombre));
  pista.append(el("div", "v", valor), n);
  pista.style.opacity = "1";
  const r = pista.getBoundingClientRect();
  const x = Math.min(evento.clientX + 12, window.innerWidth - r.width - 8);
  pista.style.left = `${Math.max(8, x)}px`;
  pista.style.top = `${Math.max(8, evento.clientY - r.height - 12)}px`;
}

const ocultarPista = () => { pista.style.opacity = "0"; };

// --- Energia ----------------------------------------------------------------

function pintarIndicadores(energia) {
  const cont = $("indicadores");
  cont.textContent = "";
  if (!hayDatos(energia)) {
    cont.appendChild(indicador("Energía", "—", "", energia?.no_disponible || "sin configurar"));
    $("energia-origen").textContent = "";
    pintarFlujo(null);
    return;
  }
  const r = energia.resumen;
  $("energia-origen").textContent = r.origen_de_los_datos ? `· ${r.origen_de_los_datos}` : "";
  cont.append(
    indicador("Produciendo", vatios(r.solar_w), "", "sol", "sol"),
    indicador("Consumiendo", vatios(r.consumo_casa_w), "", "la casa"),
    indicador(
      r.red_estado === "exportando" ? "Vertiendo" : "Tomando",
      vatios(Math.abs(r.red_w)), "",
      r.red_estado === "exportando" ? "a la red" : "de la red", "red",
    ),
    r.bateria_soc_pct === null || r.bateria_soc_pct === undefined
      ? indicador("Batería", "—", "", "sin lectura", "bateria")
      : indicador("Batería", String(Math.round(r.bateria_soc_pct)), "%", r.bateria_estado, "bateria"),
  );
  pintarFlujo(r);
}

// El diagrama: sol, bateria y red alrededor de la casa. Cada linea se anima
// en el sentido en que va la energia y se apaga cuando no pasa nada por ella.
// Bateria positiva = cargando (de la casa hacia ella); red positiva =
// vertiendo (de la casa hacia la red). Es el mismo signo que en el resumen.
function pintarFlujo(r) {
  const linea = (id, w, haciaFuera) => {
    const n = $(id);
    const activa = Boolean(r) && Math.abs(Number(w) || 0) >= 50;
    n.classList.toggle("activa", activa);
    n.classList.toggle("atras", activa && haciaFuera);
  };
  if (!r) {
    for (const id of ["cifra-sol", "cifra-casa", "cifra-bateria", "cifra-red"]) $(id).textContent = "—";
    linea("linea-sol", 0, false); linea("linea-bateria", 0, false); linea("linea-red", 0, false);
    return;
  }
  $("cifra-sol").textContent = vatios(r.solar_w);
  $("cifra-casa").textContent = vatios(r.consumo_casa_w);
  $("cifra-bateria").textContent = r.bateria_w ? vatios(Math.abs(r.bateria_w)) : "en reposo";
  $("cifra-red").textContent = r.red_w ? vatios(Math.abs(r.red_w)) : "0 W";
  linea("linea-sol", r.solar_w, false);
  linea("linea-bateria", r.bateria_w, Number(r.bateria_w) < 0);
  linea("linea-red", r.red_w, r.red_estado !== "exportando");
}

function pintarBateria(energia) {
  if (!visible("tarjeta-bateria", hayDatos(energia))) return;

  const r = energia.resumen;
  const estado = $("estado-bateria");
  estado.textContent = "";
  if (r.bateria_soc_pct === null || r.bateria_soc_pct === undefined) {
    $("relleno-bateria").style.width = "0%";
    $("soc-bateria").textContent = "—";
    estado.textContent = "Sin lectura de la carga de la batería.";
    return;
  }
  const soc = Math.max(0, Math.min(100, Number(r.bateria_soc_pct)));
  $("relleno-bateria").style.width = `${soc}%`;
  const socNodo = $("soc-bateria");
  socNodo.textContent = String(Math.round(soc));
  socNodo.appendChild(el("small", null, "%"));

  // El color nunca va solo: le acompana siempre un punto y un texto.
  let color = "var(--cian)";
  let texto = r.bateria_estado || "";
  if (soc < 10) { color = "var(--critico)"; texto += " · muy baja"; }
  else if (soc < 20) { color = "var(--aviso)"; texto += " · baja"; }
  $("relleno-bateria").style.background = color;

  const punto = el("span", "punto");
  punto.style.background = color;
  estado.append(punto, document.createTextNode(texto));
  if (r.bateria_w) estado.append(document.createTextNode(` a ${vatios(Math.abs(r.bateria_w))}`));
  if (r.bateria_salud_pct !== undefined && r.bateria_salud_pct !== null) {
    estado.append(document.createTextNode(` · salud ${Math.round(r.bateria_salud_pct)} %`));
  }
}

const SERIES = [
  { clave: "solar_w", nombre: "Sol", color: "var(--oro)" },
  { clave: "bateria_w", nombre: "Batería", color: "var(--cian)" },
  { clave: "red_w", nombre: "Red", color: "var(--malva)" },
];

function pintarMezcla(energia) {
  if (!visible("tarjeta-mezcla", hayDatos(energia))) return;

  const m = energia.mezcla;
  const total = SERIES.reduce((suma, x) => suma + (m[x.clave] || 0), 0);
  const partes = SERIES.map((x) => {
    const w = m[x.clave] || 0;
    return { ...x, w, pct: total > 0 ? (w / total) * 100 : 0 };
  });

  const barra = $("apilada");
  const leyenda = $("leyenda");
  barra.textContent = "";
  leyenda.textContent = "";
  pintarTabla(partes);

  if (total <= 0) {
    barra.classList.add("oculto");
    $("nota-mezcla").textContent = "La casa no está consumiendo nada ahora mismo.";
    $("ver-tabla").classList.add("oculto");
    return;
  }
  $("ver-tabla").classList.remove("oculto");

  // Con una sola fuente, una barra apilada seria un grafico de una sola barra:
  // el numero ya es el grafico.
  const presentes = partes.filter((x) => x.pct > 2);
  if (presentes.length === 1) {
    barra.classList.add("oculto");
    leyenda.appendChild(indicador(`Todo de ${presentes[0].nombre.toLowerCase()}`, vatios(total)));
    $("nota-mezcla").textContent = "Derivado del balance del inversor, no de un contador por rama.";
    return;
  }
  barra.classList.remove("oculto");
  for (const serie of partes) {
    if (serie.w <= 0) continue;
    barra.appendChild(segmento(serie));
    leyenda.appendChild(clave(serie));
  }
  $("nota-mezcla").textContent =
    `Total ${vatios(total)}. Derivado del balance del inversor, no de un contador por rama.`;
}

function segmento(serie) {
  const seg = el("div", "segmento");
  seg.style.background = serie.color;
  seg.style.width = `${serie.pct}%`;
  seg.tabIndex = 0;
  seg.setAttribute("role", "img");
  seg.setAttribute("aria-label", `${serie.nombre}: ${vatios(serie.w)}`);
  if (serie.pct > 14) seg.textContent = vatios(serie.w);
  const mostrar = (ev) => mostrarPista(ev, serie.nombre, vatios(serie.w), serie.color);
  seg.addEventListener("pointermove", mostrar);
  seg.addEventListener("pointerleave", ocultarPista);
  seg.addEventListener("focus", () => {
    const r = seg.getBoundingClientRect();
    mostrar({ clientX: r.left + r.width / 2, clientY: r.top });
  });
  seg.addEventListener("blur", ocultarPista);
  return seg;
}

function clave(serie) {
  const nodo = el("span", "clave");
  const muestra = el("i");
  muestra.style.background = serie.color;
  nodo.append(muestra, document.createTextNode(`${serie.nombre} `), el("b", null, vatios(serie.w)));
  return nodo;
}

function pintarTabla(partes) {
  const tabla = el("table");
  const cabecera = el("tr");
  for (const texto of ["Fuente", "Potencia", "Parte"]) {
    cabecera.appendChild(el("th", texto === "Fuente" ? null : "num", texto));
  }
  tabla.appendChild(cabecera);
  for (const serie of partes) {
    const tr = el("tr");
    tr.append(el("td", null, serie.nombre), el("td", "num", vatios(serie.w)),
              el("td", "num", `${Math.round(serie.pct)} %`));
    tabla.appendChild(tr);
  }
  $("tabla-mezcla").textContent = "";
  $("tabla-mezcla").appendChild(tabla);
}

function pintarExcedente(datos) {
  if (!visible("tarjeta-excedente", hayDatos(datos))) return;

  const cont = $("excedente");
  cont.textContent = "";
  const disponible = datos.excedente.disponible_para_gastar_w;
  cont.appendChild(indicador("Disponible para gastar", vatios(disponible), "", "", "sol"));

  const rec = datos.recomendacion || {};
  const ul = el("ul");
  for (const [aparatos, pastilla, prefijo] of [
    [rec.encender, "bien", "Cabe"],
    [rec.apagar, "aviso", "Sobra encendido"],
  ]) {
    for (const x of aparatos || []) {
      const li = fila(null, x.nombre, prefijo, { pastilla });
      li.insertBefore(el("span", "sec", vatios(x.consumo_w)), li.lastChild);
      ul.appendChild(li);
    }
  }
  if (!ul.children.length) {
    ul.appendChild(el("li", "vacio", disponible > 0
      ? "Hay excedente pero nada de lo declarado cabe en él."
      : "No hay excedente ahora mismo."));
  }
  ul.style.marginTop = "12px";
  cont.appendChild(ul);
}

// --- Inicio: la casa en losetas ---------------------------------------------
// Cada loseta resume un sistema en una linea y, al tocarla, abre la hoja con el
// detalle. La lista de losetas se deriva de los datos: lo que no esta
// configurado no aparece, y no hay que tocar nada aqui para anadir un sistema
// declarado en `panel:`.

const ACTIVO = ["encendida", "encendido", "cargando", "sonando", "en marcha", "lavando",
                "centrifug", "calentando", "abierta"];
const SIN_DATO = ["sin dato", "sin conexion", "sin conexión"];

function iconoDeSistema(titulo) {
  const t = titulo.toLowerCase();
  const tabla = [
    ["tiempo", "sol"], ["meteo", "sol"], ["coche", "coche"], ["wallbox", "coche"], ["agua", "agua"], ["ecowater", "agua"],
    ["spa", "spa"], ["jacuzzi", "spa"], ["piscina", "spa"], ["riego", "riego"],
    ["cine", "cine"], ["tv", "cine"], ["lavad", "lavadora"], ["cocina", "lavadora"],
    ["videoport", "timbre"], ["portero", "timbre"], ["puerta", "candado"], ["clima", "clima"],
  ];
  for (const [clave, ic] of tabla) if (t.includes(clave)) return ic;
  return "aparatos";
}

function losetasDe(datos) {
  const salida = [];
  const casa = hayDatos(datos.casa) ? datos.casa : null;

  if (hayDatos(datos.energia)) {
    const r = datos.energia.resumen;
    const bat = r.bateria_soc_pct === null || r.bateria_soc_pct === undefined
      ? "" : ` · batería ${Math.round(r.bateria_soc_pct)} %`;
    salida.push({ id: "energia", icono: "sol", titulo: "Energía", tono: Number(r.solar_w) > 0 ? "oro" : "neutro",
      estado: `${vatios(r.solar_w)} de sol${bat}`, ir: "energia" });
  }

  if (casa?.presencia) {
    const dentro = casa.presencia.filter((p) => p.en_casa).map((p) => p.nombre);
    salida.push({ id: "presencia", icono: "personas", titulo: "En casa", tono: dentro.length ? "bien" : "neutro",
      estado: dentro.length ? dentro.join(", ") : "Nadie en casa",
      detalle: () => lista(casa.presencia, (p) => fila(null, p.nombre, p.en_casa ? "en casa" : "fuera", {
        izquierda: el("span", p.en_casa ? "avatar en-casa" : "avatar", (p.nombre || "?").slice(0, 1).toUpperCase()),
        pastilla: p.en_casa ? "bien" : "neutra",
      })) });
  }

  if (casa?.alarma?.length) {
    const a = casa.alarma[0];
    salida.push({ id: "alarma", icono: "escudo", titulo: "Seguridad",
      tono: a.saltando ? "critico" : a.armada ? "bien" : "neutro",
      estado: casa.alarma.length === 1 ? a.estado : casa.alarma.map((x) => `${x.nombre}: ${x.estado}`).join(" · "),
      detalle: () => lista(casa.alarma, (x) => fila(null, x.nombre, x.estado,
        { pastilla: x.saltando ? "critico" : x.armada ? "bien" : "neutra" })) });
  }

  if (casa?.accesos?.length) {
    const abiertos = casa.accesos.filter((a) => a.abierto);
    salida.push({ id: "accesos", icono: "candado", titulo: "Accesos", tono: abiertos.length ? "aviso" : "bien",
      estado: abiertos.length ? `${abiertos.map((a) => a.nombre).join(", ")}: ${abiertos.length === 1 && abiertos[0].ventana ? "abierta" : "abierto"}` : "Todo cerrado",
      detalle: () => lista(casa.accesos, (a) => fila(null, a.nombre, a.estado, { pastilla: a.abierto ? "aviso" : "bien" })) });
  }

  if (casa?.luces?.total) {
    const n = casa.luces.encendidas.length;
    salida.push({ id: "luces", icono: "luz", titulo: "Luces", tono: n ? "oro" : "neutro",
      estado: n ? `${n} encendida${n === 1 ? "" : "s"}: ${casa.luces.encendidas.join(", ")}` : "Todas apagadas",
      detalle: () => {
        if (!n) return el("p", "vacio", `Las ${casa.luces.total} luces están apagadas.`);
        const chips = el("div", "chips");
        for (const nombre of casa.luces.encendidas) chips.appendChild(el("span", "chip", nombre));
        return chips;
      } });
  }

  if (casa?.persianas?.length) {
    const abiertas = casa.persianas.filter((p) =>
      p.posicion !== null && p.posicion !== undefined ? p.posicion > 0 : p.estado !== "cerrada");
    salida.push({ id: "persianas", icono: "persiana", titulo: "Persianas", tono: abiertas.length ? "activo" : "neutro",
      estado: abiertas.length ? `${abiertas.length} de ${casa.persianas.length} abiertas` : "Todas cerradas",
      detalle: () => lista(casa.persianas, (p) => {
        if (p.posicion === null || p.posicion === undefined) {
          return fila(null, p.nombre, p.estado, { pastilla: p.estado === "cerrada" ? "neutra" : "cian" });
        }
        const barra = el("span", "barrita");
        const dentro = el("i");
        dentro.style.width = `${Math.max(0, Math.min(100, Number(p.posicion)))}%`;
        barra.appendChild(dentro);
        const li = fila(null, p.nombre, undefined, { derecha: barra });
        li.appendChild(el("span", "sec", `${Math.round(p.posicion)} %`));
        return li;
      }) });
  }

  if (casa?.clima?.length) {
    const apagado = (c) => ["off", "apagada", "en reposo"].includes(c.modo);
    const activos = casa.clima.filter((c) => !apagado(c));
    const primero = activos[0] || casa.clima[0];
    const temp = primero.actual !== null && primero.actual !== undefined ? ` · ${grados(primero.actual)} en ${primero.nombre.toLowerCase()}` : "";
    salida.push({ id: "clima", icono: "clima", titulo: "Clima", tono: activos.length ? "activo" : "neutro",
      estado: activos.length ? `${activos.length} en marcha${temp}` : `Apagado${temp}`,
      detalle: () => lista(casa.clima, (c) => {
        const g = el("span", "grados", c.actual !== null && c.actual !== undefined ? grados(c.actual) : "—");
        if (c.objetivo !== null && c.objetivo !== undefined) g.appendChild(el("small", null, ` → ${c.objetivo}°`));
        const li = fila(null, c.nombre, c.modo, { pastilla: apagado(c) ? "neutra" : "cian" });
        li.insertBefore(g, li.lastChild);
        return li;
      }) });
  }

  if (Array.isArray(datos.dispositivos) && datos.dispositivos.length) {
    const encendidos = datos.dispositivos.filter((d) => d.encendido === true);
    salida.push({ id: "aparatos", icono: "aparatos", titulo: "Aparatos", tono: encendidos.length ? "activo" : "neutro",
      estado: encendidos.length ? `${encendidos.length} encendido${encendidos.length === 1 ? "" : "s"}: ${encendidos.map((d) => d.nombre).join(", ")}` : "Todo apagado",
      detalle: () => lista(datos.dispositivos, (d) => d.encendido === true
        ? fila(null, d.nombre, "encendido", { pastilla: "bien" })
        : d.encendido === false ? fila(null, d.nombre, "apagado", { pastilla: "neutra" })
        : fila(null, d.nombre, d.categoria || "", { pastilla: "neutra" })) });
  }

  for (const s of casa?.sistemas || []) {
    const activo = s.lineas.some((l) => ACTIVO.some((a) => String(l.valor).toLowerCase().includes(a)));
    const util = s.lineas.filter((l) => !SIN_DATO.includes(String(l.valor).toLowerCase()));
    const resumen = util.length ? util.slice(0, 2).map((l) => `${l.nombre}: ${l.valor}`).join(" · ") : "Sin datos todavía";
    salida.push({ id: `sistema-${s.titulo}`, icono: iconoDeSistema(s.titulo), titulo: s.titulo,
      tono: activo ? "activo" : "neutro", estado: resumen,
      detalle: () => lista(s.lineas, (l) => fila(null, l.nombre, l.valor,
        { pastilla: SIN_DATO.includes(String(l.valor).toLowerCase()) ? "neutra" : undefined })) });
  }

  if (Array.isArray(datos.musica) && datos.musica.length) {
    const sonando = datos.musica.filter((m) => !m.error && (m.estado === "play" || m.estado === "stream"));
    const que = (m) => [m.titulo, m.artista].filter(Boolean).join(" — ") || m.servicio || "sonando";
    salida.push({ id: "musica", icono: "musica", titulo: "Música", tono: sonando.length ? "bien" : "neutro",
      estado: sonando.length ? `${sonando[0].reproductor}: ${que(sonando[0])}` : "En silencio",
      detalle: () => lista(datos.musica, (m) => {
        if (m.error) return fila(null, m.reproductor, "no responde", { pastilla: "neutra" });
        if (m.estado === "play" || m.estado === "stream") {
          const ondas = el("span", "sonando");
          ondas.append(el("i"), el("i"), el("i"));
          return fila(null, m.reproductor, que(m), { izquierda: ondas });
        }
        return fila(null, m.reproductor, "en silencio", { pastilla: "neutra" });
      }) });
  }

  if (datos.red && typeof datos.red === "object" && !datos.red.no_disponible && Object.keys(datos.red).length) {
    const red = datos.red;
    const nombres = new Map([["wan", "Internet"], ["wlan", "WiFi"], ["lan", "Cable"], ["vpn", "VPN"]]);
    const partes = [...nombres].filter(([k]) => red[k]);
    const mal = partes.filter(([k]) => red[k].estado !== "ok");
    const wifi = red.wlan?.usuarios;
    salida.push({ id: "red", icono: "wifi", titulo: "Red", tono: mal.length ? "critico" : "bien",
      estado: mal.length ? `${mal.map(([, n]) => n).join(", ")} con problemas` : `Todo bien${wifi ? ` · ${wifi} en la WiFi` : ""}`,
      detalle: () => lista(partes, ([k, etiqueta]) => {
        const s = red[k];
        const bien = s.estado === "ok";
        let detalle = "";
        if (k === "wan" && s.latencia_ms !== undefined && s.latencia_ms !== null) detalle = `${s.latencia_ms} ms`;
        else if (s.usuarios !== undefined && s.usuarios !== null) detalle = `${s.usuarios} conectados`;
        if (s.caidos) detalle += ` · ${s.caidos} caídos`;
        const li = fila(null, etiqueta, bien ? "bien" : (s.estado || "?"), { pastilla: bien ? "bien" : "critico" });
        if (detalle) li.insertBefore(el("span", "sec", detalle), li.lastChild);
        return li;
      }) });
  }

  if (Array.isArray(datos.camaras) && datos.camaras.length) {
    salida.push({ id: "camaras", icono: "camara", titulo: "Cámaras", tono: "neutro",
      estado: datos.camaras.map((c) => c.nombre).join(", "), ir: "camaras" });
  }
  return salida;
}

function pintarLosetas(datos) {
  const cont = $("losetas");
  cont.textContent = "";
  // La energia y las camaras tienen su sitio (la tira y su pestana).
  const losetas = losetasDe(datos).filter((l) => !l.ir);
  for (const l of losetas) {
    const b = el("button", `ficha ${l.tono}`);
    b.type = "button";
    const ic = el("span", "ic");
    ic.appendChild(icono(l.icono));
    b.append(ic, el("span", "nombre", l.titulo), el("span", "estado", l.estado));
    b.addEventListener("click", () => {
      if (l.ir) irA(l.ir);
      else abrirHoja(l);
    });
    cont.appendChild(b);
  }
  if (!losetas.length) cont.appendChild(el("p", "vacio", "Todavía no hay nada configurado. Empieza por /verificar en Telegram."));

  // La banda de arriba: lo unico que pide atencion de un vistazo.
  const aviso = $("aviso-casa");
  aviso.textContent = "";
  const abiertos = (hayDatos(datos.casa) ? datos.casa.accesos || [] : []).filter((a) => a.abierto);
  const saltando = (hayDatos(datos.casa) ? datos.casa.alarma || [] : []).filter((a) => a.saltando);
  if (saltando.length) {
    aviso.className = "aviso-casa critico";
    aviso.append(icono("alerta"), el("span", null, `Alarma: ${saltando.map((a) => a.nombre).join(", ")}`));
  } else if (abiertos.length) {
    aviso.className = "aviso-casa";
    const texto = abiertos.length === 1
      ? `${abiertos[0].nombre}: ${abiertos[0].ventana ? "abierta" : "abierto"}`
      : `Abierto: ${abiertos.map((a) => a.nombre).join(" · ")}`;
    aviso.append(icono("alerta"), el("span", null, texto));
  } else if (hayDatos(datos.casa) && (datos.casa.accesos || []).length) {
    aviso.className = "aviso-casa tranquilo";
    aviso.append(icono("bien"), el("span", null, "Todo cerrado"));
  } else {
    aviso.classList.add("oculto");
  }
}

// --- El plano de la casa ----------------------------------------------------
// Cada estancia es un rectangulo de la rejilla de `plano:`; se tine de oro
// con luz encendida, lleva borde cian con el clima en marcha y ambar si hay un
// acceso abierto. Dentro, la temperatura y un simbolo por cada cosa que pasa.
// Tocarla abre la hoja con lo que hay en ella.

const NS = "http://www.w3.org/2000/svg";
const CELDA = 40;
const CLAVE_PLANTA = "casa-ai-planta";
let plantaActual = null;

function svg(tag, atributos = {}) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(atributos)) n.setAttribute(k, String(v));
  return n;
}

// Un icono de ICONOS, reducido, para dentro del plano.
function simbolo(nombre, x, y, clase, escala = 0.55) {
  const g = svg("g", { class: `simbolo ${clase}`, transform: `translate(${x} ${y}) scale(${escala})` });
  for (const d of ICONOS[nombre] || []) g.appendChild(svg("path", { d }));
  return g;
}

// Las zonas se escriben sin acentos en el YAML (sirven para casar nombres de
// entidades); en el plano se ensenan con ellos.
const ACENTOS = new Map([["salon", "salón"], ["bano", "baño"], ["jardin", "jardín"], ["sotano", "sótano"],
  ["atico", "ático"], ["habitacion", "habitación"], ["balcon", "balcón"], ["recibidor", "recibidor"]]);
function capitalizar(t) {
  if (!t) return t;
  const bonito = t.split(" ").map((p) => ACENTOS.get(p) || p).join(" ");
  return bonito.charAt(0).toUpperCase() + bonito.slice(1);
}

function pintarPlano(datos) {
  const plano = Array.isArray(datos.plano) ? datos.plano : [];
  const svgPlano = $("plano");
  svgPlano.textContent = "";
  if (!plano.length) { svgPlano.parentElement.classList.add("oculto"); return; }
  svgPlano.parentElement.classList.remove("oculto");

  // Plantas: un selector si hay mas de una; se recuerda la elegida.
  const plantas = [...new Set(plano.map((e) => e.planta || ""))];
  const selector = $("plantas");
  selector.textContent = "";
  if (plantas.length > 1) {
    if (!plantas.includes(plantaActual)) {
      let guardada = null;
      try { guardada = localStorage.getItem(CLAVE_PLANTA); } catch { /* nada */ }
      plantaActual = plantas.includes(guardada) ? guardada : plantas[0];
    }
    for (const p of plantas) {
      const b = el("button", p === plantaActual ? "activa" : "", p ? capitalizar(p.toLowerCase()) : "Casa");
      b.type = "button";
      b.addEventListener("click", () => {
        plantaActual = p;
        try { localStorage.setItem(CLAVE_PLANTA, p); } catch { /* nada */ }
        if (ultimosDatos) pintarPlano(ultimosDatos);
      });
      selector.appendChild(b);
    }
    selector.classList.remove("oculto");
  } else {
    plantaActual = plantas[0];
    selector.classList.add("oculto");
  }
  const habitaciones = new Map(((hayDatos(datos.casa) && datos.casa.habitaciones) || []).map((h) => [h.zona, h]));

  const M = 6;
  const visibles = plano.filter((e) => (e.planta || "") === plantaActual);
  const anchoMax = Math.max(...visibles.map((e) => e.x + e.ancho));
  const altoMax = Math.max(...visibles.map((e) => e.y + e.alto));
  svgPlano.setAttribute("viewBox", `0 0 ${anchoMax * CELDA + M * 2} ${altoMax * CELDA + M * 2}`);

  for (const e of visibles) {
    const h = habitaciones.get(e.zona) || {};
    const x = M + e.x * CELDA, y = M + e.y * CELDA, w = e.ancho * CELDA - 4, alto = e.alto * CELDA - 4;
    const clases = ["estancia"];
    if (e.exterior) clases.push("exterior");
    if (/piscina|spa|jacuzzi/i.test(e.zona)) clases.push("agua");
    if (h.luces_encendidas) clases.push("luz");
    if (h.clima && !["off", "apagada", "en reposo"].includes(h.clima)) clases.push("clima");
    if (h.accesos_abiertos || (h.ventanas_abiertas || []).length) clases.push("alerta");
    const g = svg("g", { class: clases.join(" "), tabindex: "0", role: "button" });
    g.appendChild(svg("rect", { class: "suelo", x, y, width: w, height: alto, rx: 7 }));
    g.appendChild(svg("title")).textContent = capitalizar(e.zona);

    // Nombre, recortado a lo que cabe.
    const nombre = capitalizar(e.zona);
    const hayTemp = h.temperatura !== null && h.temperatura !== undefined;
    const bajo = alto < 44;  // una celda de alto: todo en una linea
    // La temperatura va a la derecha del nombre si caben los dos; si no, debajo.
    const anchoNombre = nombre.length * 6.3;
    const tempAlLado = hayTemp && !bajo && anchoNombre + 40 <= w - 12;
    const tempDebajo = hayTemp && !bajo && !tempAlLado && alto >= 60;
    const cabe = Math.max(3, Math.floor((w - 10 - (tempAlLado ? 38 : 0)) / 6.3));
    const t = svg("text", { class: "nombre", x: x + 6, y: y + 14 });
    t.textContent = nombre.length > cabe ? `${nombre.slice(0, cabe - 1)}…` : nombre;
    g.appendChild(t);
    if (tempAlLado || tempDebajo) {
      const tt = svg("text", { class: "grados", x: tempAlLado ? x + w - 6 : x + 6, y: tempAlLado ? y + 15 : y + 30,
                               "text-anchor": tempAlLado ? "end" : "start" });
      tt.textContent = grados(h.temperatura);
      g.appendChild(tt);
    }

    // Simbolos: abajo a la izquierda, o a la derecha del nombre si el cuarto
    // es de una sola celda de alto. Los que quepan.
    const simbolos = [];
    if (h.luces_encendidas) simbolos.push(["luz", "luz", h.luces_encendidas]);
    if (h.musica) simbolos.push(["musica", "musica"]);
    if (h.persianas_abiertas) simbolos.push(["persiana", "persiana"]);
    if (h.accesos_abiertos) simbolos.push(["alerta", "alerta"]);
    if ((h.ventanas_abiertas || []).length) simbolos.push(["ventana", "alerta", h.ventanas_abiertas.length]);
    if (h.camara) simbolos.push(["camara", "camara"]);
    const ancho = (s) => 18 + (s[2] > 1 ? 8 : 0);
    let sx, sy;
    if (bajo) {
      const total = simbolos.reduce((a, s) => a + ancho(s), 0);
      sx = x + w - 4 - total; sy = y + alto / 2 - 7;
      if (sx < x + 6 + Math.min(anchoNombre, cabe * 6.3)) { simbolos.length = 0; }
    } else {
      sx = x + 6; sy = y + alto - 19;
    }
    for (const s of simbolos) {
      const [ic, clase, cuenta] = s;
      if (sx + 14 > x + w - 4) break;
      g.appendChild(simbolo(ic, sx, sy, clase));
      if (cuenta && cuenta > 1) {
        const c = svg("text", { class: "cuenta", x: sx + 14, y: sy + 5 });
        c.textContent = String(cuenta);
        g.appendChild(c);
      }
      sx += ancho(s);
    }

    const abrir = () => abrirHoja({ titulo: capitalizar(e.zona), icono: e.exterior ? "sol" : "casa", detalle: () => detalleEstancia(e, h) });
    g.addEventListener("click", abrir);
    g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); abrir(); } });
    svgPlano.appendChild(g);
  }
}

// El detalle de una estancia, por secciones como en la app de ONNA:
// iluminacion, persianas, temperatura, suministros y el resto. Es de lectura;
// para tocar algo, el boton lleva al chat con la estancia ya escrita.
const SECCIONES = [
  ["Iluminación", ["light"]],
  ["Persianas y estores", ["cover"]],
  ["Temperatura", ["climate"]],
  ["Ventanas y puertas", ["binary_sensor"]],
  ["Suministros", ["switch", "fan", "input_boolean"]],
  ["Música", ["media_player"]],
  ["Otros", null],
];

const ENCENDIDO = new Set(["encendida", "encendido", "abierta", "on", "calor", "frio", "auto", "sonando"]);

function detalleEstancia(e, h) {
  const cont = el("div");
  const linea = el("ul");
  if (h.temperatura !== null && h.temperatura !== undefined) {
    linea.appendChild(fila(null, "Temperatura", grados(h.temperatura), { pastilla: "cian" }));
  }
  if (h.musica) linea.appendChild(fila(null, "Sonando", h.musica, { pastilla: "bien" }));
  if (h.accesos_abiertos) linea.appendChild(fila(null, "Acceso", "abierto", { pastilla: "aviso" }));
  for (const v of h.ventanas_abiertas || []) linea.appendChild(fila(null, v, "abierta", { pastilla: "aviso" }));
  if (linea.children.length) cont.appendChild(linea);

  const ents = h.entidades || [];
  const usadas = new Set();
  for (const [titulo, dominios] of SECCIONES) {
    const grupo = ents.filter((x, i) => !usadas.has(i) && (dominios === null || dominios.includes(x.dominio)));
    if (!grupo.length) continue;
    grupo.forEach((x) => usadas.add(ents.indexOf(x)));
    const activos = grupo.filter((x) => ENCENDIDO.has(String(x.estado).toLowerCase())).length;
    const cab = el("p", "seccion-titulo", titulo);
    if (dominios && dominios.includes("light")) cab.append(el("span", "cuenta", `  ${activos} de ${grupo.length} encendidas`));
    cont.appendChild(cab);
    cont.appendChild(lista(grupo, (x) => {
      const encendido = ENCENDIDO.has(String(x.estado).toLowerCase());
      const apagado = ["apagada", "apagado", "cerrada", "off", "en reposo"].includes(String(x.estado).toLowerCase());
      return fila(null, x.nombre, x.estado, { pastilla: encendido ? "bien" : apagado ? "neutra" : undefined });
    }));
  }
  if (!ents.length && !linea.children.length) {
    cont.appendChild(el("p", "vacio", "Nada declarado en esta estancia todavía."));
  }

  const pedir = el("button", "boton primario", `Pedir a Jarvis algo en ${capitalizar(e.zona).toLowerCase()}`);
  pedir.type = "button";
  pedir.style.marginTop = "16px";
  pedir.style.width = "100%";
  pedir.addEventListener("click", () => {
    $("hoja").close();
    irA("jarvis");
    $("mensaje").value = `En ${capitalizar(e.zona).toLowerCase()}, `;
    $("mensaje").focus();
  });
  cont.appendChild(pedir);
  return cont;
}

function pintarTiraEnergia(energia) {
  const cont = $("tira-energia");
  cont.textContent = "";
  if (!visible("tira-energia", hayDatos(energia))) return;
  const r = energia.resumen;
  const chip = (clase, ic, valor, etiqueta) => {
    const b = el("button", clase);
    b.type = "button";
    b.append(icono(ic), el("span", "valor", valor), el("span", "etiqueta", etiqueta));
    b.addEventListener("click", () => irA("energia"));
    return b;
  };
  cont.append(
    chip("sol", "sol", vatios(r.solar_w), "sol"),
    chip("casa", "casa", vatios(r.consumo_casa_w), "casa"),
    chip("bat", "bateria", r.bateria_soc_pct === null || r.bateria_soc_pct === undefined ? "—" : `${Math.round(r.bateria_soc_pct)} %`, "batería"),
    chip("red", "red", `${r.red_estado === "exportando" ? "↑" : "↓"} ${vatios(Math.abs(r.red_w))}`, "red"),
  );
}

// --- Hoja de detalle --------------------------------------------------------

function abrirHoja(loseta) {
  $("hoja-titulo").textContent = loseta.titulo;
  const ic = $("hoja-icono");
  ic.textContent = "";
  ic.appendChild(icono(loseta.icono));
  const cuerpo = $("hoja-cuerpo");
  cuerpo.textContent = "";
  cuerpo.appendChild(loseta.detalle());
  const hoja = $("hoja");
  if (typeof hoja.showModal === "function") hoja.showModal(); else hoja.setAttribute("open", "");
}

$("hoja-cerrar").addEventListener("click", () => $("hoja").close());
$("hoja").addEventListener("click", (e) => { if (e.target === $("hoja")) $("hoja").close(); });

// --- Pestanas ---------------------------------------------------------------

function irA(vista) {
  for (const s of document.querySelectorAll("[data-vista]")) s.classList.toggle("activa", s.dataset.vista === vista);
  for (const b of document.querySelectorAll("#pestanas button")) b.classList.toggle("activa", b.dataset.ir === vista);
  try { localStorage.setItem(CLAVE_VISTA, vista); } catch { /* nada */ }
  window.scrollTo({ top: 0 });
  if (vista === "camaras" && ultimosDatos) pintarCamaras(ultimosDatos.camaras);
}

for (const b of document.querySelectorAll("#pestanas button")) {
  b.querySelector(".pastilla-icono").appendChild(icono(b.querySelector(".pastilla-icono").dataset.icono));
  b.addEventListener("click", () => irA(b.dataset.ir));
}

// --- Camaras ----------------------------------------------------------------

async function pintarCamaras(camaras, forzar = false) {
  const hay = Array.isArray(camaras) && camaras.length;
  visible("camaras-vacio", !hay);
  const cont = $("camaras");
  if (!hay) { cont.textContent = ""; return; }
  // Solo se piden capturas con la pestana de camaras a la vista, y una vez
  // por visita (o al pulsar Actualizar): cada captura es una peticion a Protect.
  if (!document.querySelector('[data-vista="camaras"]').classList.contains("activa")) return;
  if (!forzar && cont.children.length === camaras.length) return;

  cont.textContent = "";
  for (const c of camaras) {
    const fig = el("figure");
    const img = el("img");
    img.alt = `Cámara ${c.nombre}`;
    const zonaDistinta = c.zona && c.zona.toLowerCase() !== c.nombre.toLowerCase();
    const pie = el("figcaption", null, zonaDistinta ? `${c.nombre} · ${c.zona}` : c.nombre);
    fig.append(img, pie);
    cont.appendChild(fig);
    try {
      const r = await pedir(`api/panel/camara/${encodeURIComponent(c.nombre)}`);
      img.src = URL.createObjectURL(await r.blob());
    } catch {
      pie.textContent += " — sin imagen";
    }
  }
}

$("refrescar-camaras").addEventListener("click", () => {
  if (ultimosDatos) pintarCamaras(ultimosDatos.camaras, true);
});

// --- Jarvis (chat) ----------------------------------------------------------

function burbuja(clase, texto) {
  const b = el("div", `burbuja ${clase}`, texto);
  $("hilo").appendChild(b);
  b.scrollIntoView({ block: "nearest" });
  return b;
}

async function preguntar(mensaje) {
  mensaje = mensaje.trim();
  if (!mensaje) return;
  $("bienvenida").classList.add("oculto");
  $("sugerencias").classList.add("oculto");
  $("mensaje").value = "";
  burbuja("yo", mensaje);
  const espera = burbuja("jarvis pensando", "Pensando…");
  try {
    const r = await pedir("chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mensaje, hilo: "panel" }),
    });
    espera.textContent = (await r.json()).respuesta;
  } catch (e) {
    espera.textContent = `No he podido responder: ${e.message}`;
  }
  espera.classList.remove("pensando");
  await refrescar();
}

$("enviar").addEventListener("click", () => preguntar($("mensaje").value));
$("mensaje").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); preguntar($("mensaje").value); }
});
for (const b of document.querySelectorAll("#sugerencias button")) {
  b.addEventListener("click", () => preguntar(b.textContent));
}

// --- Saludo y ciclo ---------------------------------------------------------

function saludo(quien) {
  const h = new Date().getHours();
  const base = h < 6 ? "Buenas noches" : h < 14 ? "Buenos días" : h < 21 ? "Buenas tardes" : "Buenas noches";
  return quien ? `${base}, ${quien}` : base;
}

async function refrescar() {
  try {
    const datos = await (await pedir("api/panel")).json();
    ultimosDatos = datos;
    $("momento").textContent = datos.momento;
    $("saludo").textContent = saludo(datos.quien);
    pintarPlano(datos);
    pintarTiraEnergia(datos.energia);
    pintarLosetas(datos);
    pintarIndicadores(datos.energia);
    pintarBateria(datos.energia);
    pintarMezcla(datos.energia);
    pintarExcedente(datos.excedente);
    pintarCamaras(datos.camaras);
  } catch (e) {
    $("momento").textContent = `sin datos: ${e.message}`;
  }
}

async function entrar() {
  const valor = $("token").value.trim();
  if (valor) guardarToken(valor);
  try {
    await pedir("salud");
  } catch (e) {
    $("error-acceso").textContent = valor ? `No ha funcionado: ${e.message}` : "";
    return;
  }
  $("acceso").classList.add("oculto");
  $("contenido").classList.remove("oculto");
  $("pestanas").classList.remove("oculto");
  // Por el ingress no hay token que olvidar.
  $("salir").classList.toggle("oculto", !token());
  let vista = "inicio";
  try { vista = localStorage.getItem(CLAVE_VISTA) || "inicio"; } catch { /* nada */ }
  irA(document.querySelector(`[data-vista="${vista}"]`) ? vista : "inicio");
  await refrescar();
  // Limpiar antes de armar: dos clics en Entrar dejaban dos temporizadores y
  // olvidarToken() solo cancelaba el ultimo.
  armarRefresco();
}

// Con la pestana en segundo plano no hay nadie mirando, y cada pasada son
// varias lecturas a Home Assistant y al inversor.
function armarRefresco() {
  if (temporizador) clearInterval(temporizador);
  temporizador = document.hidden ? null : setInterval(refrescar, REFRESCO_MS);
}

document.addEventListener("visibilitychange", () => {
  if (!$("contenido").classList.contains("oculto")) {
    if (!document.hidden) refrescar();
    armarRefresco();
  }
});

$("entrar").addEventListener("click", entrar);
// Por el ingress no hace falta token: se prueba a entrar directamente y, si
// el backend pide token (401), se queda el formulario a la vista.
entrar();
$("token").addEventListener("keydown", (e) => { if (e.key === "Enter") entrar(); });
$("salir").addEventListener("click", olvidarToken);
$("ver-tabla").addEventListener("click", () => {
  const t = $("tabla-mezcla");
  t.classList.toggle("oculto");
  $("ver-tabla").textContent = t.classList.contains("oculto") ? "Ver como tabla" : "Ocultar tabla";
});
$("tema").addEventListener("click", () => {
  const raiz = document.documentElement;
  const oscuro = raiz.getAttribute("data-theme") === "dark"
    || (!raiz.hasAttribute("data-theme")
        && window.matchMedia("(prefers-color-scheme: dark)").matches);
  raiz.setAttribute("data-theme", oscuro ? "light" : "dark");
});

// Panel de casa. Solo lectura: las acciones van por el chat, que pasa por la
// capa de seguridad. Los nombres que llegan del backend se insertan con
// textContent, nunca con innerHTML: vienen de aparatos y de Home Assistant, y
// son datos, no marcado.
"use strict";

const CLAVE = "casa-ai-token";
const REFRESCO_MS = 10000;

const $ = (id) => document.getElementById(id);
let temporizador = null;

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
// Unos 35 trios createElement/className/textContent escritos a mano en cinco
// dialectos distintos del mismo patron. Con esto, cambiar la estructura visual
// se hace en un sitio.

function el(tag, clase, texto) {
  const nodo = document.createElement(tag);
  if (clase) nodo.className = clase;
  if (texto !== undefined) nodo.textContent = texto;
  return nodo;
}

// Muestra u oculta una tarjeta y dice si hay algo que pintar dentro, para que
// cada seccion arranque con una linea en vez de con tres.
function visible(id, hay) {
  $(id).classList.toggle("oculto", !hay);
  return hay;
}

const hayDatos = (x) => Boolean(x) && !x.no_disponible;

// --- Formato ----------------------------------------------------------------

const vatios = (w) => {
  const n = Number(w) || 0;
  return Math.abs(n) >= 1000
    ? `${(n / 1000).toLocaleString("es-ES", { maximumFractionDigits: 1 })} kW`
    : `${Math.round(n)} W`;
};

function indicador(etiqueta, valor, unidad, pie, clase) {
  const d = el("div", clase ? `indicador ${clase}` : "indicador");
  const v = el("div", "valor", valor);
  if (unidad) v.appendChild(el("span", "unidad", ` ${unidad}`));
  d.append(el("div", "etiqueta", etiqueta), v);
  if (pie) d.appendChild(el("div", "pie", pie));
  return d;
}

// --- Pista flotante ---------------------------------------------------------
// El valor manda y el nombre acompaña: aquí el lector ya sabe qué serie es y
// lo que quiere es el número. La clave es un trazo, no un cuadro relleno.

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

// --- Secciones --------------------------------------------------------------

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
      vatios(Math.abs(r.red_w)),
      "",
      r.red_estado === "exportando" ? "a la red" : "de la red",
      "red",
    ),
    r.bateria_soc_pct === null || r.bateria_soc_pct === undefined
      ? indicador("Batería", "—", "", "sin lectura", "bateria")
      : indicador("Batería", String(Math.round(r.bateria_soc_pct)), "%", r.bateria_estado, "bateria"),
  );
  pintarFlujo(r);
}

// El diagrama: sol, batería y red alrededor de la casa. Cada línea se anima
// en el sentido en que va la energía, y se apaga cuando no pasa nada por ella.
// Batería positiva = cargando (de la casa hacia ella); red positiva =
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
  // La línea de la batería está dibujada desde la casa: "atrás" es hacia la casa.
  linea("linea-bateria", r.bateria_w, Number(r.bateria_w) < 0);
  linea("linea-red", r.red_w, r.red_estado !== "exportando");
}

function pintarBateria(energia) {
  if (!visible("tarjeta-bateria", hayDatos(energia))) return;

  const r = energia.resumen;
  const estado = $("estado-bateria");
  estado.textContent = "";
  // Sin lectura de SOC no se pinta un 0 %: se dice que no se sabe.
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

  // El relleno lleva la severidad. El color nunca va solo: le acompaña
  // siempre un punto y un texto.
  let color = "var(--cian)";
  let texto = r.bateria_estado || "";
  if (soc < 10) { color = "var(--critico)"; texto += " · muy baja"; }
  else if (soc < 20) { color = "var(--aviso)"; texto += " · baja"; }
  $("relleno-bateria").style.background = color;

  const punto = el("span", "punto");
  punto.style.background = color;
  estado.append(punto, document.createTextNode(texto));
  if (r.bateria_w) {
    estado.append(document.createTextNode(` a ${vatios(Math.abs(r.bateria_w))}`));
  }
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

  // Las series se derivan UNA vez. Antes cada una se recalculaba en cuatro
  // sitios independientes (el total, el filtro de presentes, el bucle de
  // segmentos y otra vez en la tabla), asi que cambiar el umbral del 2 % o
  // anadir una cuarta fuente obligaba a tocar cuatro bucles y mantenerlos de
  // acuerdo.
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
    // El `remove` va aqui tambien: si la pasada anterior habia ocultado la
    // barra por ser de fuente unica, sin esto se quedaba oculta para siempre.
    barra.classList.add("oculto");
    $("nota-mezcla").textContent = "La casa no está consumiendo nada ahora mismo.";
    $("ver-tabla").classList.add("oculto");
    return;
  }
  $("ver-tabla").classList.remove("oculto");

  // Con una sola fuente, una barra apilada seria un grafico de una sola barra:
  // el numero ya es el grafico. Se dice con palabras y se deja la tabla para
  // quien quiera el desglose exacto.
  const presentes = partes.filter((x) => x.pct > 2);
  if (presentes.length === 1) {
    barra.classList.add("oculto");
    leyenda.appendChild(
      indicador(`Todo de ${presentes[0].nombre.toLowerCase()}`, vatios(total)),
    );
    $("nota-mezcla").textContent =
      "Derivado del balance del inversor, no de un contador por rama.";
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
  // Etiqueta directa dentro del segmento cuando cabe. Es lo que releva el
  // aviso de contraste del aqua en modo claro.
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
  nodo.append(
    muestra,
    document.createTextNode(`${serie.nombre} `),
    el("b", null, vatios(serie.w)),
  );
  return nodo;
}

// Vista de tabla: todo valor de la barra es alcanzable sin pasar el ratón, que
// es además el relevo que exige el aviso de contraste del aqua en modo claro.
function pintarTabla(partes) {
  const tabla = el("table");
  const cabecera = el("tr");
  for (const texto of ["Fuente", "Potencia", "Parte"]) {
    cabecera.appendChild(el("th", texto === "Fuente" ? null : "num", texto));
  }
  tabla.appendChild(cabecera);
  for (const serie of partes) {
    const fila = el("tr");
    fila.append(
      el("td", null, serie.nombre),
      el("td", "num", vatios(serie.w)),
      el("td", "num", `${Math.round(serie.pct)} %`),
    );
    tabla.appendChild(fila);
  }
  $("tabla-mezcla").textContent = "";
  $("tabla-mezcla").appendChild(tabla);
}

function pintarExcedente(datos) {
  if (!visible("tarjeta-excedente", hayDatos(datos))) return;

  const cont = $("excedente");
  cont.textContent = "";
  const disponible = datos.excedente.disponible_para_gastar_w;

  cont.appendChild(indicador("Disponible para gastar", vatios(disponible)));

  const rec = datos.recomendacion || {};
  const lista = el("ul");
  // Los dos bucles eran quince lineas identicas salvo el color y el prefijo.
  for (const [aparatos, color, prefijo] of [
    [rec.encender, "var(--bien)", "Cabe"],
    [rec.apagar, "var(--aviso)", "Sobra encendido"],
  ]) {
    for (const x of aparatos || []) {
      lista.appendChild(fila(color, `${prefijo}: ${x.nombre}`, vatios(x.consumo_w)));
    }
  }
  if (!lista.children.length) {
    lista.appendChild(el("li", "aviso", disponible > 0
      ? "Hay excedente pero nada de lo declarado cabe en él."
      : "No hay excedente ahora mismo."));
  }
  cont.appendChild(lista);
}

// Una fila de lista: punto de color (o un nodo a la izquierda), nombre que
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

// El estado de un aparato es tri-estado: encendido, apagado, o no se sabe.
const ESTADO_APARATO = new Map([
  [true, { color: "var(--bien)", texto: "encendido" }],
  [false, { color: "var(--tinta-apagada)", texto: "apagado" }],
]);

function pintarDispositivos(lista) {
  if (!visible("tarjeta-dispositivos", Array.isArray(lista) && lista.length)) return;

  const ul = $("dispositivos");
  ul.textContent = "";
  for (const d of lista) {
    if (d.encendido === true) ul.appendChild(fila(null, d.nombre, "encendido", { pastilla: "bien" }));
    else if (d.encendido === false) ul.appendChild(fila(null, d.nombre, "apagado", { pastilla: "neutra" }));
    else ul.appendChild(fila(null, d.nombre, d.categoria || "", { pastilla: "neutra" }));
  }
}

// --- La casa segun Home Assistant ------------------------------------------

function pintarLista(idTarjeta, idLista, elementos, aFila) {
  if (!visible(idTarjeta, Array.isArray(elementos) && elementos.length)) return;
  const ul = $(idLista);
  ul.textContent = "";
  for (const x of elementos) ul.appendChild(aFila(x));
}

function pintarCasa(casa) {
  const hay = casa && typeof casa === "object" && !casa.no_disponible;
  if (!hay) {
    for (const id of ["presencia", "accesos", "luces", "persianas", "clima"]) visible(`tarjeta-${id}`, false);
    $("sistemas").textContent = "";
    return;
  }

  pintarLista("tarjeta-presencia", "presencia", casa.presencia, (p) => {
    const avatar = el("span", p.en_casa ? "avatar en-casa" : "avatar", (p.nombre || "?").slice(0, 1).toUpperCase());
    return fila(null, p.nombre, p.en_casa ? "en casa" : "fuera",
      { izquierda: avatar, pastilla: p.en_casa ? "bien" : "neutra" });
  });

  // Un acceso abierto o sin llave es lo unico del panel que pide atencion.
  pintarLista("tarjeta-accesos", "accesos", casa.accesos, (a) =>
    fila(null, a.nombre, a.estado, { pastilla: a.abierto ? "aviso" : "bien" }));

  const luces = casa.luces;
  if (visible("tarjeta-luces", luces && luces.total > 0)) {
    const n = luces.encendidas.length;
    $("luces-resumen").textContent = `${n} de ${luces.total} encendidas`;
    const chips = $("luces");
    chips.textContent = "";
    for (const nombre of luces.encendidas) chips.appendChild(el("span", "chip", nombre));
    $("luces-vacio").classList.toggle("oculto", n > 0);
  }

  pintarLista("tarjeta-persianas", "persianas", casa.persianas, (p) => {
    const conPosicion = p.posicion !== null && p.posicion !== undefined;
    if (!conPosicion) {
      return fila(null, p.nombre, p.estado, { pastilla: p.estado === "cerrada" ? "neutra" : "cian" });
    }
    const barra = el("span", "barrita");
    const dentro = el("i");
    dentro.style.width = `${Math.max(0, Math.min(100, Number(p.posicion)))}%`;
    barra.appendChild(dentro);
    const derecha = el("span", "sec", `${Math.round(p.posicion)} %`);
    const li = fila(null, p.nombre, undefined, { derecha: barra });
    li.appendChild(derecha);
    return li;
  });

  pintarLista("tarjeta-clima", "clima", casa.clima, (c) => {
    const grados = el("span", "grados");
    if (c.actual !== null && c.actual !== undefined) {
      grados.textContent = `${Number(c.actual).toLocaleString("es-ES", { maximumFractionDigits: 1 })}°`;
    } else {
      grados.textContent = "—";
    }
    if (c.objetivo !== null && c.objetivo !== undefined) {
      grados.appendChild(el("small", null, ` → ${c.objetivo}°`));
    }
    const apagado = ["off", "apagada", "en reposo"].includes(c.modo);
    const li = fila(null, c.nombre, c.modo, { pastilla: apagado ? "neutra" : "cian" });
    li.insertBefore(grados, li.lastChild);
    return li;
  });

  // Una tarjeta por sistema declarado en `panel:` (coche, agua, spa, riego...).
  const cont = $("sistemas");
  cont.textContent = "";
  for (const s of casa.sistemas || []) {
    const tarjeta = el("section", "tarjeta");
    tarjeta.appendChild(el("h2", null, s.titulo));
    const ul = el("ul");
    for (const l of s.lineas) {
      const sinDato = l.valor === "sin dato" || l.valor === "sin conexion";
      ul.appendChild(fila(null, l.nombre, l.valor, { pastilla: sinDato ? "neutra" : undefined }));
    }
    tarjeta.appendChild(ul);
    cont.appendChild(tarjeta);
  }
}

// Lo que suena en cada reproductor. Un reproductor apagado no es un error del
// panel: se dice y se sigue.
function pintarMusica(lista) {
  if (!visible("tarjeta-musica", Array.isArray(lista) && lista.length)) return;

  const ul = $("musica");
  ul.textContent = "";
  for (const m of lista) {
    const nombre = m.zona && m.zona !== m.reproductor.toLowerCase() ? `${m.reproductor} · ${m.zona}` : m.reproductor;
    if (m.error) {
      ul.appendChild(fila(null, nombre, "no responde", { pastilla: "neutra" }));
    } else if (m.estado === "play" || m.estado === "stream") {
      const ondas = el("span", "sonando");
      ondas.append(el("i"), el("i"), el("i"));
      const que = [m.titulo, m.artista].filter(Boolean).join(" — ") || m.servicio || "sonando";
      ul.appendChild(fila(null, nombre, que, { izquierda: ondas }));
    } else {
      ul.appendChild(fila(null, nombre, "en silencio", { pastilla: "neutra" }));
    }
  }
}

// La salud de la red segun UniFi: internet, wifi y cable. Nada de lo que
// pinta aqui permite tocar la red; eso va por el chat y con confirmacion.
const SUBSISTEMAS_RED = new Map([
  ["wan", "Internet"], ["wlan", "WiFi"], ["lan", "Cable"], ["vpn", "VPN"],
]);

function pintarRed(red) {
  if (!visible("tarjeta-red", red && typeof red === "object" && !red.no_disponible
      && Object.keys(red).length)) return;

  const ul = $("red");
  ul.textContent = "";
  for (const [clave, etiqueta] of SUBSISTEMAS_RED) {
    const s = red[clave];
    if (!s) continue;
    const bien = s.estado === "ok";
    let detalle = "";
    if (clave === "wan" && s.latencia_ms !== undefined && s.latencia_ms !== null) {
      detalle = `${s.latencia_ms} ms`;
    } else if (s.usuarios !== undefined && s.usuarios !== null) {
      detalle = `${s.usuarios} conectados`;
    }
    if (s.caidos) detalle += ` · ${s.caidos} caídos`;
    const li = fila(null, etiqueta, bien ? "bien" : (s.estado || "?"),
      { pastilla: bien ? "bien" : "critico" });
    if (detalle) li.insertBefore(el("span", "sec", detalle), li.lastChild);
    ul.appendChild(li);
  }
}

async function pintarCamaras(camaras) {
  if (!visible("tarjeta-camaras", Array.isArray(camaras) && camaras.length)) return;
  const cont = $("camaras");
  if (cont.children.length === camaras.length) return;  // ya montadas

  cont.textContent = "";
  for (const c of camaras) {
    const fig = el("figure");
    const img = el("img");
    img.alt = `Cámara ${c.nombre}`;
    const zonaDistinta = c.zona && c.zona.toLowerCase() !== c.nombre.toLowerCase();
    const pie = el("figcaption", null, zonaDistinta ? `${c.nombre} · ${c.zona}` : c.nombre);
    fig.append(img, pie);
    cont.appendChild(fig);
    // Fetch en vez de src directo: la imagen necesita la cabecera del token.
    try {
      const r = await pedir(`api/panel/camara/${encodeURIComponent(c.nombre)}`);
      img.src = URL.createObjectURL(await r.blob());
    } catch {
      pie.textContent += " — sin imagen";
    }
  }
}

// El saludo: por la hora del dia y, si el backend sabe quien mira, con su nombre.
function saludo(quien) {
  const h = new Date().getHours();
  const base = h < 6 ? "Buenas noches" : h < 14 ? "Buenos días" : h < 21 ? "Buenas tardes" : "Buenas noches";
  return quien ? `${base}, ${quien}` : base;
}

// --- Ciclo ------------------------------------------------------------------

async function refrescar() {
  try {
    const datos = await (await pedir("api/panel")).json();
    $("momento").textContent = datos.momento;
    $("saludo").textContent = saludo(datos.quien);
    pintarIndicadores(datos.energia);
    pintarBateria(datos.energia);
    pintarMezcla(datos.energia);
    pintarExcedente(datos.excedente);
    pintarCasa(datos.casa);
    pintarDispositivos(datos.dispositivos);
    pintarMusica(datos.musica);
    pintarRed(datos.red);
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
  $("salir").classList.remove("oculto");
  await refrescar();
  // Limpiar antes de armar: dos clics en Entrar dejaban dos temporizadores y
  // olvidarToken() solo cancelaba el ultimo.
  armarRefresco();
}

// Con la pestana en segundo plano no hay nadie mirando, y cada pasada son
// varias lecturas a Home Assistant y al inversor: seis por minuto durante
// horas por un panel que alguien dejo abierto en otra ventana.
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

$("mensaje").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("enviar").click(); }
});
$("enviar").addEventListener("click", async () => {
  const mensaje = $("mensaje").value.trim();
  if (!mensaje) return;
  $("respuesta").classList.add("con-texto");
  $("respuesta").textContent = "Pensando…";
  try {
    const r = await pedir("chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mensaje, hilo: "panel" }),
    });
    $("respuesta").textContent = (await r.json()).respuesta;
  } catch (e) {
    $("respuesta").textContent = `Error: ${e.message}`;
  }
  await refrescar();
});

if (token()) { entrar(); } else { $("acceso").classList.remove("oculto"); }

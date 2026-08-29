"use strict";

/* Calculadora FIFO para la renta — todo se ejecuta en este navegador.
 * Pyodide (Python compilado a WebAssembly) corre el mismo motor probado
 * con tests en Python (calculadora.py / tipos_cambio.py / lector_csv.py /
 * motor_web.py). El fichero del usuario NUNCA se sube a ningún sitio:
 * se lee con el File API del navegador y se pasa como texto a Python,
 * todo dentro de esta pestaña.
 */

const FICHEROS_MOTOR = [
  "calculadora.py",
  "tipos_cambio.py",
  "lector_csv.py",
  "motor_web.py",
  "presets_broker.json",
];
const RUTA_CACHE_BCE = "cache/eurofxref-hist.xml";
const CLAVE_LOCALSTORAGE_PRESETS = "fifo-renta:presets_broker";
const PREFIJO_LOCALSTORAGE_MAPEO = "fifo-renta:mapeo:";

const CAMPOS_OBLIGATORIOS = ["fecha", "tipo", "valor", "cantidad", "precio"];
const CAMPOS_OPCIONALES = ["divisa", "comision"];
const ETIQUETAS_CAMPO = {
  fecha: "Fecha",
  tipo: "Tipo de operación",
  valor: "ISIN / valor",
  cantidad: "Cantidad",
  precio: "Precio",
  divisa: "Divisa (opcional, si falta se asume EUR)",
  comision: "Comisión (opcional, si falta se asume 0)",
};

let pyodide = null;
let lectorCsv = null;
let motorWeb = null;
let bceDisponible = false;
let textoCsvActual = null;
let cabecerasActuales = [];
let ultimoResultado = null;

const el = (id) => document.getElementById(id);

function mostrar(elemento) { elemento.classList.remove("oculto"); }
function ocultar(elemento) { elemento.classList.add("oculto"); }

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto;
  return div.innerHTML;
}

// --- Arranque -----------------------------------------------------------
//
// Dos etapas INDEPENDIENTES, cada una con su propio estado y su propio
// mensaje de error, para que un fallo nunca se atribuya al componente
// equivocado:
//   1. cargarMotor(): Pyodide + los .py del motor. Si esto falla, no se
//      puede hacer nada (ni EUR ni nada), así que sí bloquea el resto.
//   2. cargarTiposBCE(): el fichero de tipos de cambio. Si esto falla,
//      el motor sigue funcionando perfectamente para operaciones en EUR
//      (que no necesitan el BCE); solo las de otras divisas fallarán, y
//      lo harán con su propio error explicado en el desglose de ese
//      valor — no aquí arriba, y no como si el motor no hubiera cargado.

async function iniciar() {
  const motorListo = await cargarMotor();
  if (!motorListo) return;   // sin Python no hay nada que hacer

  mostrar(el("zona-carga"));
  configurarZonaCarga();

  await cargarTiposBCE();
}

async function cargarMotor() {
  try {
    pyodide = await loadPyodide();

    for (const nombre of FICHEROS_MOTOR) {
      if (nombre === "presets_broker.json") continue;   // se trata aparte: puede venir de localStorage
      const respuesta = await fetch(`motor/${nombre}`);
      if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status} al pedir motor/${nombre}`);
      pyodide.FS.writeFile(nombre, await respuesta.text());
    }

    // El fichero de presets que se sirve con la web es solo el punto de
    // partida. Pyodide corre en un filesystem en memoria que se pierde al
    // recargar la página, así que si el usuario ya ha guardado presets en
    // este navegador, se restauran desde localStorage en vez de pisarlos
    // con el fichero por defecto.
    pyodide.FS.writeFile("presets_broker.json", await cargarPresetsIniciales());

    lectorCsv = pyodide.pyimport("lector_csv");
    motorWeb = pyodide.pyimport("motor_web");

    el("estado-motor-texto").textContent = "Motor de cálculo listo.";
    ocultar(el("estado-motor"));
    return true;
  } catch (error) {
    console.error("Fallo cargando el motor de cálculo (Pyodide/Python):", error);
    el("estado-motor").querySelector(".spinner")?.remove();
    el("estado-motor-texto").innerHTML =
      "No se ha podido cargar el motor de cálculo (Python). Comprueba tu conexión y recarga la página. " +
      `<br><span class="texto-pequeno">${escaparHtml(error.message || String(error))}</span>`;
    el("estado-motor").classList.add("aviso", "aviso-error");
    return false;
  }
}

async function cargarTiposBCE() {
  try {
    pyodide.FS.mkdir("cache");
    const respuesta = await fetch(`motor/${RUTA_CACHE_BCE}`);
    if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status} al pedir motor/${RUTA_CACHE_BCE}`);
    pyodide.FS.writeFile(RUTA_CACHE_BCE, await respuesta.text());

    bceDisponible = true;
    mostrarFrescuraBCE();
  } catch (error) {
    console.error("Fallo cargando los tipos de cambio del BCE:", error);
    bceDisponible = false;

    const contenedor = el("frescura-bce");
    contenedor.classList.add("desactualizado");
    contenedor.innerHTML =
      "No se han podido cargar los tipos de cambio oficiales del BCE " +
      "(el motor de cálculo sí ha cargado bien). Puedes calcular operaciones en EUR con normalidad; " +
      "las de otras divisas no se podrán calcular hasta que esto se resuelva. " +
      `<br><span class="texto-pequeno">${escaparHtml(error.message || String(error))}</span>`;
    mostrar(contenedor);
  }
}

async function cargarPresetsIniciales() {
  try {
    const guardado = localStorage.getItem(CLAVE_LOCALSTORAGE_PRESETS);
    if (guardado) return guardado;
  } catch (error) {
    console.warn("No se han podido leer los presets guardados en este navegador:", error);
  }
  const respuesta = await fetch("motor/presets_broker.json");
  return respuesta.ok ? await respuesta.text() : "{}";
}

function guardarPresetsEnLocalStorage() {
  try {
    const contenido = pyodide.FS.readFile("presets_broker.json", { encoding: "utf8" });
    localStorage.setItem(CLAVE_LOCALSTORAGE_PRESETS, contenido);
  } catch (error) {
    console.warn("No se ha podido recordar el preset en este navegador:", error);
  }
}

// --- Recordar el ultimo mapeo usado para unas mismas cabeceras ---------
//
// Ademas de los presets con nombre (guardados explicitamente), se recuerda
// sin que el usuario tenga que hacer nada el ultimo mapeo que funciono
// para un fichero con exactamente estas cabeceras (mismo nombre de
// columnas, en cualquier orden), para no obligar a repetirlo si se sube
// otro extracto del mismo broker.

function claveMapeoLocal(cabeceras) {
  const huella = [...cabeceras].map((c) => c.trim().toLowerCase()).sort().join("||");
  return PREFIJO_LOCALSTORAGE_MAPEO + huella;
}

function recordarMapeoLocal(cabeceras, mapeo) {
  try {
    localStorage.setItem(claveMapeoLocal(cabeceras), JSON.stringify(mapeo));
  } catch (error) {
    console.warn("No se ha podido recordar el mapeo en este navegador:", error);
  }
}

function mapeoRecordadoLocal(cabeceras) {
  try {
    const guardado = localStorage.getItem(claveMapeoLocal(cabeceras));
    return guardado ? JSON.parse(guardado) : null;
  } catch (error) {
    console.warn("No se ha podido leer el mapeo recordado de este navegador:", error);
    return null;
  }
}

function mostrarFrescuraBCE() {
  const infoPy = motorWeb.info_frescura_bce();
  const info = infoPy.toJs({ dict_converter: Object.fromEntries });
  infoPy.destroy();

  const contenedor = el("frescura-bce");
  mostrar(contenedor);

  if (!info.ok) {
    contenedor.classList.add("desactualizado");
    contenedor.textContent = `No se ha podido comprobar la fecha de los tipos de cambio del BCE: ${info.error}`;
    return;
  }

  let texto = `Tipos del BCE actualizados hasta el ${info.fecha_mas_reciente}.`;
  if (info.desactualizado) {
    contenedor.classList.add("desactualizado");
    texto += ` Este dato tiene ${info.dias_de_antiguedad} días — puede que las operaciones más recientes no se puedan calcular todavía.`;
  }
  contenedor.textContent = texto;
}

// --- Paso 1: arrastrar / elegir el CSV ---------------------------------

function configurarZonaCarga() {
  const zonaDrop = el("zona-drop");
  const inputFichero = el("input-fichero");

  zonaDrop.addEventListener("click", () => inputFichero.click());
  zonaDrop.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter" || evento.key === " ") { evento.preventDefault(); inputFichero.click(); }
  });

  ["dragenter", "dragover"].forEach((tipo) =>
    zonaDrop.addEventListener(tipo, (evento) => {
      evento.preventDefault();
      zonaDrop.classList.add("sobre-elemento");
    })
  );
  ["dragleave", "drop"].forEach((tipo) =>
    zonaDrop.addEventListener(tipo, (evento) => {
      evento.preventDefault();
      zonaDrop.classList.remove("sobre-elemento");
    })
  );
  zonaDrop.addEventListener("drop", (evento) => {
    const fichero = evento.dataTransfer.files[0];
    if (fichero) cargarFichero(fichero);
  });

  inputFichero.addEventListener("change", (evento) => {
    const fichero = evento.target.files[0];
    if (fichero) cargarFichero(fichero);
  });
}

function cargarFichero(fichero) {
  ocultarError();
  ocultar(el("zona-resultado"));

  const lector = new FileReader();
  lector.onload = () => {
    textoCsvActual = lector.result;
    const nombreFichero = el("nombre-fichero");
    nombreFichero.textContent = `Fichero cargado: ${fichero.name} (${fichero.size.toLocaleString("es-ES")} bytes) — no se ha subido a ningún sitio.`;
    mostrar(nombreFichero);
    detectarYPreparearMapeo();
  };
  lector.onerror = () => mostrarError("No se ha podido leer el fichero. Prueba a exportarlo de nuevo desde tu bróker.");
  lector.readAsText(fichero);
}

// --- Paso 2: detectar cabeceras y mapear columnas ----------------------

function detectarYPreparearMapeo() {
  let resultado;
  try {
    const resultadoPy = lectorCsv.detectar_csv.callKwargs({ contenido: textoCsvActual });
    resultado = resultadoPy.toJs();
    resultadoPy.destroy();
  } catch (error) {
    mostrarError(`No se ha podido leer el CSV: ${mensajeDeErrorPython(error)}`);
    return;
  }

  const [cabeceras, filasMuestra] = resultado;
  cabecerasActuales = cabeceras;

  pintarTablaMapeo(cabeceras);
  pintarTablaMuestra(cabeceras, filasMuestra);
  pintarSelectorPresets();
  aplicarAutoDeteccion(cabeceras);

  mostrar(el("zona-mapeo"));
  actualizarBotonCalcular();
}

// Encuentra el mapeo de columnas sin que el usuario tenga que elegirlas a
// mano, probando por este orden (el primero que encaja gana):
//   1. Un mapeo recordado de un fichero anterior con las mismas cabeceras.
//   2. Un preset guardado (con nombre) cuyas columnas existan todas aqui.
//   3. Adivinar cada campo por el nombre de su cabecera (sugerir_mapeo),
//      tolerando mayusculas/acentos/sinonimos habituales. Puede acertar
//      solo alguno de los campos: el resto se deja para elegir a mano.
function aplicarAutoDeteccion(cabeceras) {
  const avisoPreset = el("preset-sugerido");
  const avisoAuto = el("mapeo-auto-detectado");
  ocultar(avisoPreset);
  ocultar(avisoAuto);

  const recordado = mapeoRecordadoLocal(cabeceras);
  if (recordado) {
    aplicarMapeoAlFormulario(recordado);
    avisoAuto.textContent = "Hemos recordado el mapeo que usaste la última vez con estas mismas cabeceras. Puedes cambiar cualquier columna si no es correcto.";
    mostrar(avisoAuto);
    return;
  }

  const cabecerasSet = new Set(cabeceras);
  const presetQueEncaja = obtenerPresets().find((preset) =>
    Object.values(preset.mapeo).every((columna) => cabecerasSet.has(columna))
  );
  if (presetQueEncaja) {
    aplicarMapeoAlFormulario(presetQueEncaja.mapeo);
    avisoPreset.textContent = `Este fichero encaja con el preset "${presetQueEncaja.nombre}": lo hemos preseleccionado. Puedes cambiar cualquier columna si no es correcto.`;
    mostrar(avisoPreset);
    setTimeout(() => { el("select-preset").value = presetQueEncaja.nombre; }, 0);
    return;
  }

  const sugerenciaPy = lectorCsv.sugerir_mapeo(pyodide.toPy(cabeceras));
  const sugerencia = sugerenciaPy.toJs({ dict_converter: Object.fromEntries });
  sugerenciaPy.destroy();

  const nDetectados = Object.keys(sugerencia).length;
  if (nDetectados > 0) {
    aplicarMapeoAlFormulario(sugerencia);
    const totalCampos = CAMPOS_OBLIGATORIOS.length + CAMPOS_OPCIONALES.length;
    avisoAuto.textContent = `Hemos detectado automáticamente ${nDetectados} de ${totalCampos} columnas por el nombre de la cabecera. Revisa y completa el resto.`;
    mostrar(avisoAuto);
  }
}

function pintarTablaMapeo(cabeceras) {
  const cuerpo = el("tabla-mapeo").querySelector("tbody");
  cuerpo.innerHTML = "";

  const todosLosCampos = [...CAMPOS_OBLIGATORIOS, ...CAMPOS_OPCIONALES];
  for (const campo of todosLosCampos) {
    const fila = document.createElement("tr");

    const celdaEtiqueta = document.createElement("td");
    celdaEtiqueta.textContent = ETIQUETAS_CAMPO[campo] + (CAMPOS_OBLIGATORIOS.includes(campo) ? " *" : "");
    fila.appendChild(celdaEtiqueta);

    const celdaSelect = document.createElement("td");
    const select = document.createElement("select");
    select.id = `mapeo-${campo}`;
    select.dataset.campo = campo;

    const opcionVacia = document.createElement("option");
    opcionVacia.value = "";
    opcionVacia.textContent = CAMPOS_OBLIGATORIOS.includes(campo) ? "— elige una columna —" : "— ninguna (usar valor por defecto) —";
    select.appendChild(opcionVacia);

    for (const cabecera of cabeceras) {
      const opcion = document.createElement("option");
      opcion.value = cabecera;
      opcion.textContent = cabecera;
      select.appendChild(opcion);
    }

    select.addEventListener("change", actualizarBotonCalcular);
    celdaSelect.appendChild(select);
    fila.appendChild(celdaSelect);
    cuerpo.appendChild(fila);
  }
}

function pintarTablaMuestra(cabeceras, filas) {
  const tabla = el("tabla-muestra");
  tabla.innerHTML = "";

  const filaCabecera = document.createElement("tr");
  cabeceras.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    filaCabecera.appendChild(th);
  });
  tabla.appendChild(filaCabecera);

  filas.forEach((fila) => {
    const tr = document.createElement("tr");
    fila.forEach((valor) => {
      const td = document.createElement("td");
      td.textContent = valor;
      tr.appendChild(td);
    });
    tabla.appendChild(tr);
  });
}

function obtenerPresets() {
  const listaPy = lectorCsv.listar_presets();
  const nombres = listaPy.toJs();
  listaPy.destroy();

  return nombres.map((nombre) => {
    const parPy = lectorCsv.cargar_preset(nombre);
    const [mapeoPy] = parPy.toJs();
    const mapeo = Object.fromEntries(mapeoPy);
    parPy.destroy();
    return { nombre, mapeo };
  });
}

function aplicarMapeoAlFormulario(mapeo) {
  for (const campo of [...CAMPOS_OBLIGATORIOS, ...CAMPOS_OPCIONALES]) {
    const select = el(`mapeo-${campo}`);
    const columna = mapeo[campo] || "";
    select.value = cabecerasActuales.includes(columna) ? columna : "";
  }
  actualizarBotonCalcular();
}

function pintarSelectorPresets() {
  const select = el("select-preset");
  select.innerHTML = '<option value="">— Elegir columnas a mano —</option>';

  for (const preset of obtenerPresets()) {
    const opcion = document.createElement("option");
    opcion.value = preset.nombre;
    opcion.textContent = preset.nombre;
    select.appendChild(opcion);
  }

  select.onchange = () => {
    if (!select.value) return;
    const preset = obtenerPresets().find((p) => p.nombre === select.value);
    if (preset) aplicarMapeoAlFormulario(preset.mapeo);
  };
}

function leerMapeoDelFormulario() {
  const mapeo = {};
  for (const campo of CAMPOS_OBLIGATORIOS) {
    const valor = el(`mapeo-${campo}`).value;
    if (!valor) return null;
    mapeo[campo] = valor;
  }
  for (const campo of CAMPOS_OPCIONALES) {
    const valor = el(`mapeo-${campo}`).value;
    if (valor) mapeo[campo] = valor;   // si no se elige, NO se incluye la clave (valor por defecto en Python)
  }
  return mapeo;
}

function actualizarBotonCalcular() {
  el("boton-calcular").disabled = leerMapeoDelFormulario() === null;
}

el("boton-calcular").addEventListener("click", calcular);

el("boton-guardar-preset").addEventListener("click", () => {
  ocultarError();
  const nombre = el("input-nombre-preset").value.trim();
  if (!nombre) { mostrarError("Escribe un nombre para guardar el preset."); return; }

  const mapeo = leerMapeoDelFormulario();
  if (!mapeo) { mostrarError("Completa antes las columnas obligatorias (*) para poder guardar el preset."); return; }

  try {
    lectorCsv.guardar_preset(nombre, pyodide.toPy(new Map(Object.entries(mapeo))));
  } catch (error) {
    mostrarError(`No se ha podido guardar el preset: ${mensajeDeErrorPython(error)}`);
    return;
  }

  guardarPresetsEnLocalStorage();
  el("input-nombre-preset").value = "";
  pintarSelectorPresets();
  el("select-preset").value = nombre;
});

// --- Paso 3: calcular y mostrar resultados -----------------------------

function calcular() {
  ocultarError();
  const mapeo = leerMapeoDelFormulario();
  if (!mapeo) return;

  let resultado;
  try {
    const mapeoPy = pyodide.toPy(new Map(Object.entries(mapeo)));
    const resultadoPy = motorWeb.procesar_csv(textoCsvActual, mapeoPy);
    resultado = resultadoPy.toJs({ dict_converter: Object.fromEntries });
    resultadoPy.destroy();
    mapeoPy.destroy();
  } catch (error) {
    mostrarError(`No se ha podido calcular: ${mensajeDeErrorPython(error)}`);
    return;
  }

  ultimoResultado = resultado;

  if (resultado.error_lectura) {
    mostrarError(`El fichero no se puede leer con este mapeo: ${resultado.error_lectura}`);
    ocultar(el("zona-resultado"));
    return;
  }

  // El mapeo ha funcionado (aunque algun valor concreto falle despues por
  // otro motivo, p.ej. tipo de cambio no disponible): lo recordamos para
  // no obligar a repetirlo si se sube otro fichero con estas cabeceras.
  recordarMapeoLocal(cabecerasActuales, mapeo);

  pintarResultado(resultado);
  mostrar(el("zona-resultado"));
}

function pintarResultado(resultado) {
  pintarTotales(resultado);
  pintarAvisos(resultado.avisos_lectura);
  pintarDividendos(resultado.dividendos);
  pintarValores(resultado.valores);

  const hayAlgoQueDescargar = Object.values(resultado.valores).some((v) => v.desglose && v.desglose.length);
  el("boton-descargar").classList.toggle("oculto", !hayAlgoQueDescargar);
}

function pintarTotales(resultado) {
  const contenedor = el("totales");
  const { completo, ganancia_patrimonial, motivo } = resultado.totales;

  if (completo) {
    const negativo = ganancia_patrimonial.trim().startsWith("-");
    contenedor.innerHTML = `
      <h2>Ganancia patrimonial total</h2>
      <p class="total-principal ${negativo ? "negativo" : ""}">${ganancia_patrimonial} €</p>
      <p class="total-secundario">Suma del FIFO de todos los valores del fichero, ya aplicada la regla de los 2 meses.</p>
    `;
  } else {
    // Nunca se muestra un importe aqui (ni "0.00 €"): si no es
    // "completo" es que no hay un total fiable que mostrar, y un numero
    // con pinta de valido induciria a pensar que ya esta calculado.
    contenedor.innerHTML = `
      <h2>Ganancia patrimonial total</h2>
      <p class="total-principal">No se ha podido calcular</p>
      <p class="total-secundario">${escaparHtml(motivo || "Revisa los avisos y el detalle de cada valor más abajo.")}</p>
    `;
  }
}

function pintarAvisos(avisos) {
  const contenedor = el("avisos-lectura");
  if (!avisos || avisos.length === 0) { ocultar(contenedor); return; }

  const items = avisos.map((a) => `<li>${escaparHtml(a)}</li>`).join("");
  contenedor.innerHTML = `<h2>Filas no procesadas (${avisos.length})</h2><ul class="lista-avisos">${items}</ul>`;
  mostrar(contenedor);
}

function pintarDividendos(dividendos) {
  const contenedor = el("dividendos");
  if (!dividendos || Object.keys(dividendos.por_valor).length === 0) { ocultar(contenedor); return; }

  const filasPorValor = Object.entries(dividendos.por_valor)
    .map(([valor, d]) => `<tr><td>${escaparHtml(valor)}</td><td>${d.bruto} €</td><td>${d.retencion} €</td></tr>`)
    .join("");

  contenedor.innerHTML = `
    <h2>Dividendos (rendimiento del capital mobiliario)</h2>
    <p class="total-secundario">Van en una casilla distinta de la renta a las ganancias patrimoniales.</p>
    <div class="tabla-scroll">
      <table class="tabla-desglose">
        <thead><tr><th>Valor</th><th>Bruto</th><th>Retención</th></tr></thead>
        <tbody>${filasPorValor}</tbody>
        <tfoot><tr><td><strong>Total</strong></td><td><strong>${dividendos.bruto_total} €</strong></td><td><strong>${dividendos.retencion_total} €</strong></td></tr></tfoot>
      </table>
    </div>
  `;
  mostrar(contenedor);
}

function pintarValores(valores) {
  const contenedor = el("desglose-por-valor");
  contenedor.innerHTML = "";

  for (const [valor, datos] of Object.entries(valores)) {
    const bloque = document.createElement("div");
    bloque.className = "tarjeta valor-bloque";

    if (datos.error) {
      bloque.innerHTML = `
        <h3>${escaparHtml(valor)}</h3>
        <div class="aviso aviso-error">No se puede calcular: ${escaparHtml(datos.error)}</div>
      `;
      contenedor.appendChild(bloque);
      continue;
    }

    const filasDesglose = datos.desglose.map((op) => `
      <tr>
        <td>${op.fecha}</td>
        <td>${op.resultado_bruto} €</td>
        <td>${op.bloqueado} €</td>
        <td>${op.resultado_declarado} €</td>
      </tr>
    `).join("");

    const lotesPendientes = datos.lotes_pendientes.length
      ? `<p class="total-secundario">Quedan sin vender: ${datos.lotes_pendientes.map((l) => `${l.acciones} ud. del ${l.fecha}`).join(", ")}</p>`
      : "";

    bloque.innerHTML = `
      <h3><span>${escaparHtml(valor)}</span><span>${datos.ganancia} €</span></h3>
      ${datos.desglose.length ? `
        <div class="tabla-scroll">
          <table class="tabla-desglose tabla-desglose-ventas">
            <thead><tr><th>Fecha venta</th><th>Bruto</th><th>Bloqueado (2 meses)</th><th>Declarado</th></tr></thead>
            <tbody>${filasDesglose}</tbody>
          </table>
        </div>` : `<p class="total-secundario">Sin ventas en este fichero.</p>`}
      ${lotesPendientes}
    `;
    contenedor.appendChild(bloque);
  }
}

// --- Paso 4: descargar el desglose en CSV ------------------------------

el("boton-descargar").addEventListener("click", descargarDesgloseCSV);

function descargarDesgloseCSV() {
  if (!ultimoResultado) return;

  const filas = [["Valor", "Fecha venta", "Resultado bruto", "Bloqueado (regla 2 meses)", "Resultado declarado"]];
  for (const [valor, datos] of Object.entries(ultimoResultado.valores)) {
    if (datos.error || !datos.desglose) continue;
    for (const op of datos.desglose) {
      filas.push([valor, op.fecha, op.resultado_bruto, op.bloqueado, op.resultado_declarado]);
    }
  }

  const csv = filas.map((fila) => fila.map(csvEscapar).join(";")).join("\r\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);

  const enlace = document.createElement("a");
  enlace.href = url;
  enlace.download = "desglose_ganancias_patrimoniales.csv";
  document.body.appendChild(enlace);
  enlace.click();
  document.body.removeChild(enlace);
  URL.revokeObjectURL(url);
}

function csvEscapar(valor) {
  const texto = String(valor);
  return /[;"\n]/.test(texto) ? `"${texto.replace(/"/g, '""')}"` : texto;
}

// --- Utilidades ---------------------------------------------------------

function mensajeDeErrorPython(error) {
  // Los errores de Python que cruzan a JS via Pyodide traen el mensaje
  // real de la excepcion en error.message, con ruido de traceback detras.
  const texto = String(error && error.message ? error.message : error);
  const primeraLinea = texto.split("\n").find((l) => l.trim().length > 0);
  return primeraLinea || texto;
}

function mostrarError(mensaje) {
  const contenedor = el("zona-error");
  contenedor.textContent = mensaje;
  mostrar(contenedor);
}

function ocultarError() {
  ocultar(el("zona-error"));
}

iniciar();

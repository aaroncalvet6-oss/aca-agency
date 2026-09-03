"use strict";

/* Calculadora FIFO para la renta — todo se ejecuta en este navegador.
 * Pyodide (Python compilado a WebAssembly) corre el mismo motor probado
 * con tests en Python (calculadora.py / tipos_cambio.py / lector_csv.py /
 * motor_web.py, sin modificar ni uno). El fichero del usuario NUNCA se
 * sube a ningún sitio: se lee con el File API del navegador y se pasa
 * como texto a Python, todo dentro de esta pestaña.
 */

const FICHEROS_MOTOR = [
  "calculadora.py",
  "tipos_cambio.py",
  "lector_csv.py",
  "motor_web.py",
  "presets_broker.json",
];
const RUTA_CACHE_BCE = "cache/eurofxref-hist.xml";

// Los cuatro ficheros grandes que loadPyodide() pide de verdad (visto en el
// propio código de pyodide.js): el núcleo WASM, la librería estándar de
// Python, el manifiesto de paquetes y el glue JS. Ni numpy ni pandas ni
// ningún otro paquete científico se cargan — el motor (calculadora.py /
// lector_csv.py / tipos_cambio.py / motor_web.py) solo usa la librería
// estándar, así que no hay nada más que pedir.
const PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const PYODIDE_ARCHIVOS_GRANDES = [
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json",
  "pyodide.asm.js",
];
// Service worker: cachea SOLO los ficheros de Pyodide (URL versionada con
// "v0.26.4", así que son inmutables — nunca cambia el contenido de esa URL)
// para que la segunda visita no vuelva a bajar 13-14 MB. El resto de la web
// (estilo.css, app.js, el motor .py...) sigue yendo a la red normal: no
// queremos que un service worker deje la web publicada pegada a una versión
// vieja después de un despliegue nuevo.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch((error) => {
    console.warn("No se ha podido registrar el service worker de caché de Pyodide:", error);
  });
}

// --- Lectura de CSV en JS puro, solo para el paso 1 --------------------
//
// El paso 1 (soltar el CSV, ver y completar el mapeo de columnas) NUNCA
// necesita a Python: leer cabeceras y adivinar qué columna es cada campo
// es trabajo de texto, no de cálculo fiscal. Portar ESTO a JS (que no es
// "el motor" — no decide FIFO, tipos de cambio ni la regla de los dos
// meses) es lo que permite que el paso 1 esté listo al instante, mientras
// Pyodide sigue cargando de fondo. calcular() (más abajo) sigue pasando
// SIEMPRE por el motor de verdad en Python para el resultado — esto de
// aquí es solo para pintar la tabla de mapeo antes de que el motor esté.
//
// Copia literal (mismo comportamiento, sin reinterpretar nada) de
// _normalizar_cabecera / SINONIMOS_CAMPO / sugerir_mapeo / _detectar_separador
// / detectar_csv de lector_csv.py — si alguna vez se cambia allí, hay que
// replicar el cambio aquí.

const SINONIMOS_CAMPO = {
  fecha: [
    "fecha", "date", "fecha operacion", "fecha ejecucion", "fecha contratacion",
    "trade date", "value date", "fecha valor", "fecha transaccion",
  ],
  tipo: [
    "tipo", "type", "tipo operacion", "transaction type", "operacion",
    "accion", "movimiento", "concepto",
  ],
  valor: [
    "isin", "valor", "ticker", "symbol", "security", "nombre",
    "activo", "instrumento", "producto", "titulo", "security name",
  ],
  cantidad: [
    "cantidad", "quantity", "shares", "acciones", "unidades",
    "n acciones", "numero acciones", "units", "titulos", "nominal",
  ],
  precio: [
    "precio", "price", "precio unitario", "unit price", "cotizacion",
    "importe unitario", "share price", "precio accion",
  ],
  divisa: ["divisa", "moneda", "currency", "ccy", "divisa operacion"],
  comision: [
    "comision", "comisiones", "comision eur", "fee", "fees",
    "gastos", "coste", "costes", "charges", "commission", "comision operacion",
  ],
};

function normalizarCabecera(texto) {
  texto = texto.replace(/º/g, "").replace(/ª/g, "");
  texto = texto.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");   // quita acentos (marcas combinantes)
  texto = texto.trim().toLowerCase();
  return texto.replace(/[^a-z0-9]+/g, "");
}

function sugerirMapeoJS(cabeceras) {
  const normalizadas = new Map(cabeceras.map((c) => [c, normalizarCabecera(c)]));
  const sugerencia = {};
  for (const [campo, sinonimos] of Object.entries(SINONIMOS_CAMPO)) {
    const sinonimosNormalizados = new Set(sinonimos.map(normalizarCabecera));
    const candidatos = cabeceras.filter((c) => sinonimosNormalizados.has(normalizadas.get(c)));
    if (candidatos.length === 1) sugerencia[campo] = candidatos[0];
  }
  return sugerencia;
}

// Sniffer minimalista: entre coma/punto y coma/tabulador, elige el que
// aparece el mismo número de veces en todas las líneas de la muestra (como
// csv.Sniffer, sin su heurística completa — de sobra para decidir un
// separador con el que pintar la tabla de mapeo).
function detectarSeparadorJS(texto) {
  const muestra = texto.slice(0, 4096);
  const lineas = muestra.split(/\r\n|\r|\n/).filter((l) => l.length > 0);
  let mejor = ",";
  let mejorPuntuacion = -1;
  for (const delimitador of [",", ";", "\t"]) {
    const conteos = lineas.map((l) => l.split(delimitador).length - 1);
    if (conteos.length === 0 || conteos[0] === 0) continue;
    const consistente = conteos.every((c) => c === conteos[0]);
    if (consistente && conteos[0] > mejorPuntuacion) {
      mejor = delimitador;
      mejorPuntuacion = conteos[0];
    }
  }
  return mejor;
}

// Parser de CSV con comillas (campos con el separador o saltos de línea
// dentro, comillas escapadas ""), equivalente al csv.reader de Python.
function parsearCSV(texto, delimitador) {
  if (texto.charCodeAt(0) === 0xfeff) texto = texto.slice(1);   // BOM UTF-8
  const filas = [];
  let fila = [];
  let campo = "";
  let dentroComillas = false;
  let huboContenido = false;

  for (let i = 0; i < texto.length; i++) {
    const c = texto[i];
    if (dentroComillas) {
      if (c === '"') {
        if (texto[i + 1] === '"') { campo += '"'; i++; } else { dentroComillas = false; }
      } else {
        campo += c;
      }
      continue;
    }
    if (c === '"') { dentroComillas = true; huboContenido = true; continue; }
    if (c === delimitador) { fila.push(campo); campo = ""; huboContenido = true; continue; }
    if (c === "\r") continue;
    if (c === "\n") {
      fila.push(campo);
      filas.push(fila);
      fila = [];
      campo = "";
      huboContenido = false;
      continue;
    }
    campo += c;
    huboContenido = true;
  }
  if (huboContenido || campo.length > 0) { fila.push(campo); filas.push(fila); }
  return filas;
}

// Igual que lector_csv.detectar_csv(contenido=...): [cabeceras, filas_muestra, separador].
function detectarCsvJS(contenido, numFilasMuestra = 5) {
  const separador = detectarSeparadorJS(contenido);
  const filas = parsearCSV(contenido, separador);
  if (filas.length === 0) throw new Error("El fichero está vacío");
  const cabeceras = filas[0].map((c) => c.trim());
  const filasMuestra = filas.slice(1, 1 + numFilasMuestra);
  return [cabeceras, filasMuestra, separador];
}

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

const CLAVE_LOCALSTORAGE_PRESETS = "fifo-renta:presets_broker";
const PREFIJO_LOCALSTORAGE_MAPEO = "fifo-renta:mapeo:";

// Orquestador para varios ficheros del mismo ejercicio (Trade Republic, p.ej.,
// separa el informe por el periodo con IBAN alemán y el periodo con IBAN
// español). NO modifica motor_web.py ni ningún otro fichero del motor
// probado con tests: es una capa aparte, ejecutada solo en Pyodide, que
// reutiliza exactamente las mismas funciones ya testeadas para juntar las
// operaciones de todos los ficheros POR VALOR antes de pasarlas por el
// FIFO (una compra en un fichero y su venta en otro tienen que liquidarse
// juntas; tratar cada fichero por separado rompería el FIFO real).
const CODIGO_ORQUESTADOR_MULTIARCHIVO = `
import contextlib
import io
from decimal import Decimal

import lector_csv
import motor_web
from calculadora import _fecha_para_ordenar, calcular_desglose


def _dinero_multi(valor):
    return str(valor.quantize(Decimal("0.01")))


def _anio(fecha_str):
    # lector_csv.leer_operaciones siempre da fechas "DD/MM/AAAA" (4 digitos
    # de año), asi que comparar estas cadenas como texto ordena igual que
    # comparar los años como numeros.
    return fecha_str.split("/")[-1]


def _anios_de_dividendos(dividendos_por_valor):
    return {_anio(d["fecha"]) for divs in dividendos_por_valor.values() for d in divs}


def _participaciones_por_venta(operaciones, detalle_ventas):
    # calcular_desglose no devuelve las participaciones de cada venta (solo
    # el resultado en euros), pero cada operacion de venta original ya trae
    # su propio "acciones". Como calcular_desglose ordena las operaciones
    # con este mismo criterio (_fecha_para_ordenar, orden estable) antes de
    # recorrerlas, las ventas ordenadas aqui caen en el mismo orden que las
    # filas de detalle_ventas: se pueden emparejar por posicion.
    ventas_ordenadas = sorted(
        (op for op in operaciones if op["tipo"] == "venta"),
        key=lambda op: _fecha_para_ordenar(op["fecha"]),
    )
    return [str(venta["acciones"]) for venta in ventas_ordenadas]


def _lotes_pendientes_al_cierre(operaciones, anio_cierre):
    # Cuantas acciones de cada compra (en el mismo orden cronologico que usa
    # el FIFO real) seguian sin vender al cierre de "anio_cierre", SIN volver
    # a ejecutar calcular_desglose: el FIFO consume siempre las compras mas
    # antiguas primero, asi que basta con saber cuantas acciones en total se
    # habian vendido ya a esa fecha (de cualquier año hasta ese cierre,
    # inclusive) e ir descontando esa cantidad de las compras en orden. La
    # regla de los 2 meses no cambia esto en ningun caso: solo ajusta el
    # COSTE fiscal de un lote, nunca cuantas acciones quedan ni en que orden
    # se consumen, asi que este calculo de cantidades es exacto aunque no
    # repita la parte monetaria (que ya ha calculado calcular_desglose una
    # unica vez, mas arriba).
    compras = sorted(
        (op for op in operaciones if op["tipo"] == "compra" and _anio(op["fecha"]) <= anio_cierre),
        key=lambda op: _fecha_para_ordenar(op["fecha"]),
    )
    vendido_hasta_cierre = sum(
        (Decimal(str(op["acciones"])) for op in operaciones
         if op["tipo"] == "venta" and _anio(op["fecha"]) <= anio_cierre),
        Decimal("0"),
    )

    pendientes = []
    restante = vendido_hasta_cierre
    for compra in compras:
        acciones = Decimal(str(compra["acciones"]))
        consumidas = min(acciones, restante)
        quedan = acciones - consumidas
        restante -= consumidas
        if quedan > 0:
            pendientes.append({"fecha": compra["fecha"], "acciones": str(quedan)})
    return pendientes


def _resumen_dividendos_anio(dividendos_por_valor, anio):
    bruto_total = Decimal("0")
    retencion_total = Decimal("0")
    por_valor = {}
    for valor, dividendos in dividendos_por_valor.items():
        del_anio = [d for d in dividendos if _anio(d["fecha"]) == anio]
        if not del_anio:
            continue
        bruto_valor = sum((d["bruto"] for d in del_anio), Decimal("0"))
        retencion_valor = sum((d["retencion"] for d in del_anio), Decimal("0"))
        por_valor[valor] = {"bruto": _dinero_multi(bruto_valor), "retencion": _dinero_multi(retencion_valor)}
        bruto_total += bruto_valor
        retencion_total += retencion_valor
    return {
        "bruto_total": _dinero_multi(bruto_total),
        "retencion_total": _dinero_multi(retencion_total),
        "a_declarar_total": _dinero_multi(bruto_total - retencion_total),
        "por_valor": por_valor,
    }


def procesar_csvs_multi(ficheros, mapeo, tipos=None, comision_en_divisa_operacion=False):
    resultado = {
        "frescura_bce": motor_web.info_frescura_bce(),
        "error_lectura": None,
        "avisos_lectura": [],
        "completo": True,
        "motivo": None,
        "valores_con_error": {},
        "fifo_desde": None,
        "anios_disponibles": [],
        "anio_por_defecto": None,
        "ejercicios": {},
    }

    varios = len(ficheros) > 1
    operaciones_por_valor_total = {}
    dividendos_por_valor_total = {}
    avisos_total = []

    for nombre, contenido in ficheros:
        try:
            ops, divs, avisos = lector_csv.leer_operaciones(
                contenido=contenido, mapeo=mapeo, tipos=tipos,
                comision_en_divisa_operacion=comision_en_divisa_operacion,
            )
        except lector_csv.ErrorLectorCSV as error:
            mensaje = f"{nombre}: {error}" if varios else str(error)
            resultado["error_lectura"] = mensaje
            resultado["completo"] = False
            resultado["motivo"] = mensaje
            return resultado

        prefijo = f"{nombre} - " if varios else ""
        avisos_total.extend(f"{prefijo}{aviso}" for aviso in avisos)

        for valor, ops_valor in ops.items():
            operaciones_por_valor_total.setdefault(valor, []).extend(ops_valor)
        for valor, divs_valor in divs.items():
            dividendos_por_valor_total.setdefault(valor, []).extend(divs_valor)

    resultado["avisos_lectura"] = avisos_total

    if not operaciones_por_valor_total:
        resultado["completo"] = False
        resultado["motivo"] = (
            "No se ha podido leer ninguna compra o venta de este fichero "
            f"({len(avisos_total)} fila(s) ignorada(s)); revisa el mapeo de columnas."
            if avisos_total else
            "El fichero no tiene ninguna compra o venta que calcular."
        )
        anios_divs = sorted(_anios_de_dividendos(dividendos_por_valor_total))
        resultado["anios_disponibles"] = anios_divs
        for anio in anios_divs:
            resultado["ejercicios"][anio] = {
                "totales": {"bruto": None, "bloqueado": None, "declarable": None},
                "dividendos": _resumen_dividendos_anio(dividendos_por_valor_total, anio),
                "valores": {},
            }
        return resultado

    # La compra mas antigua de TODO el fichero (cualquier valor): el FIFO
    # arranca aqui, aunque el usuario solo quiera ver un ejercicio posterior
    # -- hace falta el historico completo para saber el coste real de lo
    # vendido este año, no solo las operaciones de este año.
    todas_las_compras = [
        op for ops in operaciones_por_valor_total.values() for op in ops if op["tipo"] == "compra"
    ]
    if todas_las_compras:
        resultado["fifo_desde"] = min(
            (op["fecha"] for op in todas_las_compras), key=_fecha_para_ordenar
        )

    resultados_por_valor = {}
    algun_error = False

    for valor, operaciones in operaciones_por_valor_total.items():
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ganancia, lotes_finales, detalle_ventas = calcular_desglose(operaciones)
        except Exception as error:
            algun_error = True
            resultado["valores_con_error"][valor] = str(error)
            continue

        resultados_por_valor[valor] = {
            "operaciones": operaciones,
            "detalle_ventas": detalle_ventas,
            "participaciones": _participaciones_por_venta(operaciones, detalle_ventas),
        }

    resultado["completo"] = not algun_error
    if algun_error:
        n_errores = len(resultado["valores_con_error"])
        resultado["motivo"] = f"{n_errores} valor(es) no se han podido calcular (mira el detalle de cada uno)."
        return resultado

    # Un año entra en la lista si hay algo que declarar en el (una venta o
    # un dividendo): un año en el que solo hay compras no genera nada que
    # declarar todavia, asi que no se le dedica un ejercicio propio.
    anios = _anios_de_dividendos(dividendos_por_valor_total)
    for r in resultados_por_valor.values():
        anios.update(_anio(fila["fecha"]) for fila in r["detalle_ventas"])
    anios = sorted(anios)
    resultado["anios_disponibles"] = anios

    anios_con_ventas = sorted({
        _anio(fila["fecha"])
        for r in resultados_por_valor.values()
        for fila in r["detalle_ventas"]
    })
    resultado["anio_por_defecto"] = anios_con_ventas[-1] if anios_con_ventas else (anios[-1] if anios else None)

    for anio in anios:
        ganancia_anio = Decimal("0")
        bruto_anio = Decimal("0")
        bloqueado_anio = Decimal("0")
        valores_anio = {}

        for valor, r in resultados_por_valor.items():
            desglose_anio = [fila for fila in r["detalle_ventas"] if _anio(fila["fecha"]) == anio]
            participaciones_anio = [
                p for fila, p in zip(r["detalle_ventas"], r["participaciones"])
                if _anio(fila["fecha"]) == anio
            ]
            lotes_pendientes = _lotes_pendientes_al_cierre(r["operaciones"], anio)

            if not desglose_anio and not lotes_pendientes:
                continue

            bruto_valor = sum((fila["resultado_bruto"] for fila in desglose_anio), Decimal("0"))
            bloqueado_valor = sum((fila["bloqueado"] for fila in desglose_anio), Decimal("0"))
            ganancia_valor = sum((fila["resultado_declarado"] for fila in desglose_anio), Decimal("0"))
            participaciones_total_valor = sum((Decimal(p) for p in participaciones_anio), Decimal("0"))

            ganancia_anio += ganancia_valor
            bruto_anio += bruto_valor
            bloqueado_anio += bloqueado_valor

            valores_anio[valor] = {
                "ganancia": _dinero_multi(ganancia_valor),
                "bruto_total": _dinero_multi(bruto_valor),
                "bloqueado_total": _dinero_multi(bloqueado_valor),
                "participaciones_total": str(participaciones_total_valor),
                "desglose": [
                    {
                        "fecha": fila["fecha"],
                        "participaciones": participaciones_anio[idx],
                        "resultado_bruto": _dinero_multi(fila["resultado_bruto"]),
                        "bloqueado": _dinero_multi(fila["bloqueado"]),
                        "resultado_declarado": _dinero_multi(fila["resultado_declarado"]),
                    }
                    for idx, fila in enumerate(desglose_anio)
                ],
                "lotes_pendientes": lotes_pendientes,
            }

        resultado["ejercicios"][anio] = {
            "totales": {
                "bruto": _dinero_multi(bruto_anio),
                "bloqueado": _dinero_multi(bloqueado_anio),
                "declarable": _dinero_multi(ganancia_anio),
            },
            "dividendos": _resumen_dividendos_anio(dividendos_por_valor_total, anio),
            "valores": valores_anio,
        }

    return resultado
`;

let pyodide = null;
let lectorCsv = null;
let motorWeb = null;
let procesarCsvsMulti = null;
let ficherosActuales = [];   // [{nombre, tamano, texto}, ...]
let cabecerasActuales = [];
let ultimoResultado = null;
let anioSeleccionado = null;   // ejercicio fiscal que se está mostrando ahora mismo
let nombrePresetActivo = null;   // null = mapeo a mano, sin preset asociado

const el = (id) => document.getElementById(id);

function mostrar(elemento) { elemento.classList.remove("oculto"); }
function ocultar(elemento) { elemento.classList.add("oculto"); }

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto;
  return div.innerHTML;
}

// "-40.80" -> "-40,80 €" ; "1234.5" -> "1.234,50 €"
// El signo menos es el tipográfico real (− U+2212), no el guion del
// teclado (-): un guion es un signo de puntuación, no un operador
// matemático, y en una herramienta de cifras la diferencia se nota.
const SIGNO_MENOS = "−";

function formatoDinero(cadena) {
  const negativo = cadena.trim().startsWith("-");
  const [entero, decimales] = cadena.replace("-", "").split(".");
  const enteroConMiles = entero.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${negativo ? SIGNO_MENOS : ""}${enteroConMiles},${decimales || "00"} €`;
}

// Las cantidades de acciones (p.ej. "0.30") vienen de Python con el punto
// decimal de Decimal: en la página TODO se lee con coma, sin excepción, y
// con un mínimo de 2 decimales ("3" -> "3,00", "1.5" -> "1,50") para que
// la columna cuadre igual que el resto de cifras tabulares.
function formatoCantidad(cadena) {
  const negativo = cadena.trim().startsWith("-");
  const [entero, decimalesBrutos] = cadena.replace("-", "").split(".");
  const enteroConMiles = entero.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const decimales = (decimalesBrutos || "").padEnd(2, "0");
  return `${negativo ? SIGNO_MENOS : ""}${enteroConMiles},${decimales}`;
}

// Clase de color de una cifra según su signo: verde en ganancias, rojo en
// pérdidas (columnas Bruto y Declarado de la tabla de ventas).
function claseSigno(cadena) {
  const valor = parseFloat(cadena);
  if (valor > 0) return "g";
  if (valor < 0) return "r";
  return "";
}

// --- Progreso de los 3 pasos ---------------------------------------------

const ORDEN_PASO = { carga: 1, mapeo: 2, resultado: 3 };

function actualizarPasos(pasoActivo) {
  mostrar(el("pasos"));
  for (const [nombre, numero] of Object.entries(ORDEN_PASO)) {
    const paso = el(`paso-nav-${numero}`);
    const marcador = paso.querySelector("i");
    paso.classList.remove("done", "on");
    if (nombre === pasoActivo) {
      paso.classList.add("on");
      marcador.textContent = String(numero);
    } else if (numero < ORDEN_PASO[pasoActivo]) {
      paso.classList.add("done");
      marcador.textContent = "✓";
    } else {
      marcador.textContent = String(numero);
    }
  }
}

// --- Arranque -----------------------------------------------------------
//
// El paso 1 (soltar el CSV, revisar y completar el mapeo de columnas) no
// depende de Python en absoluto — ver detectarCsvJS/sugerirMapeoJS más
// arriba — así que se muestra AL INSTANTE, sin esperar a Pyodide. Mientras
// el usuario suelta el fichero y repasa el mapeo (20-40 s normalmente),
// cargarMotorEnSegundoPlano() sigue cargando Python de fondo: el usuario
// nunca mira una pantalla de carga sin poder hacer nada, la espera ocurre
// mientras trabaja. Si llega a "Calcular" antes de que el motor esté listo
// (fichero muy simple, mapeo ya recordado), calcular() deja el clic en
// cola y se ejecuta solo en cuanto el motor termine — ver más abajo.
//
// cargarMotor() y cargarTiposBCE() siguen siendo dos etapas independientes,
// cada una con su propio estado y su propio mensaje de error:
//   1. cargarMotor(): Pyodide + los .py del motor. Si esto falla, no se
//      puede calcular nada (ni EUR ni nada) — se avisa en el propio aviso
//      pequeño del paso 1.
//   2. cargarTiposBCE(): el fichero de tipos de cambio. Si esto falla,
//      el motor sigue funcionando perfectamente para operaciones en EUR
//      (que no necesitan el BCE); solo las de otras divisas fallarán, y
//      lo harán con su propio error explicado en el desglose de ese
//      valor — no aquí arriba, y no como si el motor no hubiera cargado.

let motorListo = false;
let calculoPendiente = false;

function iniciar() {
  mostrar(el("zona-carga"));
  actualizarPasos("carga");
  configurarZonaCarga();
  configurarBotones();

  cargarMotorEnSegundoPlano();
}

async function cargarMotorEnSegundoPlano() {
  const ok = await cargarMotor();
  if (!ok) return;   // cargarMotor() ya ha puesto el error en el aviso del paso 1

  motorListo = true;
  ocultar(el("estado-motor"));
  pintarSelectorPresets();   // hasta ahora no se podía: necesita lectorCsv (Python)
  el("boton-guardar-preset").disabled = false;
  actualizarAvisoComisionDivisa();   // igual: si ya hay un fichero cargado, ahora sí se puede comprobar

  if (calculoPendiente) {
    calculoPendiente = false;
    el("boton-calcular").textContent = "Calcular";
    const mapeo = leerMapeoDelFormulario();
    if (mapeo) {
      await calcularConMotor(mapeo, obtenerComisionEnDivisaOperacion());
    } else {
      el("boton-calcular").disabled = false;
    }
  }

  await cargarTiposBCE();
}

// Descarga "urls" en paralelo llevando la cuenta real de bytes recibidos
// frente al Content-Length de cada una, y llama a actualizarPorcentaje con
// el % agregado. No es una barra decorativa: son los mismos bytes que
// loadPyodide() necesita, así que al terminar esto ya están en la caché
// HTTP del navegador (o en la del service worker) y la llamada real a
// loadPyodide() de más abajo los reaprovecha en vez de volver a bajarlos —
// por eso conviene hacer esta precarga ANTES, no en paralelo con ella.
// Los <link rel="preload"> del <head> ya habrán adelantado el arranque de
// estas descargas antes de que este script llegue a ejecutarse; fetch()
// aquí se limita a observar (y a terminar de traer) esas mismas descargas.
async function precargarConProgreso(urls, actualizarPorcentaje) {
  const totales = new Array(urls.length).fill(0);
  const recibidos = new Array(urls.length).fill(0);

  function reportarProgreso() {
    const total = totales.reduce((a, b) => a + b, 0);
    if (total === 0) return;
    const hecho = recibidos.reduce((a, b) => a + b, 0);
    actualizarPorcentaje(Math.min(99, Math.round((hecho / total) * 100)));
  }

  await Promise.all(urls.map(async (url, indice) => {
    let respuesta;
    try {
      respuesta = await fetch(url);
    } catch (error) {
      return;   // sin red hasta este recurso: loadPyodide() hará su propio intento (y dará su propio error) después
    }
    if (!respuesta.ok || !respuesta.body) return;

    totales[indice] = Number(respuesta.headers.get("content-length")) || 0;
    reportarProgreso();

    const lector = respuesta.body.getReader();
    for (;;) {
      const { done, value } = await lector.read();
      if (done) break;
      recibidos[indice] += value.length;
      reportarProgreso();
    }
  }));
}

async function cargarMotor() {
  try {
    const barra = el("progreso-motor-barra");
    const urlsPyodide = PYODIDE_ARCHIVOS_GRANDES.map((nombre) => `${PYODIDE_INDEX_URL}${nombre}`);
    await precargarConProgreso(urlsPyodide, (porcentaje) => {
      barra.style.width = `${porcentaje}%`;
    });

    pyodide = await loadPyodide();
    barra.style.width = "100%";

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

    pyodide.runPython(CODIGO_ORQUESTADOR_MULTIARCHIVO);
    procesarCsvsMulti = pyodide.globals.get("procesar_csvs_multi");

    return true;
  } catch (error) {
    console.error("Fallo cargando el motor de cálculo (Pyodide/Python):", error);
    el("estado-motor-texto").innerHTML =
      "No se ha podido cargar el motor de cálculo (Python). Comprueba tu conexión y recarga la página. " +
      `<br><span class="texto-pequeno">${escaparHtml(error.message || String(error))}</span>`;
    el("estado-motor").classList.add("aviso-error");
    return false;
  }
}

async function cargarTiposBCE() {
  try {
    pyodide.FS.mkdir("cache");
    const respuesta = await fetch(`motor/${RUTA_CACHE_BCE}`);
    if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status} al pedir motor/${RUTA_CACHE_BCE}`);
    pyodide.FS.writeFile(RUTA_CACHE_BCE, await respuesta.text());
  } catch (error) {
    console.error("Fallo cargando los tipos de cambio del BCE:", error);

    const banner = el("bce-error");
    banner.querySelector(".wrap").innerHTML =
      "No se han podido cargar los tipos de cambio oficiales del BCE " +
      "(el motor de cálculo sí ha cargado bien). Puedes calcular operaciones en EUR con normalidad; " +
      "las de otras divisas no se podrán calcular hasta que esto se resuelva. " +
      `<span class="mono"> ${escaparHtml(error.message || String(error))}</span>`;
    mostrar(banner);
  }
}

// --- Presets: persistencia en localStorage --------------------------------
//
// Pyodide corre en un filesystem en memoria (MEMFS) que se pierde al
// recargar la página. Para que "guardar un preset" sea de verdad
// duradero, el contenido de presets_broker.json se copia a localStorage
// cada vez que cambia, y se restaura desde ahí al arrancar.

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

// Borrar un preset no es una operación que exponga lector_csv.py (solo
// guardar/cargar/listar): en vez de tocar el motor para añadir un
// "borrar_preset", se manipula aquí mismo el JSON ya cargado en el
// filesystem de Pyodide, con las mismas funciones de fichero que ya usa
// guardar_preset por debajo (leer, modificar, escribir).
function eliminarPreset(nombre) {
  const contenido = pyodide.FS.readFile("presets_broker.json", { encoding: "utf8" });
  const presets = JSON.parse(contenido);
  delete presets[nombre];
  pyodide.FS.writeFile("presets_broker.json", JSON.stringify(presets, null, 2));
  guardarPresetsEnLocalStorage();
}

// --- Recordar el último mapeo usado para unas mismas cabeceras -----------

function claveMapeoLocal(cabeceras) {
  const huella = [...cabeceras].map((c) => c.trim().toLowerCase()).sort().join("||");
  return PREFIJO_LOCALSTORAGE_MAPEO + huella;
}

function recordarMapeoLocal(cabeceras, mapeo, comisionEnDivisaOperacion) {
  try {
    localStorage.setItem(claveMapeoLocal(cabeceras), JSON.stringify({ mapeo, comisionEnDivisaOperacion }));
  } catch (error) {
    console.warn("No se ha podido recordar el mapeo en este navegador:", error);
  }
}

function mapeoRecordadoLocal(cabeceras) {
  try {
    const guardado = localStorage.getItem(claveMapeoLocal(cabeceras));
    if (!guardado) return null;
    const datos = JSON.parse(guardado);
    // Formato antiguo (de antes de recordar la divisa de la comision): el
    // valor guardado era el mapeo a secas, sin envoltorio.
    return datos && datos.mapeo ? datos : { mapeo: datos, comisionEnDivisaOperacion: false };
  } catch (error) {
    console.warn("No se ha podido leer el mapeo recordado de este navegador:", error);
    return null;
  }
}

// --- Paso 1: elegir uno o varios CSV --------------------------------------

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
    const csv = [...evento.dataTransfer.files].filter((f) => f.name.toLowerCase().endsWith(".csv"));
    if (csv.length) cargarFicheros(csv);
  });

  inputFichero.addEventListener("change", (evento) => {
    if (evento.target.files.length) cargarFicheros(evento.target.files);
    inputFichero.value = "";   // permite volver a elegir el mismo fichero si se ha quitado
  });
}

function leerFicheroComoTexto(fichero) {
  return new Promise((resolve, reject) => {
    const lector = new FileReader();
    lector.onload = () => resolve(lector.result);
    lector.onerror = () => reject(new Error(`No se ha podido leer "${fichero.name}"`));
    lector.readAsText(fichero);
  });
}

async function cargarFicheros(ficheros) {
  ocultarError();
  ocultar(el("zona-resultado"));

  try {
    ficherosActuales = await Promise.all([...ficheros].map(async (fichero) => ({
      nombre: fichero.name,
      tamano: fichero.size,
      texto: await leerFicheroComoTexto(fichero),
    })));
  } catch (error) {
    mostrarError(error.message || "No se ha podido leer el fichero. Prueba a exportarlo de nuevo desde tu bróker.");
    return;
  }

  pintarListaFicheros();
  detectarYPreparearMapeo();
}

function pintarListaFicheros() {
  const lista = el("lista-ficheros");
  lista.innerHTML = "";

  if (ficherosActuales.length === 0) {
    ocultar(lista);
    ocultar(el("nota-sin-subida"));
    return;
  }

  ficherosActuales.forEach((fichero, indice) => {
    const fila = document.createElement("div");
    fila.className = "filerow";
    fila.innerHTML = `
      <span class="nm">${escaparHtml(fichero.nombre)} <span class="sz">· ${fichero.tamano.toLocaleString("es-ES")} bytes</span></span>
      <a class="quitar-fichero" data-indice="${indice}">Quitar</a>
    `;
    lista.appendChild(fila);
  });

  lista.querySelectorAll(".quitar-fichero").forEach((boton) => {
    boton.addEventListener("click", () => {
      ficherosActuales.splice(Number(boton.dataset.indice), 1);
      pintarListaFicheros();
      if (ficherosActuales.length === 0) {
        ocultar(el("zona-mapeo"));
        ocultar(el("boton-calcular"));
        ocultar(el("zona-resultado"));
        ocultar(el("aviso-comision-divisa"));
      } else {
        detectarYPreparearMapeo();
      }
    });
  });

  mostrar(lista);
  mostrar(el("nota-sin-subida"));
}

// --- Paso 2: detectar cabeceras y mapear columnas -------------------------

function detectarYPreparearMapeo() {
  const primerFichero = ficherosActuales[0];
  let cabeceras, filasMuestra;
  try {
    [cabeceras, filasMuestra] = detectarCsvJS(primerFichero.texto);
  } catch (error) {
    mostrarError(`No se ha podido leer el CSV: ${error.message || error}`);
    return;
  }

  cabecerasActuales = cabeceras;

  pintarTablaMapeo(cabeceras);
  pintarTablaMuestra(cabeceras, filasMuestra);
  pintarSelectorPresets();
  aplicarAutoDeteccion(cabeceras);

  mostrar(el("zona-mapeo"));
  mostrar(el("boton-calcular"));
  actualizarPasos("mapeo");
}

// Encuentra el mapeo de columnas sin que el usuario tenga que elegirlas a
// mano, probando por este orden (el primero que encaja gana):
//   1. Un mapeo recordado de un fichero anterior con las mismas cabeceras.
//   2. Un preset guardado (con nombre) cuyas columnas existan todas aquí.
//   3. Adivinar cada campo por el nombre de su cabecera (sugerir_mapeo),
//      tolerando mayúsculas/acentos/sinónimos habituales. Puede acertar
//      solo alguno de los campos: el resto se deja para elegir a mano.
// Si con esto quedan todos los campos obligatorios cubiertos, el paso 2
// se deja PLEGADO (el usuario solo lo abre si quiere revisar algo); si
// falta alguno, se abre solo y se señala qué falta.
function aplicarAutoDeteccion(cabeceras) {
  const avisoPreset = el("preset-sugerido");
  const avisoAuto = el("mapeo-auto-detectado");
  ocultar(avisoPreset);
  ocultar(avisoAuto);
  nombrePresetActivo = null;

  const recordado = mapeoRecordadoLocal(cabeceras);
  if (recordado) {
    aplicarMapeoAlFormulario(recordado.mapeo, recordado.comisionEnDivisaOperacion);
    avisoAuto.textContent = "Hemos recordado el mapeo que usaste la última vez con estas mismas cabeceras. Puedes cambiar cualquier columna si no es correcto.";
    mostrar(avisoAuto);
  } else {
    // Los presets guardados viven en Python (lectorCsv): si el motor aún no
    // ha terminado de cargar, este paso se salta sin más y se pasa directo
    // al autodetectado por sinónimos (JS, siempre disponible) — nunca se
    // espera a Pyodide para pintar el mapeo.
    const cabecerasSet = new Set(cabeceras);
    const presetQueEncaja = motorListo
      ? obtenerPresets().find((preset) => Object.values(preset.mapeo).every((columna) => cabecerasSet.has(columna)))
      : null;
    if (presetQueEncaja) {
      aplicarMapeoAlFormulario(presetQueEncaja.mapeo, presetQueEncaja.comisionEnDivisaOperacion);
      nombrePresetActivo = presetQueEncaja.nombre;
      avisoPreset.textContent = `Este fichero encaja con el preset "${presetQueEncaja.nombre}": lo hemos preseleccionado. Puedes cambiar cualquier columna si no es correcto.`;
      mostrar(avisoPreset);
      setTimeout(() => { el("select-preset").value = presetQueEncaja.nombre; }, 0);
    } else {
      const sugerencia = sugerirMapeoJS(cabeceras);

      const nDetectados = Object.keys(sugerencia).length;
      if (nDetectados > 0) {
        aplicarMapeoAlFormulario(sugerencia);
        const todosLosCampos = [...CAMPOS_OBLIGATORIOS, ...CAMPOS_OPCIONALES];
        const faltanObligatorios = CAMPOS_OBLIGATORIOS.filter((c) => !sugerencia[c]);
        const faltanOpcionales = CAMPOS_OPCIONALES.filter((c) => !sugerencia[c]);

        let mensaje = `Hemos detectado automáticamente ${nDetectados} de ${todosLosCampos.length} campos, por el nombre de su cabecera.`;
        if (faltanObligatorios.length > 0) {
          mensaje += ` Falta indicar: ${faltanObligatorios.map((c) => ETIQUETAS_CAMPO[c]).join(", ")}.`;
        }
        if (faltanOpcionales.length > 0) {
          const verbo = faltanOpcionales.length === 1 ? "es opcional" : "son opcionales";
          mensaje += ` ${faltanOpcionales.map((c) => ETIQUETAS_CAMPO[c].split(" (")[0]).join(", ")} ${verbo}: puedes dejarlo sin indicar si tu fichero no lo trae.`;
        }
        avisoAuto.textContent = mensaje;
        mostrar(avisoAuto);
      }
    }
  }

  actualizarEstadoMapeo();
  actualizarResumenMapeoTexto();
  actualizarAvisoComisionDivisa();
  actualizarPresetActivo();

  // Solo la deteccion inicial decide si el paso se abre o se pliega; una
  // vez el usuario lo ha tocado, que se quede como el navegador lo deje.
  const faltanObligatorios = CAMPOS_OBLIGATORIOS.some((campo) => !el(`mapeo-${campo}`).value);
  el("zona-mapeo").open = faltanObligatorios;
}

function pintarTablaMapeo(cabeceras) {
  const cuerpo = el("tabla-mapeo").querySelector("tbody");
  cuerpo.innerHTML = "";

  const todosLosCampos = [...CAMPOS_OBLIGATORIOS, ...CAMPOS_OPCIONALES];
  for (const campo of todosLosCampos) {
    const fila = document.createElement("tr");

    const celdaEtiqueta = document.createElement("td");
    celdaEtiqueta.innerHTML = ETIQUETAS_CAMPO[campo] +
      (CAMPOS_OBLIGATORIOS.includes(campo) ? ' <span class="campo-obligatorio-marca">*</span>' : "");
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

    select.addEventListener("change", () => {
      nombrePresetActivo = null;   // tocar cualquier columna a mano desasocia el preset
      el("select-preset").value = "";
      actualizarEstadoMapeo();
      actualizarResumenMapeoTexto();
      actualizarAvisoComisionDivisa();
      actualizarPresetActivo();
    });
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
    const [mapeo, , comisionEnDivisaOperacion] = parPy.toJs({ dict_converter: Object.fromEntries });
    parPy.destroy();
    return { nombre, mapeo, comisionEnDivisaOperacion: !!comisionEnDivisaOperacion };
  });
}

// comisionEnDivisaOperacion es opcional: si no se da (p.ej. al aplicar una
// sugerencia por sinonimos, que no tiene opinion sobre esto), el
// desplegable se deja tal como estuviera.
function aplicarMapeoAlFormulario(mapeo, comisionEnDivisaOperacion) {
  for (const campo of [...CAMPOS_OBLIGATORIOS, ...CAMPOS_OPCIONALES]) {
    const select = el(`mapeo-${campo}`);
    const columna = mapeo[campo] || "";
    select.value = cabecerasActuales.includes(columna) ? columna : "";
  }
  if (comisionEnDivisaOperacion !== undefined) {
    el("select-comision-divisa").value = comisionEnDivisaOperacion ? "divisa_operacion" : "eur";
  }
}

function obtenerComisionEnDivisaOperacion() {
  return el("select-comision-divisa").value === "divisa_operacion";
}

// Aviso informativo (nunca bloquea) cuando el fichero tiene operaciones en
// otra divisa con comision distinta de cero: hay que confirmar con el
// broker en que moneda la cobra antes de fiarse de cualquiera de las dos
// opciones del desplegable.
function actualizarAvisoComisionDivisa() {
  const aviso = el("aviso-comision-divisa");
  const valores = valoresDelFormulario();
  if (!motorListo || !valores.comision || ficherosActuales.length === 0) { ocultar(aviso); return; }

  const mapeoPy = pyodide.toPy(new Map(Object.entries(valores)));
  let hayRiesgo = false;
  try {
    for (const fichero of ficherosActuales) {
      hayRiesgo = lectorCsv.hay_operaciones_en_otra_divisa_con_comision.callKwargs({
        mapeo: mapeoPy, contenido: fichero.texto,
      });
      if (hayRiesgo) break;
    }
  } finally {
    mapeoPy.destroy();
  }

  if (hayRiesgo) {
    aviso.textContent = "Tu fichero tiene operaciones en otra divisa con comisión. Comprueba en qué moneda la cobra tu bróker.";
    mostrar(aviso);
  } else {
    ocultar(aviso);
  }
}

function pintarSelectorPresets() {
  const select = el("select-preset");
  select.innerHTML = '<option value="">— Elegir columnas a mano —</option>';
  if (!motorListo) return;   // los presets viven en Python; se repinta en cuanto el motor esté listo

  for (const preset of obtenerPresets()) {
    const opcion = document.createElement("option");
    opcion.value = preset.nombre;
    opcion.textContent = preset.nombre;
    select.appendChild(opcion);
  }

  select.onchange = () => {
    if (!select.value) {
      nombrePresetActivo = null;
      actualizarPresetActivo();
      return;
    }
    const preset = obtenerPresets().find((p) => p.nombre === select.value);
    if (preset) {
      aplicarMapeoAlFormulario(preset.mapeo, preset.comisionEnDivisaOperacion);
      nombrePresetActivo = preset.nombre;
      actualizarEstadoMapeo();
      actualizarResumenMapeoTexto();
      actualizarAvisoComisionDivisa();
      actualizarPresetActivo();
    }
  };
}

// Deja claro cuál de los presets guardados está aplicado ahora mismo (si
// alguno), con un botón para borrarlo sin tener que ir a buscarlo en el
// desplegable. Tocar cualquier columna a mano lo desasocia (ver
// pintarTablaMapeo), para no dar a entender que sigue vigente un preset
// que ya no coincide con lo que hay en el formulario.
function actualizarPresetActivo() {
  const contenedor = el("preset-activo");
  if (!nombrePresetActivo) { ocultar(contenedor); return; }

  contenedor.innerHTML = `Preset activo: <b>${escaparHtml(nombrePresetActivo)}</b> · `;
  const boton = document.createElement("button");
  boton.type = "button";
  boton.className = "boton-enlace";
  boton.textContent = "Borrar este preset";
  boton.addEventListener("click", () => {
    if (!confirm(`¿Borrar el preset "${nombrePresetActivo}"? Esto no afecta al mapeo que tienes puesto ahora, solo lo quita de la lista de presets guardados.`)) return;
    eliminarPreset(nombrePresetActivo);
    nombrePresetActivo = null;
    pintarSelectorPresets();
    actualizarPresetActivo();
  });
  contenedor.appendChild(boton);
  mostrar(contenedor);
}

function valoresDelFormulario() {
  const valores = {};
  for (const campo of [...CAMPOS_OBLIGATORIOS, ...CAMPOS_OPCIONALES]) {
    const valor = el(`mapeo-${campo}`).value;
    if (valor) valores[campo] = valor;
  }
  return valores;
}

function columnasDuplicadas(mapeo) {
  const porColumna = new Map();
  for (const [campo, columna] of Object.entries(mapeo)) {
    if (!porColumna.has(columna)) porColumna.set(columna, []);
    porColumna.get(columna).push(campo);
  }
  return [...porColumna.entries()].filter(([, campos]) => campos.length > 1);
}

function leerMapeoDelFormulario() {
  const valores = valoresDelFormulario();
  if (CAMPOS_OBLIGATORIOS.some((campo) => !valores[campo])) return null;
  if (columnasDuplicadas(valores).length > 0) return null;
  return valores;
}

// Valida en vivo (sin esperar a pulsar "Calcular"): columnas duplicadas o
// campos obligatorios sin cubrir bloquean el botón con un motivo claro.
function actualizarEstadoMapeo() {
  const avisoEl = el("mapeo-aviso");
  const valores = valoresDelFormulario();
  const duplicados = columnasDuplicadas(valores);
  const faltan = CAMPOS_OBLIGATORIOS.filter((campo) => !valores[campo]);

  if (duplicados.length > 0) {
    const detalle = duplicados
      .map(([columna, campos]) => `"${columna}" está asignada a la vez a ${campos.map((c) => ETIQUETAS_CAMPO[c]).join(" y ")}`)
      .join("; ");
    avisoEl.textContent = `No puedes usar la misma columna para dos campos: ${detalle}. Si tu fichero no tiene una columna propia para uno de ellos, esa operación no se puede calcular.`;
    mostrar(avisoEl);
    el("boton-calcular").disabled = true;
    return;
  }

  if (faltan.length > 0) {
    avisoEl.textContent = `Tu fichero no tiene (o no has indicado) columna para: ${faltan.map((c) => ETIQUETAS_CAMPO[c]).join(", ")}. No se puede calcular sin eso.`;
    mostrar(avisoEl);
    el("boton-calcular").disabled = true;
    return;
  }

  ocultar(avisoEl);
  el("boton-calcular").disabled = false;
}

// El circulo del resumen plegado usa un icono distinto segun el estado:
// un check solo cuando de verdad esta todo listo para calcular, un aviso
// (¡ nunca un check !) mientras falte algo obligatorio o haya un
// conflicto — un check en ese caso diria "todo bien" cuando no lo esta.
const ICONO_TICK_OK = '<svg width="9" height="7" viewBox="0 0 9 7" fill="none" aria-hidden="true"><path d="M1 3.4L3.3 5.7L8 1" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ICONO_TICK_AVISO = '<svg width="2" height="7.4" viewBox="0 0 2 7.4" fill="none" aria-hidden="true"><path d="M1 0.7V4.3" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><circle cx="1" cy="6.5" r="0.9" fill="#fff"/></svg>';

function actualizarResumenMapeoTexto() {
  const todosLosCampos = [...CAMPOS_OBLIGATORIOS, ...CAMPOS_OPCIONALES];
  const valores = valoresDelFormulario();
  const nMapeados = Object.keys(valores).length;
  const faltanObligatorios = CAMPOS_OBLIGATORIOS.filter((campo) => !valores[campo]);
  const completo = faltanObligatorios.length === 0 && columnasDuplicadas(valores).length === 0;

  const texto = el("resumen-mapeo-texto");
  const accion = el("resumen-mapeo-accion");
  const tick = el("mapeo-tick");

  if (!completo) {
    texto.textContent = faltanObligatorios.length > 0
      ? `Falta indicar: ${faltanObligatorios.map((c) => ETIQUETAS_CAMPO[c]).join(", ")}`
      : "Hay columnas repetidas";
    accion.textContent = "Completar";
    tick.classList.add("alerta");
    tick.innerHTML = ICONO_TICK_AVISO;
  } else {
    texto.textContent = `${nMapeados} de ${todosLosCampos.length} campos completados`;
    accion.textContent = "Revisar";
    tick.classList.remove("alerta");
    tick.innerHTML = ICONO_TICK_OK;
  }
}

function configurarBotones() {
  el("boton-calcular").addEventListener("click", calcular);

  el("boton-guardar-preset").addEventListener("click", () => {
    ocultarError();
    const nombre = el("input-nombre-preset").value.trim();
    if (!nombre) { mostrarError("Escribe un nombre para guardar el preset."); return; }

    const mapeo = leerMapeoDelFormulario();
    if (!mapeo) { mostrarError("Completa antes las columnas obligatorias (*) para poder guardar el preset."); return; }

    try {
      lectorCsv.guardar_preset.callKwargs({
        nombre,
        mapeo: pyodide.toPy(new Map(Object.entries(mapeo))),
        comision_en_divisa_operacion: obtenerComisionEnDivisaOperacion(),
      });
    } catch (error) {
      mostrarError(`No se ha podido guardar el preset: ${mensajeDeErrorPython(error)}`);
      return;
    }

    guardarPresetsEnLocalStorage();
    el("input-nombre-preset").value = "";
    nombrePresetActivo = nombre;
    pintarSelectorPresets();
    el("select-preset").value = nombre;
    actualizarPresetActivo();
  });

  el("boton-descargar").addEventListener("click", descargarDesgloseCSV);
}

// --- Paso 3: calcular y mostrar resultados -------------------------------
//
// Si el motor todavía está cargando (poco probable: suele tardar menos que
// revisar el mapeo, pero un fichero muy simple con el mapeo ya recordado
// puede llegar aquí antes), el clic se deja en cola: calcularConMotor() se
// ejecuta solo, automáticamente, en cuanto cargarMotorEnSegundoPlano()
// termine — el usuario no tiene que volver a pulsar nada.

function calcular() {
  ocultarError();
  const mapeo = leerMapeoDelFormulario();
  if (!mapeo) return;

  if (!motorListo) {
    calculoPendiente = true;
    el("boton-calcular").disabled = true;
    el("boton-calcular").textContent = "Preparando el motor de cálculo…";
    return;
  }

  calcularConMotor(mapeo, obtenerComisionEnDivisaOperacion());
}

function calcularConMotor(mapeo, comisionEnDivisaOperacion) {
  let resultado;
  try {
    const ficherosPy = pyodide.toPy(ficherosActuales.map((f) => [f.nombre, f.texto]));
    const mapeoPy = pyodide.toPy(new Map(Object.entries(mapeo)));
    const resultadoPy = procesarCsvsMulti.callKwargs({
      ficheros: ficherosPy, mapeo: mapeoPy, comision_en_divisa_operacion: comisionEnDivisaOperacion,
    });
    resultado = resultadoPy.toJs({ dict_converter: Object.fromEntries });
    resultadoPy.destroy();
    mapeoPy.destroy();
    ficherosPy.destroy();
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

  // El mapeo ha funcionado (aunque algún valor concreto falle después por
  // otro motivo, p.ej. tipo de cambio no disponible): lo recordamos para
  // no obligar a repetirlo si se sube otro fichero con estas cabeceras.
  recordarMapeoLocal(cabecerasActuales, mapeo, comisionEnDivisaOperacion);

  // El FIFO ya se ha calculado UNA sola vez (dentro de procesarCsvsMulti,
  // arriba) sobre todo el histórico del fichero. A partir de aquí solo se
  // elige qué ejercicio fiscal se enseña; cambiar de año no vuelve a tocar
  // Python, solo relee resultado.ejercicios[año], que ya está calculado.
  anioSeleccionado = resultado.anio_por_defecto || (resultado.anios_disponibles[0] ?? null);

  pintarResultado(resultado);
  mostrar(el("zona-resultado"));
  actualizarPasos("resultado");
  el("zona-resultado").scrollIntoView({ behavior: "smooth", block: "start" });
}

function pintarResultado(resultado) {
  pintarCabeceraEjercicio(resultado);
  pintarResultadoCard(resultado);
  pintarAvisos(resultado.avisos_lectura);
  pintarDondeVaEsto(resultado);
  pintarDescarga(resultado);
}

// Selector de ejercicio + la nota de que el FIFO arranca desde la primera
// compra del fichero. Vive fuera de resultado-card porque no cambia al
// cambiar de año (el propio selector solo tiene sentido ahí), y así el
// usuario ve primero POR QUÉ hace falta el histórico completo, antes que
// la cifra de un año concreto.
function pintarCabeceraEjercicio(resultado) {
  const contenedor = el("cabecera-ejercicio");
  const anios = resultado.anios_disponibles || [];

  if (!resultado.completo || anios.length === 0) { ocultar(contenedor); return; }

  const selector = el("selector-ejercicio");
  if (anios.length > 1) {
    selector.innerHTML = "";
    for (const anio of anios) {
      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = "pill-ejercicio" + (anio === anioSeleccionado ? " activo" : "");
      boton.textContent = anio;
      boton.addEventListener("click", () => {
        if (anio === anioSeleccionado) return;
        anioSeleccionado = anio;
        pintarResultado(ultimoResultado);   // solo relee datos ya calculados, no vuelve a llamar a Python
      });
      selector.appendChild(boton);
    }
    mostrar(el("selector-ejercicio-fila"));
  } else {
    ocultar(el("selector-ejercicio-fila"));
  }

  const nota = el("nota-fifo-historico");
  nota.innerHTML = resultado.fifo_desde
    ? `El cálculo del FIFO empieza en <b>${escaparHtml(resultado.fifo_desde)}</b>, la compra más antigua de
       este fichero, aunque sea de un ejercicio anterior al que ves aquí: hace falta el historial completo
       de compras para saber el coste real de lo que se vende cada año, no solo las operaciones de ese año.`
    : "";

  mostrar(contenedor);
}

function bloqueDividendos(dividendos) {
  if (!dividendos || Object.keys(dividendos.por_valor).length === 0) return "";
  return `
    <div>
      <p class="lbl">Dividendos</p>
      <div style="margin-top:0.6rem">
        <div class="kv"><span class="k">Bruto</span><span class="v">${formatoDinero(dividendos.bruto_total)}</span></div>
        <div class="kv"><span class="k">Retención en origen</span><span class="v">${formatoDinero(dividendos.retencion_total)}</span></div>
        <div class="kv"><span class="k">A declarar</span><span class="v fuerte">${formatoDinero(dividendos.a_declarar_total)}</span></div>
      </div>
    </div>
  `;
}

// El bloque central de toda la página: la cifra del ejercicio seleccionado,
// y justo debajo el desglose de ganancias patrimoniales y dividendos DE ESE
// AÑO, y por cada valor su tabla de ventas de ese año y lo que le queda sin
// vender al cierre de ese año.
function pintarResultadoCard(resultado) {
  const card = el("resultado-card");

  if (!resultado.completo) {
    // Nunca se muestra un importe aqui (ni "0,00 €"): si no es "completo"
    // es que no hay un total fiable que mostrar, y un numero con aspecto
    // de valido induciria a pensar que ya esta calculado.
    const errores = Object.entries(resultado.valores_con_error || {});
    const detalleErrores = errores.length
      ? `<ul class="lista-avisos" style="margin-top:0.9rem">${errores
          .map(([valor, error]) => `<li><b>${escaparHtml(valor)}</b>: ${escaparHtml(error)}</li>`)
          .join("")}</ul>`
      : "";
    card.innerHTML = `
      <div class="head">
        <p class="kind plano">Sin calcular</p>
        <p class="big chica">No se ha podido calcular</p>
        <p class="note">${escaparHtml(resultado.motivo || "Revisa los avisos y el detalle de cada valor más abajo.")}</p>
        ${detalleErrores}
      </div>
    `;
    return;
  }

  const anios = resultado.anios_disponibles || [];
  if (anios.length === 0) {
    card.innerHTML = `
      <div class="head">
        <p class="kind plano">Nada que declarar todavía</p>
        <p class="big chica">Sin ventas ni dividendos</p>
        <p class="note">Este fichero solo tiene compras: no genera nada que declarar hasta que vendas algo o cobres un dividendo.</p>
      </div>
    `;
    return;
  }

  const ejercicio = resultado.ejercicios[anioSeleccionado];
  const { bruto, bloqueado, declarable } = ejercicio.totales;
  const bloqueDivs = bloqueDividendos(ejercicio.dividendos);

  const valoresDelAnio = Object.entries(ejercicio.valores);
  const nValores = valoresDelAnio.length;
  const nVentas = valoresDelAnio.reduce((acc, [, d]) => acc + d.desglose.length, 0);
  const piezasEyebrow = [
    `Ejercicio ${anioSeleccionado}`,
    `${nValores} valor${nValores === 1 ? "" : "es"}`,
    `${nVentas} venta${nVentas === 1 ? "" : "s"}`,
  ].filter(Boolean).join(" · ");

  const negativo = declarable.trim().startsWith("-");
  const claseKind = negativo ? "perdida" : "";
  const etiquetaKind = negativo ? "Pérdida patrimonial" : "Ganancia patrimonial";

  // "Bloqueado por recompra" es siempre un importe positivo que se SUMA al
  // bruto (por eso Declarable = Bruto + Bloqueado): bloquear una perdida la
  // hace menos negativa, nunca mas positiva de lo que ya era la venta. Pero
  // enseñado a secas entre "Bruto" y "Declarable" se lee como si la
  // recompra hubiera generado dinero extra. Con un "+" explicito y la nota
  // de que es perdida diferida (no una ganancia) queda claro que no se
  // declara ya, no que haya aparecido de la nada.
  const hayBloqueado = parseFloat(bloqueado) !== 0;
  const filaBloqueado = hayBloqueado
    ? `
      <div class="kv bloqueado"><span class="k">Bloqueado por recompra</span><span class="v">+${formatoDinero(bloqueado)}</span></div>
      <p class="nota-bloqueo">Pérdida bloqueada por la regla de los 2 meses (art. 33.5.f LIRPF): no computa en este ejercicio, se traslada al coste del lote recomprado.</p>
    `
    : `<div class="kv"><span class="k">Bloqueado por recompra</span><span class="v">${formatoDinero(bloqueado)}</span></div>`;

  const bloqueGanancias = `
    <div>
      <p class="lbl">Ganancias patrimoniales</p>
      <div style="margin-top:0.6rem">
        <div class="kv"><span class="k">Bruto de las ventas</span><span class="v">${formatoDinero(bruto)}</span></div>
        ${filaBloqueado}
        <div class="kv"><span class="k">Declarable</span><span class="v fuerte">${formatoDinero(declarable)}</span></div>
      </div>
    </div>
  `;

  const secciones = valoresDelAnio.map(([valor, datos]) => htmlSeccionValor(valor, datos)).join("");

  card.innerHTML = `
    <div class="head">
      <p class="ej mono">${escaparHtml(piezasEyebrow)}</p>
      <p class="kind ${claseKind}">${etiquetaKind}</p>
      <p class="big">${formatoDinero(declarable)}</p>
      <p class="note">Suma del FIFO de todos los valores del fichero para las ventas de ${anioSeleccionado}, con la regla de los dos meses ya aplicada.</p>
    </div>
    <div class="split2">${bloqueGanancias}${bloqueDivs}</div>
    ${secciones}
  `;
}

function htmlSeccionValor(valor, datos) {
  const htmlLotes = htmlLotesPendientes(datos.lotes_pendientes);

  if (!datos.desglose.length) {
    return `
      <div class="sec">
        <h3>${escaparHtml(valor)}</h3>
        <p class="cap">Sin ventas en este ejercicio.</p>
        ${htmlLotes}
      </div>
    `;
  }

  const filas = datos.desglose.map((op) => `
    <tr>
      <td>${op.fecha}</td>
      <td>${formatoCantidad(op.participaciones)}</td>
      <td class="${claseSigno(op.resultado_bruto)}">${formatoDinero(op.resultado_bruto)}</td>
      <td class="${parseFloat(op.bloqueado) !== 0 ? "w" : ""}">${formatoDinero(op.bloqueado)}</td>
      <td class="${claseSigno(op.resultado_declarado)}">${formatoDinero(op.resultado_declarado)}</td>
    </tr>
  `).join("");

  return `
    <div class="sec">
      <h3>Desglose por venta</h3>
      <p class="cap">${escaparHtml(valor)}</p>
      <div class="scroll">
        <table>
          <thead><tr><th>Fecha venta</th><th>Participaciones</th><th>Bruto</th><th>Bloqueado</th><th>Declarado</th></tr></thead>
          <tbody>${filas}</tbody>
          <tfoot>
            <tr>
              <td>Total</td>
              <td>${formatoCantidad(datos.participaciones_total)}</td>
              <td>${formatoDinero(datos.bruto_total)}</td>
              <td>${formatoDinero(datos.bloqueado_total)}</td>
              <td>${formatoDinero(datos.ganancia)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      ${htmlLotes}
    </div>
  `;
}

function htmlLotesPendientes(lotes) {
  if (!lotes || lotes.length === 0) return "";
  const items = lotes.map((l) => `<li>${formatoCantidad(l.acciones)} participaciones compradas el ${l.fecha}</li>`).join("");
  return `
    <div class="rest">
      Seguían sin vender al cierre de ${anioSeleccionado} — su coste (ya ajustado por la regla de los 2 meses
      si aplica) es el que cuenta cuando por fin se vendan:
      <ul>${items}</ul>
    </div>
  `;
}

// Lo que le importa al usuario después del número: en qué apartado de la
// declaración va. Deliberadamente sin números de casilla: cambian cada
// campaña y esta herramienta no los tiene verificados, así que describimos
// el apartado en palabras y remitimos al borrador o a una gestoría.
function pintarDondeVaEsto(resultado) {
  const contenedor = el("donde-va-esto");
  const anios = resultado.anios_disponibles || [];
  if (!resultado.completo || anios.length === 0) { ocultar(contenedor); return; }

  const ejercicio = resultado.ejercicios[anioSeleccionado];
  const hayDividendos = ejercicio.dividendos && Object.keys(ejercicio.dividendos.por_valor).length > 0;

  contenedor.innerHTML = `
    <h3>Dónde va esto en tu declaración de ${anioSeleccionado}</h3>
    <p><b>Los ${formatoDinero(ejercicio.totales.declarable)}</b> van en el apartado de ganancias y
    pérdidas patrimoniales derivadas de transmisiones, dentro de la base imponible del ahorro.</p>
    ${hayDividendos ? `
    <p><b>Los ${formatoDinero(ejercicio.dividendos.bruto_total)} de dividendos</b> van en un apartado
    distinto: rendimientos del capital mobiliario.</p>` : ""}
    <p class="warn-note">
      Los números de casilla concretos cambian cada campaña. Compruébalos en el borrador de Renta Web o
      con tu gestoría antes de presentar.
    </p>
  `;
  mostrar(contenedor);
}

function pintarAvisos(avisos) {
  const contenedor = el("avisos-lectura");
  if (!avisos || avisos.length === 0) { ocultar(contenedor); return; }

  const items = avisos.map((a) => `<li>${escaparHtml(a)}</li>`).join("");
  contenedor.innerHTML = `
    <p class="lbl">Filas no procesadas · ${avisos.length}</p>
    <ul class="lista-avisos" style="margin-top:0.7rem">${items}</ul>
  `;
  mostrar(contenedor);
}

function pintarDescarga(resultado) {
  const anios = resultado.anios_disponibles || [];
  const hayAlgoQueDescargar = resultado.completo && anios.some((anio) =>
    Object.values(resultado.ejercicios[anio].valores).some((v) => v.desglose && v.desglose.length)
  );
  el("zona-descarga").classList.toggle("oculto", !hayAlgoQueDescargar);
}

// --- Paso 4: descargar el desglose en CSV (gratis, sin registro) --------
//
// Incluye TODOS los ejercicios del fichero (no solo el que se ve en
// pantalla ahora mismo), con una columna "Ejercicio" añadida: es el
// fichero de justificación completo, no una foto de la vista actual.

function descargarDesgloseCSV() {
  if (!ultimoResultado || !ultimoResultado.completo) return;

  const filas = [["Ejercicio", "Valor", "Fecha venta", "Participaciones", "Resultado bruto", "Bloqueado (regla 2 meses)", "Resultado declarado"]];
  for (const anio of ultimoResultado.anios_disponibles || []) {
    for (const [valor, datos] of Object.entries(ultimoResultado.ejercicios[anio].valores)) {
      for (const op of datos.desglose) {
        filas.push([anio, valor, op.fecha, op.participaciones, op.resultado_bruto, op.bloqueado, op.resultado_declarado]);
      }
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

// --- Utilidades -----------------------------------------------------------

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

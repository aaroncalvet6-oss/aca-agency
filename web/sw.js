"use strict";

/* Service worker de FIFO.es: cachea ÚNICAMENTE los ficheros de Pyodide
 * (13-14 MB) para que la segunda visita no vuelva a bajarlos. Se registra
 * desde app.js.
 *
 * Solo intercepta peticiones a la URL de Pyodide, y esa URL lleva la
 * versión en el propio path ("v0.26.4"): el contenido de esa URL nunca
 * cambia, así que cachearla para siempre es seguro. El resto de la web
 * (estilo.css, app.js, tema.js, el motor .py, las páginas HTML...) NO pasa
 * por este service worker: sigue yendo directa a la red, para que un
 * despliegue nuevo se vea sin que nadie se quede pegado a una versión
 * vieja cacheada.
 *
 * Si algún día se sube la versión de Pyodide en index.html, hay que
 * cambiarla también aquí en ORIGEN_PYODIDE/CACHE_PYODIDE — son deliberadamente
 * independientes de cualquier "número de versión" genérico de la caché para
 * que un cambio de versión de Pyodide invalide la caché vieja él solo (URL
 * distinta = entrada de caché distinta) sin necesidad de coordinar nada más.
 */

const CACHE_PYODIDE = "fifo-pyodide-v0.26.4";
const ORIGEN_PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";

// Los mismos cuatro ficheros grandes que pide loadPyodide() (ver el
// comentario junto a PYODIDE_ARCHIVOS_GRANDES en app.js). Se precargan en
// el propio evento "install" — no se espera a que una petición de la
// página los pida — para que estén listos de verdad antes de la segunda
// visita, sin depender de que esta pestaña llegue a controlar la primera.
const ARCHIVOS_A_PRECARGAR = [
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json",
  "pyodide.asm.js",
].map((nombre) => ORIGEN_PYODIDE + nombre);

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches.open(CACHE_PYODIDE).then((cache) =>
      cache.addAll(ARCHIVOS_A_PRECARGAR).catch((error) => {
        // Si el precacheo falla (p.ej. sin red en el primer install), no
        // rompemos la instalación: el handler de "fetch" de más abajo
        // cachea igualmente en cuanto la página pida estos ficheros.
        console.warn("No se ha podido precargar Pyodide en el service worker:", error);
      })
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches
      .keys()
      .then((nombres) =>
        Promise.all(
          nombres
            .filter((nombre) => nombre.startsWith("fifo-pyodide-") && nombre !== CACHE_PYODIDE)
            .map((nombre) => caches.delete(nombre))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (evento) => {
  if (!evento.request.url.startsWith(ORIGEN_PYODIDE)) return;   // todo lo demás, a la red tal cual

  evento.respondWith(
    caches.open(CACHE_PYODIDE).then(async (cache) => {
      const enCache = await cache.match(evento.request);
      if (enCache) return enCache;

      const respuesta = await fetch(evento.request);
      if (respuesta.ok) cache.put(evento.request, respuesta.clone());
      return respuesta;
    })
  );
});

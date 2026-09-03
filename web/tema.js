"use strict";

/* Interruptor de tema, compartido por las tres páginas (index, aviso-legal,
 * privacidad). El oscuro es el tema por defecto de toda la web (ver el
 * comentario al principio de estilo.css): esta web YA arranca en oscuro
 * porque <html> no lleva data-theme salvo que el script inline del <head>
 * de cada página lo ponga a "light" al leer localStorage (eso evita el
 * parpadeo — se decide antes de la primera pintura, sin esperar a este
 * fichero). Aquí solo se conecta el botón: alternar el atributo y guardar
 * la preferencia.
 */

(function () {
  var CLAVE_TEMA = "fifo-renta:tema";
  var boton = document.getElementById("interruptor-tema");
  if (!boton) return;

  function esClaro() {
    return document.documentElement.getAttribute("data-theme") === "light";
  }

  function actualizarBoton() {
    var claro = esClaro();
    boton.setAttribute("aria-pressed", String(claro));
    boton.setAttribute("aria-label", claro ? "Cambiar a modo oscuro" : "Cambiar a modo claro");
  }

  actualizarBoton();

  boton.addEventListener("click", function () {
    if (esClaro()) {
      document.documentElement.removeAttribute("data-theme");
      try { localStorage.removeItem(CLAVE_TEMA); } catch (error) { /* localStorage no disponible: el tema no persiste, pero el interruptor sigue funcionando en esta visita */ }
    } else {
      document.documentElement.setAttribute("data-theme", "light");
      try { localStorage.setItem(CLAVE_TEMA, "claro"); } catch (error) { /* idem */ }
    }
    actualizarBoton();
  });
})();

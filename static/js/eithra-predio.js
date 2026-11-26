// O prédio vivo: um toque no nível abre o painel do que há ali.
//
// O desenho não busca nada: cada nível já chegou montado no painel, e o que
// este script faz é mostrar o bloco do nível tocado e abrir o painel lateral.
// Se o Bootstrap não estiver presente, o desenho continua válido — apenas não
// abre, e nada quebra por causa disso.
(function () {
  "use strict";

  var raiz = document.querySelector(".eithra-predio");
  var painel = document.getElementById("painel-do-predio");
  if (!raiz || !painel || !window.bootstrap || !window.bootstrap.Offcanvas) {
    return;
  }

  var titulo = document.getElementById("painel-do-predio-titulo");
  var instancia = new window.bootstrap.Offcanvas(painel);

  function mostrar(nivel) {
    var blocos = painel.querySelectorAll(".eithra-predio-nivel");
    Array.prototype.forEach.call(blocos, function (bloco) {
      bloco.hidden = bloco.getAttribute("data-nivel") !== nivel;
    });

    var aberto = painel.querySelector(
      '.eithra-predio-nivel[data-nivel="' + nivel + '"]'
    );
    var nome = aberto ? aberto.querySelector(".eithra-predio-nivel-titulo") : null;
    if (titulo) {
      titulo.textContent = nome ? nome.textContent : "Prédio";
    }
  }

  raiz.addEventListener("click", function (evento) {
    var alvo = evento.target.closest("[data-nivel]");
    if (!alvo) {
      return;
    }
    var nivel = alvo.getAttribute("data-nivel");
    if (!nivel) {
      return;
    }
    mostrar(nivel);
    instancia.show();
  });
})();

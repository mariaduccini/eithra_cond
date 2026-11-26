// O prédio vivo: um toque no nível abre o painel do que há ali.
//
// O desenho não busca nada: cada nível já chegou montado no painel, e o que
// este script faz é mostrar o bloco do nível tocado e abrir o painel lateral.
// Se o Bootstrap não estiver presente, o desenho continua válido — apenas não
// abre, e nada quebra por causa disso.
(function () {
  "use strict";

  var raiz = document.querySelector(".eithra-predio-layout") || document.querySelector(".eithra-predio");
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
    Array.prototype.forEach.call(raiz.querySelectorAll("[data-nivel]"), function (el) {
      el.classList.toggle("selecionado", el.getAttribute("data-nivel") === nivel);
    });
  }

  // No computador o painel fica sempre aberto ao lado do desenho; no celular,
  // ele abre por cima. Na tela larga já começa no nível mais urgente.
  var largo = window.matchMedia("(min-width: 992px)");
  if (largo.matches) {
    var inicial = document.querySelector(".eithra-predio .ponto-urgente, .eithra-predio .ponto-atencao");
    mostrar(inicial ? inicial.getAttribute("data-nivel") : "predio");
  }

  // Com mouse: passar por cima de um nível mostra as demandas abertas dele,
  // lidas do próprio painel (nada é buscado de novo).
  if (window.matchMedia("(hover: hover)").matches) {
    var desenho = raiz.querySelector(".eithra-predio-desenho");
    var dica = document.createElement("div");
    dica.className = "eithra-predio-dica";
    dica.hidden = true;
    document.body.appendChild(dica);

    var montar = function (nivel) {
      var secao = painel.querySelector('.eithra-predio-nivel[data-nivel="' + nivel + '"]');
      if (!secao) {
        return false;
      }
      var nome = secao.querySelector(".eithra-predio-nivel-titulo");
      var demandas = secao.querySelectorAll(".eithra-predio-local .eithra-predio-lista a");
      dica.textContent = "";
      var titulo = document.createElement("strong");
      titulo.textContent = nome ? nome.textContent : nivel;
      dica.appendChild(titulo);
      var lista = document.createElement("ul");
      var limite = Math.min(demandas.length, 4);
      for (var i = 0; i < limite; i++) {
        var item = document.createElement("li");
        item.textContent = demandas[i].textContent.trim();
        lista.appendChild(item);
      }
      if (demandas.length > limite) {
        var mais = document.createElement("li");
        mais.textContent = "+" + (demandas.length - limite) + " demanda(s)";
        lista.appendChild(mais);
      }
      if (!demandas.length) {
        var nada = document.createElement("li");
        nada.textContent = "Nenhuma demanda aberta";
        lista.appendChild(nada);
      }
      dica.appendChild(lista);
      var rodape = document.createElement("small");
      rodape.textContent = "Clique para ver tudo";
      dica.appendChild(rodape);
      return true;
    };

    var atual = null;
    desenho.addEventListener("mousemove", function (evento) {
      var alvo = evento.target.closest("[data-nivel]");
      if (!alvo) {
        dica.hidden = true;
        atual = null;
        return;
      }
      var nivel = alvo.getAttribute("data-nivel");
      if (nivel !== atual) {
        atual = nivel;
        if (!montar(nivel)) {
          dica.hidden = true;
          return;
        }
      }
      dica.hidden = false;
      var x = evento.clientX + 16;
      var y = evento.clientY + 16;
      if (x + dica.offsetWidth > window.innerWidth - 8) {
        x = evento.clientX - dica.offsetWidth - 16;
      }
      if (y + dica.offsetHeight > window.innerHeight - 8) {
        y = evento.clientY - dica.offsetHeight - 16;
      }
      dica.style.left = x + "px";
      dica.style.top = y + "px";
    });
    desenho.addEventListener("mouseleave", function () {
      dica.hidden = true;
      atual = null;
    });
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
    if (!largo.matches) {
      instancia.show();
    }
  });
})();

// O clipe dos anexos da demanda.
//
// Um botão com `data-eithra-clipe="#id_do_campo"` abre o seletor do campo de
// arquivo escondido, e as miniaturas mostradas antes de confirmar vêm do que
// estiver nesse campo. Tirar uma miniatura refaz a lista do próprio campo —
// o envio leva exatamente o que está nele, nem a mais, nem a menos.
(function () {
  "use strict";

  function caixaDeMiniaturas(entrada) {
    return document.querySelector('[data-eithra-miniaturas="#' + entrada.id + '"]');
  }

  function montar(entrada, caixa) {
    function desenhar() {
      caixa.textContent = "";

      var arquivos = Array.prototype.slice.call(entrada.files || []);
      caixa.hidden = arquivos.length === 0;

      arquivos.forEach(function (arquivo, indice) {
        var peca = document.createElement("div");
        peca.className = "eithra-miniatura";

        if (arquivo.type && arquivo.type.indexOf("image/") === 0) {
          var imagem = document.createElement("img");
          imagem.src = URL.createObjectURL(arquivo);
          imagem.alt = arquivo.name;
          peca.appendChild(imagem);
        } else {
          var moldura = document.createElement("div");
          moldura.className = "eithra-miniatura-arquivo";
          moldura.innerHTML =
            '<i class="bx bx-file" aria-hidden="true"></i>' +
            '<span class="eithra-miniatura-nome"></span>';
          moldura.querySelector(".eithra-miniatura-nome").textContent = arquivo.name;
          peca.appendChild(moldura);
        }

        var tirar = document.createElement("button");
        tirar.type = "button";
        tirar.className = "eithra-miniatura-tirar";
        tirar.setAttribute("aria-label", "Remover " + arquivo.name);
        tirar.innerHTML = '<i class="bx bx-x" aria-hidden="true"></i>';
        tirar.addEventListener("click", function () {
          var restantes = arquivos.filter(function (_, posicao) {
            return posicao !== indice;
          });
          var transporte = new DataTransfer();
          restantes.forEach(function (item) {
            transporte.items.add(item);
          });
          entrada.files = transporte.files;
          desenhar();
        });

        peca.appendChild(tirar);
        caixa.appendChild(peca);
      });
    }

    desenhar();
    entrada.addEventListener("change", desenhar);
  }

  document.querySelectorAll("[data-eithra-clipe]").forEach(function (botao) {
    var entrada = document.querySelector(botao.getAttribute("data-eithra-clipe"));
    if (!entrada) {
      return;
    }
    botao.addEventListener("click", function () {
      entrada.click();
    });

    var caixa = caixaDeMiniaturas(entrada);
    if (caixa) {
      montar(entrada, caixa);
    }
  });
})();

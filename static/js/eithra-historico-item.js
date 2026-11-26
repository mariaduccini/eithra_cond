/* O histórico de reposição do item escolhido numa linha de compra.

   Ao escolher o item, a tela mostra o que já aconteceu com ele: quanto veio na
   última compra, quanto saiu e quanto saiu sem destino — com o link para a
   lista curta das saídas. É memória para decidir quanto repor, não um campo:
   por isso o bloco nasce escondido e some quando não há item escolhido. */
(function () {
  "use strict";

  function montar(dados, alvo) {
    if (!dados) {
      alvo.hidden = true;
      alvo.innerHTML = "";
      return;
    }
    const partes = [];
    partes.push(
      dados.quantidade && dados.data
        ? "Última compra: " + dados.quantidade + " em " + dados.data + "."
        : "Sem compra registrada."
    );
    partes.push("Saídas registradas: " + dados.saidas + ".");
    partes.push("Sem destino: " + dados.sem_destino + ".");
    const texto = document.createElement("span");
    texto.textContent = partes.join(" ");
    alvo.innerHTML = "";
    alvo.appendChild(texto);
    if (dados.saidas_url) {
      const link = document.createElement("a");
      link.href = dados.saidas_url;
      link.className = "text-decoration-none ms-1";
      link.textContent = "Ver saídas";
      alvo.appendChild(link);
    }
    alvo.hidden = false;
  }

  function campoItem(bloco) {
    return bloco.closest("form").querySelector("select[name='item_estoque']");
  }

  function buscar(bloco, chave) {
    const select = campoItem(bloco);
    if (!select || !chave) {
      montar(null, bloco);
      return;
    }
    const parametros = new URLSearchParams({ item: chave });
    if (bloco.dataset.eithraHistoricoCondominio) {
      parametros.set("condominio", bloco.dataset.eithraHistoricoCondominio);
    }
    fetch(bloco.dataset.eithraHistoricoUrl + "?" + parametros.toString(), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (resposta) {
        return resposta.ok ? resposta.json() : { historico: null };
      })
      .then(function (corpo) {
        montar(corpo.historico, bloco);
      })
      .catch(function () {
        montar(null, bloco);
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-eithra-historico]").forEach(function (bloco) {
      const select = campoItem(bloco);
      if (!select) return;
      select.addEventListener("change", function () {
        buscar(bloco, select.value);
      });
      if (select.value) buscar(bloco, select.value);
    });
  });
})();

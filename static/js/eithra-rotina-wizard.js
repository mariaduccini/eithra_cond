(function () {
  "use strict";

  function iniciar(formulario) {
    var etapas = Array.prototype.slice.call(
      formulario.querySelectorAll("[data-rotina-etapa]")
    );
    var indicadores = Array.prototype.slice.call(
      formulario.querySelectorAll("[data-rotina-indicador]")
    );
    var voltar = formulario.querySelector("[data-rotina-voltar]");
    var avancar = formulario.querySelector("[data-rotina-avancar]");
    var enviar = formulario.querySelector("[data-rotina-enviar]");
    var atual = 0;

    function valorParaResumo(nome) {
      var campo = formulario.elements.namedItem(nome);
      if (!campo) {
        return "";
      }
      if (campo.type === "checkbox") {
        return campo.checked ? "Sim" : "Não";
      }
      if (campo.tagName === "SELECT") {
        return campo.value && campo.selectedOptions.length
          ? campo.selectedOptions[0].text.trim()
          : "";
      }
      if (campo.type === "date" && campo.value) {
        var partes = campo.value.split("-");
        return partes.length === 3 ? partes[2] + "/" + partes[1] + "/" + partes[0] : campo.value;
      }
      return (campo.value || "").trim();
    }

    function atualizarResumo() {
      formulario.querySelectorAll("[data-rotina-resumo]").forEach(function (saida) {
        var nome = saida.dataset.rotinaResumo;
        var valor = valorParaResumo(nome);
        var linha = saida.closest("[data-rotina-resumo-linha]");
        saida.textContent = valor || "Não informado";
        if (linha) {
          linha.hidden = !valor && nome !== "gera_manutencao";
        }
      });
    }

    function mostrarEtapa(indice) {
      atual = Math.max(0, Math.min(indice, etapas.length - 1));
      etapas.forEach(function (etapa, posicao) {
        etapa.hidden = posicao !== atual;
      });
      indicadores.forEach(function (indicador, posicao) {
        indicador.classList.toggle("is-active", posicao === atual);
        indicador.classList.toggle("is-complete", posicao < atual);
      });
      voltar.hidden = atual === 0;
      avancar.hidden = atual === etapas.length - 1;
      enviar.hidden = atual !== etapas.length - 1;
      if (atual === etapas.length - 1) {
        atualizarResumo();
      }
    }

    avancar.addEventListener("click", function () {
      var obrigatorios = etapas[atual].querySelectorAll("[required]");
      for (var i = 0; i < obrigatorios.length; i += 1) {
        if (!obrigatorios[i].checkValidity()) {
          obrigatorios[i].reportValidity();
          obrigatorios[i].focus();
          return;
        }
      }
      mostrarEtapa(atual + 1);
    });

    voltar.addEventListener("click", function () {
      mostrarEtapa(atual - 1);
    });

    formulario.addEventListener("input", atualizarResumo);
    formulario.addEventListener("change", atualizarResumo);

    var etapaComErro = etapas.findIndex(function (etapa) {
      return etapa.querySelector(".text-danger");
    });
    mostrarEtapa(etapaComErro >= 0 ? etapaComErro : 0);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-rotina-wizard]").forEach(iniciar);
  });
})();

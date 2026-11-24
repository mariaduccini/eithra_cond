(function () {
  "use strict";

  function atualizarReembolso(form) {
    const pessoa = form.querySelector("[data-reembolso-pessoa]");
    const pessoaCheck = form.querySelector("#id_comprada_por_outra_pessoa");
    const solicitacao = form.querySelector("#id_precisa_reembolso");
    const campos = form.querySelector("[data-reembolso-campos]");
    const valor = form.querySelector("#id_valor_reembolso");
    if (!pessoa || !pessoaCheck || !solicitacao || !campos) return;

    pessoa.hidden = !pessoaCheck.checked;
    solicitacao.disabled = !pessoaCheck.checked;
    campos.hidden = !pessoaCheck.checked || !solicitacao.checked;
    campos.querySelectorAll("input, select, textarea").forEach(function (campo) {
      campo.disabled = campos.hidden;
    });
    if (!campos.hidden && valor && !valor.value) {
      valor.value = form.dataset.totalCompra || "";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-confirmar-compra]").forEach(function (form) {
      form.querySelectorAll(
        "#id_comprada_por_outra_pessoa, #id_precisa_reembolso"
      ).forEach(function (campo) {
        campo.addEventListener("change", function () {
          atualizarReembolso(form);
        });
      });
      atualizarReembolso(form);
    });
  });
})();

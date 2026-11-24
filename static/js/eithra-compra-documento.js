(function () {
  "use strict";

  // O documento da compra entra sozinho: ao escolher o arquivo ou tirar a foto,
  // o envio e a leitura começam sem um segundo botão. Sem JavaScript, o
  // formulário continua funcionando pelo botão de fallback em <noscript>.
  var TEXTOS = {
    enviando: "Enviando documento…",
    lendo: "Lendo documento…",
    pronto: "Documento processado. Confira as informações.",
    falha:
      "Não foi possível concluir a leitura. O documento foi mantido e você pode tentar novamente."
  };

  function prepararCaptura(botao, entrada) {
    if (botao.dataset.captura === "imagem") {
      // Orientação ao navegador/mobile: não é garantia de foto ao vivo.
      entrada.setAttribute("accept", "image/*");
      entrada.setAttribute("capture", "environment");
    } else {
      entrada.setAttribute(
        "accept",
        ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
      );
      entrada.removeAttribute("capture");
    }
  }

  function iniciar(form) {
    var entrada = form.querySelector('input[type="file"][name="arquivo"]');
    var status = document.querySelector("[data-upload-status]");
    var botoes = form.querySelectorAll("[data-captura]");
    if (!entrada) return;

    function escrever(mensagem, tom) {
      if (!status) return;
      status.hidden = false;
      status.textContent = mensagem;
      status.classList.remove(
        "text-body-secondary",
        "text-success",
        "text-danger"
      );
      status.classList.add(tom);
    }

    function ocupado(estaOcupado) {
      botoes.forEach(function (botao) {
        botao.classList.toggle("disabled", estaOcupado);
        botao.setAttribute("aria-disabled", estaOcupado ? "true" : "false");
      });
      entrada.disabled = estaOcupado;
    }

    botoes.forEach(function (botao) {
      botao.addEventListener("click", function () {
        prepararCaptura(botao, entrada);
      });
    });

    entrada.addEventListener("change", function () {
      if (!entrada.files || !entrada.files.length) return;
      enviar();
    });

    function enviar() {
      // Um envio em andamento não pode ser disparado duas vezes.
      if (entrada.disabled) return;
      ocupado(true);
      escrever(TEXTOS.enviando, "text-body-secondary");

      var pedido = new XMLHttpRequest();
      pedido.open("POST", form.action, true);
      pedido.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      pedido.upload.addEventListener("load", function () {
        // O corpo já subiu: agora o servidor lê o documento.
        escrever(TEXTOS.lendo, "text-body-secondary");
      });
      pedido.addEventListener("load", function () {
        var corpo = {};
        try {
          corpo = JSON.parse(pedido.responseText);
        } catch (erro) {
          corpo = {};
        }
        if (pedido.status >= 200 && pedido.status < 300 && corpo.ok) {
          escrever(TEXTOS.pronto, "text-success");
          // Recarrega para o documento recém-lido aparecer já persistido.
          window.setTimeout(function () {
            window.location.reload();
          }, 700);
          return;
        }
        escrever(corpo.erro || TEXTOS.falha, "text-danger");
        falhou();
      });
      pedido.addEventListener("error", function () {
        escrever(TEXTOS.falha, "text-danger");
        falhou();
      });
      pedido.send(new FormData(form));
    }

    function falhou() {
      ocupado(false);
      // O arquivo escolhido é liberado para uma nova tentativa.
      entrada.value = "";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-upload-documento]").forEach(iniciar);
  });
})();

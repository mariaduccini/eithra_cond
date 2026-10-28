/* Ditar por voz: a fala vira texto e para aí.
 *
 * Este controlador não sabe nada sobre demandas, portaria, chegada ou
 * autorização. Ele faz uma coisa só: grava a fala do navegador, manda o áudio
 * para o endereço indicado no botão e escreve o texto que voltou no campo
 * indicado no botão. Interpretar, sugerir e registrar continua sendo trabalho
 * de quem recebeu o texto — e só depois da confirmação da pessoa.
 *
 * O que ele não faz, de propósito:
 *
 * - não envia nada sem o clique: a permissão do microfone só é pedida quando a
 *   pessoa decide falar, nunca ao abrir a tela;
 * - não aparece onde não funciona: o botão nasce escondido e só se mostra se o
 *   navegador souber gravar, para que o relato por texto siga idêntico onde a
 *   voz não existe;
 * - não engole o que foi dito: se a transcrição falhar, o campo continua com o
 *   que já havia e a mensagem diz o que fazer;
 * - não expõe o serviço: nada de nome de modelo, endereço ou formato técnico
 *   chega à tela.
 *
 * Como usar:
 *
 *   <button
 *     type="button"
 *     data-eithra-ditar
 *     data-eithra-ditar-url="/caminho/transcrever/"
 *     data-eithra-ditar-campo="#id_relato"
 *     aria-pressed="false"
 *     hidden>
 *     <i class="bx bx-microphone"></i>Falar
 *   </button>
 *   <p data-eithra-ditar-recado hidden aria-live="polite"></p>
 *
 * O campo de condomínio, quando houver, é lido do próprio formulário do botão
 * (``[name="condominio"]``) — a autorização continua sendo decidida no backend.
 */
(function () {
  "use strict";

  // Só os formatos que o backend conhece, na ordem de preferência: o primeiro
  // que este navegador souber gravar é o que vai.
  var FORMATOS = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
    "audio/mp4"
  ];

  var TEXTO_REPOUSO = "Falar o que aconteceu";
  var TEXTO_OUVINDO = "Ouvindo… fale e toque para parar.";
  var TEXTO_TRANSCREVENDO = "Transcrevendo…";
  var TEXTO_PRONTO = "Transcrição pronta. Revise e toque em Continuar.";
  var TEXTO_FALHA =
    "Não foi possível entender o áudio agora. Tente de novo ou escreva.";
  var TEXTO_SEM_FORMATO =
    "Este navegador não grava num formato que o Eithra entende. Escreva o que aconteceu.";
  var TEXTO_SEM_MICROFONE =
    "O microfone não foi liberado. Você pode escrever o que aconteceu.";
  var TEXTO_SEM_GRAVACAO =
    "Não foi possível gravar neste navegador. Escreva o que aconteceu.";

  function podeGravar() {
    return (
      typeof window.MediaRecorder !== "undefined" &&
      !!window.navigator.mediaDevices &&
      typeof window.navigator.mediaDevices.getUserMedia === "function"
    );
  }

  function formatoEscolhido() {
    if (typeof window.MediaRecorder.isTypeSupported !== "function") {
      return "";
    }
    for (var i = 0; i < FORMATOS.length; i++) {
      if (window.MediaRecorder.isTypeSupported(FORMATOS[i])) {
        return FORMATOS[i];
      }
    }
    return "";
  }

  function extensaoDo(tipo) {
    if (tipo.indexOf("ogg") !== -1) {
      return "ogg";
    }
    if (tipo.indexOf("mp4") !== -1) {
      return "m4a";
    }
    return "webm";
  }

  function configurar(botao) {
    var formulario = botao.closest("form");
    var campo = document.querySelector(botao.dataset.eithraDitarCampo || "");
    var recado = document.querySelector("[data-eithra-ditar-recado]");
    if (!formulario || !campo) {
      return;
    }

    var token = formulario.querySelector("input[name='csrfmiddlewaretoken']");
    var condominio = formulario.querySelector("[name='condominio']");
    var gravador = null;
    var fluxo = null;
    var pedacos = [];

    function avisar(texto, tom) {
      if (!recado) {
        return;
      }
      recado.textContent = texto || "";
      recado.hidden = !texto;
      recado.classList.toggle("text-danger", tom === "erro");
      recado.classList.toggle("text-success", tom === "pronto");
    }

    function repousar() {
      botao.disabled = false;
      botao.classList.remove("eithra-ditar--ouvindo");
      botao.setAttribute("aria-pressed", "false");
      botao.setAttribute("aria-label", TEXTO_REPOUSO);
      botao.setAttribute("title", TEXTO_REPOUSO);
      var icone = botao.querySelector("i");
      if (icone) {
        icone.className = "bx bx-microphone me-1";
      }
    }

    // O microfone é liberado assim que a gravação acaba — nada fica aberto
    // enquanto a transcrição acontece.
    function liberarMicrofone() {
      if (!fluxo) {
        return;
      }
      fluxo.getTracks().forEach(function (faixa) {
        faixa.stop();
      });
      fluxo = null;
    }

    function transcrever(blob) {
      var dados = new FormData();
      dados.append("audio", blob, "relato." + extensaoDo(blob.type || ""));
      if (condominio && condominio.value) {
        dados.append("condominio", condominio.value);
      }
      botao.disabled = true;
      botao.classList.remove("eithra-ditar--ouvindo");
      var icone = botao.querySelector("i");
      if (icone) {
        icone.className = "bx bx-loader-alt bx-spin me-1";
      }
      avisar(TEXTO_TRANSCREVENDO, "");

      fetch(botao.dataset.eithraDitarUrl, {
        method: "POST",
        body: dados,
        headers: token ? { "X-CSRFToken": token.value } : {}
      })
        .then(function (resposta) {
          if (!resposta.ok) {
            throw new Error("falha");
          }
          return resposta.json();
        })
        .then(function (leitura) {
          repousar();
          if (leitura && leitura.texto) {
            campo.value = leitura.texto;
            campo.focus();
            avisar(TEXTO_PRONTO, "pronto");
            return;
          }
          avisar((leitura && leitura.mensagem) || TEXTO_FALHA, "erro");
        })
        .catch(function () {
          repousar();
          avisar(TEXTO_FALHA, "erro");
        });
    }

    function comecar(formato) {
      botao.disabled = false;
      try {
        gravador = new window.MediaRecorder(
          fluxo,
          formato ? { mimeType: formato } : undefined
        );
      } catch (erro) {
        liberarMicrofone();
        repousar();
        avisar(TEXTO_SEM_GRAVACAO, "erro");
        return;
      }
      pedacos = [];
      gravador.addEventListener("dataavailable", function (evento) {
        if (evento.data && evento.data.size) {
          pedacos.push(evento.data);
        }
      });
      gravador.addEventListener("stop", function () {
        var tipo = gravador ? gravador.mimeType || formato : formato;
        gravador = null;
        liberarMicrofone();
        transcrever(new Blob(pedacos, tipo ? { type: tipo } : undefined));
      });
      gravador.start();
      botao.classList.add("eithra-ditar--ouvindo");
      botao.setAttribute("aria-pressed", "true");
      botao.setAttribute("aria-label", "Parar a gravação");
      botao.setAttribute("title", "Parar a gravação");
      var icone = botao.querySelector("i");
      if (icone) {
        icone.className = "bx bx-stop-circle me-1";
      }
      avisar(TEXTO_OUVINDO, "");
    }

    function pararGravacao() {
      if (gravador && gravador.state === "recording") {
        gravador.stop();
        return;
      }
      liberarMicrofone();
      repousar();
    }

    // Só agora o microfone se oferece: se o navegador não grava, ele continua
    // escondido e o relato por texto não muda em nada.
    if (!podeGravar() || !formatoEscolhido()) {
      return;
    }
    botao.hidden = false;

    botao.addEventListener("click", function () {
      if (gravador && gravador.state === "recording") {
        pararGravacao();
        return;
      }
      // A permissão só é pedida aqui, depois do clique — nunca ao abrir a tela.
      botao.disabled = true;
      window.navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then(function (recebido) {
          fluxo = recebido;
          var formato = formatoEscolhido();
          if (!formato) {
            liberarMicrofone();
            repousar();
            avisar(TEXTO_SEM_FORMATO, "erro");
            return;
          }
          comecar(formato);
        })
        .catch(function (erro) {
          liberarMicrofone();
          repousar();
          avisar(
            erro && erro.name === "NotAllowedError"
              ? TEXTO_SEM_MICROFONE
              : TEXTO_SEM_GRAVACAO,
            "erro"
          );
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-eithra-ditar]").forEach(configurar);
  });
})();

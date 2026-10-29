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
 * - não engole o que foi dito: o que já estava escrito continua ali, e a
 *   transcrição entra depois, em linha própria. Falhar não apaga nada;
 * - não mostra dois erros ao mesmo tempo: enquanto a voz fala pelo campo, o
 *   aviso do campo cala. A cobrança de relato só volta quando a pessoa tenta
 *   seguir sem ter escrito nada;
 * - não expõe o serviço: nada de nome de modelo, endereço ou formato técnico
 *   chega à tela;
 * - não decide por conta própria quando seguir: quando a fala basta, quem diz
 *   isso é o backend, na resposta da transcrição — e o que este controlador
 *   faz é apertar o mesmo Continuar que a pessoa apertaria;
 * - não grava com o modal fora da tela: quem fecha o modal avisa, e o
 *   microfone é encerrado e liberado — o áudio pela metade não vira transcrição.
 *
 * Como usar — o microfone mora dentro do próprio campo, como um atalho dele:
 *
 *   <div class="eithra-campo-voz">
 *     <textarea id="id_relato"></textarea>
 *     <button
 *       type="button"
 *       class="eithra-ditar"
 *       data-eithra-ditar
 *       data-eithra-ditar-url="/caminho/transcrever/"
 *       data-eithra-ditar-campo="#id_relato"
 *       aria-label="Gravar relato por voz"
 *       aria-pressed="false"
 *       hidden>
 *       <i class="bx bx-microphone" aria-hidden="true"></i>
 *     </button>
 *   </div>
 *   <p data-eithra-ditar-recado hidden aria-live="polite"></p>
 *
 * O campo de condomínio, quando houver, é lido do próprio formulário do botão
 * (``[name="condominio"]``) — a autorização continua sendo decidida no backend.
 *
 * Quem fecha o modal anuncia ``eithra:descartar`` no documento, e é assim que
 * a gravação em curso é encerrada sem que este controlador precise conhecer
 * modal nenhum.
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

  var ROTULO_PRONTO = "Gravar relato por voz";
  var ROTULO_GRAVANDO = "Parar a gravação";
  var TEXTO_GRAVANDO = "Gravando…";
  var TEXTO_TRANSCREVENDO = "Transcrevendo…";
  var TEXTO_PRONTO = "Transcrição pronta. Revise antes de continuar.";
  var TEXTO_FALHA =
    "Não foi possível transcrever o áudio. Tente novamente ou digite o relato.";
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

  // O que já estava escrito continua escrito: a fala entra depois, em linha
  // própria, e quem ditou decide o que fazer com o resto.
  function juntar(existente, novo) {
    var atual = (existente || "").trim();
    return atual ? atual + "\n" + novo : novo;
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
    // O que foi dito enquanto o modal estava de pé é do modal: quando ele sai,
    // o áudio já reunido perde o sentido e o microfone é liberado.
    var descartando = false;

    // Enquanto a voz responde pelo campo, a cobrança do campo fica quieta: dois
    // avisos ao mesmo tempo dizem duas coisas e não ajudam em nenhuma. Se a
    // pessoa seguir sem escrever, o envio traz a cobrança de volta — do jeito
    // de sempre, decidida no backend.
    function calarAvisoDoCampo() {
      var caixa = botao.closest("[data-eithra-campo-voz]");
      if (!caixa) {
        return;
      }
      caixa.querySelectorAll("[data-eithra-erro-de-campo]").forEach(function (erro) {
        erro.hidden = true;
      });
    }

    function avisar(texto, tom) {
      if (!texto) {
        return;
      }
      calarAvisoDoCampo();
      if (!recado) {
        return;
      }
      recado.textContent = texto;
      recado.hidden = false;
      recado.classList.toggle("text-danger", tom === "erro");
      recado.classList.toggle("text-success", tom === "pronto");
    }

    function limparRecado() {
      if (!recado) {
        return;
      }
      recado.textContent = "";
      recado.hidden = true;
      recado.classList.remove("text-danger", "text-success");
    }

    function repousar() {
      botao.disabled = false;
      botao.classList.remove("eithra-ditar--ouvindo");
      botao.setAttribute("aria-pressed", "false");
      botao.setAttribute("aria-label", ROTULO_PRONTO);
      botao.setAttribute("title", ROTULO_PRONTO);
      var icone = botao.querySelector("i");
      if (icone) {
        icone.className = "bx bx-microphone";
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

    // Segue adiante apertando o mesmo Continuar que a pessoa apertaria. Quem
    // diz se a fala já bastou é o backend, na resposta da transcrição: aqui não
    // mora nenhuma regra de demanda — só o caminho até o botão que já existe.
    // Sem botão de etapa (fora do registro, como na atualização de uma
    // demanda), não há para onde seguir.
    function seguirComoSeApertasseContinuar() {
      var continuar = formulario.querySelector(
        "button[name='wizard'][type='submit']"
      );
      if (!continuar) {
        return;
      }
      if (typeof formulario.requestSubmit === "function") {
        formulario.requestSubmit(continuar);
        return;
      }
      continuar.click();
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
        icone.className = "bx bx-loader-alt bx-spin";
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
            // Não substitui: acrescenta. O que a pessoa já tinha escrito é
            // dela, e a fala vem depois.
            campo.value = juntar(campo.value, leitura.texto);
            campo.focus();
            avisar(TEXTO_PRONTO, "pronto");
            // A fala bastou? Então o EITHRA já leu o relato e segue adiante: a
            // etapa das informações chega preenchida, e quem ditou só confere —
            // sem mais um clique. Quando a fala veio vazia ou sem fato
            // operacional, o backend não manda seguir, e a descrição continua
            // aberta para ser completada.
            if (leitura.avancar) {
              seguirComoSeApertasseContinuar();
            }
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
        if (descartando) {
          // O modal foi fechado no meio: o que foi reunido não vira
          // transcrição, porque não há mais tela para receber o texto.
          pedacos = [];
          return;
        }
        transcrever(new Blob(pedacos, tipo ? { type: tipo } : undefined));
      });
      gravador.start();
      botao.classList.add("eithra-ditar--ouvindo");
      botao.setAttribute("aria-pressed", "true");
      botao.setAttribute("aria-label", ROTULO_GRAVANDO);
      botao.setAttribute("title", ROTULO_GRAVANDO);
      var icone = botao.querySelector("i");
      if (icone) {
        icone.className = "bx bx-stop-circle";
      }
      avisar(TEXTO_GRAVANDO, "");
    }

    function pararGravacao() {
      if (gravador && gravador.state === "recording") {
        gravador.stop();
        return;
      }
      liberarMicrofone();
      repousar();
    }

    // O modal saiu da tela: encerra a gravação em curso, libera o microfone e
    // devolve o botão ao repouso. O que já estava escrito no campo é da pessoa
    // e não é tocado aqui — a tela inteira está indo embora com o fechamento.
    function descartar() {
      descartando = true;
      var ativo = gravador;
      gravador = null;
      pedacos = [];
      if (ativo && ativo.state === "recording") {
        ativo.stop();
      }
      liberarMicrofone();
      repousar();
      limparRecado();
    }

    // Só agora o microfone se oferece: se o navegador não grava, ele continua
    // escondido e o relato por texto não muda em nada.
    if (!podeGravar() || !formatoEscolhido()) {
      return;
    }
    botao.hidden = false;

    // Quem fecha o modal avisa: a gravação em curso é encerrada e o microfone
    // é liberado, para o navegador não seguir gravando depois de o modal sair.
    document.addEventListener("eithra:descartar", descartar);

    botao.addEventListener("click", function () {
      if (gravador && gravador.state === "recording") {
        pararGravacao();
        return;
      }
      // Uma fala nova é um recomeço: o descarte anterior não vale mais.
      descartando = false;
      // A permissão só é pedida aqui, depois do clique — nunca ao abrir a tela.
      botao.disabled = true;
      limparRecado();
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

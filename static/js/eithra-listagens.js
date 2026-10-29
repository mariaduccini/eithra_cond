/* ==========================================================================
   Eithra · listagens que respondem sozinhas
   --------------------------------------------------------------------------
   Três ideias, um arquivo só, sem biblioteca e sem framework:

   1. A busca não exige Enter. Digitou, o Eithra espera um instante para não
      disparar uma consulta a cada tecla, e então atualiza a lista. Apagou o
      texto, a lista volta inteira.

   2. O filtro não exige clicar em "Filtrar". Escolheu uma situação, trocou o
      responsável, a lista já vem filtrada. Onde há texto livre, vale a mesma
      espera da busca.

   3. Onde a grid já tem para onde levar, a linha inteira abre o registro —
      e o botão "visualizar" deixa de existir.

   Em nenhum dos casos o número da página viaja junto: mudar a busca ou o
   filtro devolve a lista para a primeira página, que é onde a pessoa
   naturalmente olha depois de mudar o que procura.
   ========================================================================== */

(function () {
  "use strict";

  // Quanto esperar depois da última tecla. Curto o bastante para parecer
  // imediato, longo o bastante para não consultar o servidor a cada letra.
  var ESPERA_TEXTO = 400;

  // O que, dentro da linha, responde por conta própria. Um clique que nasce
  // aqui pertence ao controle — abrir o menu de três pontos, marcar a caixa,
  // seguir o link do título — e não à navegação da linha.
  var CONTROLES = "a, button, input, select, textarea, label, form, .dropdown";

  function enviar(formulario) {
    if (!formulario) {
      return;
    }
    if (typeof formulario.requestSubmit === "function") {
      formulario.requestSubmit();
    } else {
      formulario.submit();
    }
  }

  /* Busca contextual ------------------------------------------------------ */
  document.querySelectorAll("form[data-eithra-busca]").forEach(function (form) {
    var campo = form.querySelector("input[type='search']");
    if (!campo) {
      return;
    }
    var espera = null;
    var ultimoEnviado = campo.value;

    campo.addEventListener("input", function () {
      if (espera) {
        window.clearTimeout(espera);
      }
      espera = window.setTimeout(function () {
        if (campo.value === ultimoEnviado) {
          return;
        }
        ultimoEnviado = campo.value;
        enviar(form);
      }, ESPERA_TEXTO);
    });

    // A lupa nativa do campo limpa o texto sem passar pelo "x" do Eithra.
    campo.addEventListener("search", function () {
      if (espera) {
        window.clearTimeout(espera);
      }
      ultimoEnviado = campo.value;
      enviar(form);
    });

    // Enter continua valendo; só deixou de ser obrigatório.
    form.addEventListener("submit", function () {
      if (espera) {
        window.clearTimeout(espera);
      }
      ultimoEnviado = campo.value;
    });
  });

  /* Filtros reativos ------------------------------------------------------- */
  document
    .querySelectorAll("form[data-eithra-filtros]")
    .forEach(function (form) {
      var espera = null;

      form.querySelectorAll("select, input, textarea").forEach(function (campo) {
        var tipo = (campo.getAttribute("type") || "").toLowerCase();
        if (tipo === "hidden" || tipo === "submit" || tipo === "button") {
          return;
        }

        // Escolha em lista, data ou caixa: aplica na hora. Digitação solta:
        // espera a pessoa parar de escrever.
        var imediato =
          campo.tagName === "SELECT" || tipo === "date" || tipo === "checkbox";

        campo.addEventListener(imediato ? "change" : "input", function () {
          if (espera) {
            window.clearTimeout(espera);
          }
          if (imediato) {
            enviar(form);
            return;
          }
          espera = window.setTimeout(function () {
            enviar(form);
          }, ESPERA_TEXTO);
        });

        // Um Enter no meio de um campo de texto não deve submeter duas vezes.
        campo.addEventListener("keydown", function (evento) {
          if (evento.key === "Enter" && !imediato) {
            evento.preventDefault();
            if (espera) {
              window.clearTimeout(espera);
            }
            enviar(form);
          }
        });
      });
    });

  /* Linha e cartão que abrem o registro ------------------------------------ */
  // Onde já existe uma tela para onde levar — o detalhe de uma demanda, uma
  // manutenção, uma compra —, clicar na área livre do registro é o caminho
  // natural, e o botão "visualizar" vira repetição. Isto mora aqui, e não em
  // cada tela, para que o comportamento seja um só em todo o Eithra.
  //
  // A linha não vira controle de formulário: quem usa teclado continua
  // chegando ao detalhe pelo link do título, que segue sendo o alvo de foco e
  // o que a leitura de tela anuncia. Aqui só se estende o clique do mouse.
  document.querySelectorAll("[data-href]").forEach(function (alvo) {
    alvo.addEventListener("click", function (evento) {
      // Atalhos do navegador — abrir em nova aba, nova janela, salvar —
      // continuam sendo do navegador.
      if (evento.defaultPrevented || evento.button !== 0) {
        return;
      }
      if (evento.metaKey || evento.ctrlKey || evento.shiftKey || evento.altKey) {
        return;
      }
      // O controle dentro do registro manda mais que o registro.
      if (evento.target.closest(CONTROLES)) {
        return;
      }
      var destino = alvo.getAttribute("data-href");
      if (destino) {
        window.location.assign(destino);
      }
    });
  });
})();

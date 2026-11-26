/*
 * A contagem do estoque, campo a campo.
 *
 * O foco começa no primeiro campo, e o Enter vai para o próximo — a contagem é
 * uma leitura seguida, não uma caça ao campo com o mouse. No último campo, o
 * Enter não envia nada: quem salva é o botão, para ninguém lançar ajuste sem
 * querer.
 */
(function () {
  'use strict';

  function iniciar() {
    var formulario = document.querySelector('[data-eithra-contagem]');
    if (!formulario) {
      return;
    }
    var campos = Array.prototype.slice.call(
      formulario.querySelectorAll('[data-eithra-contagem-campo]')
    );
    if (!campos.length) {
      return;
    }

    campos.forEach(function (campo, indice) {
      campo.addEventListener('keydown', function (evento) {
        if (evento.key !== 'Enter') {
          return;
        }
        evento.preventDefault();
        var proximo = campos[indice + 1];
        if (proximo) {
          proximo.focus();
          proximo.select();
        }
      });
    });

    campos[0].focus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();

/*
 * O material que acompanha o trabalho.
 *
 * Os modais de concluir (manutenção, ocorrência, demanda) e a retirada rápida
 * oferecem os itens de estoque como chips. Um toque soma um; o "×" tira. A
 * lista vem do estoque do condomínio do registro — resolvido no backend, nunca
 * digitado — e o texto do que foi feito sugere a quantidade: "troquei 2
 * lâmpadas" já marca o chip com 2. É só um palpite, e nada é gravado sem um
 * toque: a sugestão apenas poupa a digitação de quem confirma.
 *
 * O que é gravado vai num campo escondido no formato "pk:quantidade", separado
 * por vírgula. Quem decide o que isso significa é o serviço do estoque.
 */
(function () {
  'use strict';

  // Mesmas palavras que o serviço ignora ao casar o nome do item com o relato.
  var IGNORADAS = ['para', 'com', 'sem', 'dos', 'das', 'uma', 'tipo', 'caixa', 'pacote'];

  function semAcento(texto) {
    return (texto || '')
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase();
  }

  function radicais(nome) {
    var palavras = semAcento(nome).match(/[0-9a-z]+/g) || [];
    var saida = [];
    palavras.forEach(function (palavra) {
      if (palavra.length < 4 || IGNORADAS.indexOf(palavra) !== -1) {
        return;
      }
      if (palavra.charAt(palavra.length - 1) === 's' && palavra.length > 4) {
        palavra = palavra.slice(0, -1);
      }
      saida.push(palavra);
    });
    return saida;
  }

  function sugestao(nome, texto) {
    var alvo = semAcento(texto);
    if (!alvo) {
      return 0;
    }
    var lista = radicais(nome);
    for (var i = 0; i < lista.length; i += 1) {
      var radical = lista[i];
      var posicao = alvo.indexOf(radical);
      while (posicao !== -1) {
        var antes = alvo.slice(Math.max(0, posicao - 20), posicao);
        var numeros = antes.match(/\d+/g);
        if (numeros) {
          return parseInt(numeros[numeros.length - 1], 10);
        }
        posicao = alvo.indexOf(radical, posicao + 1);
      }
    }
    return 0;
  }

  function Material(container) {
    this.container = container;
    this.modal = container.closest('.modal');
    this.valor = container.querySelector('[data-eithra-material-valor]');
    this.lista = container.querySelector('[data-eithra-material-chips]');
    this.vazio = container.querySelector('[data-eithra-material-vazio]');
    this.busca = container.querySelector('[data-eithra-material-busca]');
    this.url = container.getAttribute('data-eithra-material-url');
    this.textoNome = container.getAttribute('data-eithra-material-texto') || '';
    this.condominio = container.getAttribute('data-eithra-material-condominio') || '';
    this.pedeLocal = container.hasAttribute('data-eithra-material-local');
    this.chips = [];
    this.tocado = false;
    this.carregando = false;
    this.ligar();
  }

  Material.prototype.ligar = function () {
    var self = this;
    var modal = this.modal;

    if (modal) {
      modal.addEventListener('show.bs.modal', function (evento) {
        var alvo = evento.relatedTarget;
        var condominio =
          self.condominio ||
          (alvo && alvo.getAttribute('data-eithra-material-condominio')) ||
          '';
        self.carregar(condominio);
      });
      modal.addEventListener('hidden.bs.modal', function () {
        self.reiniciar();
      });
    } else if (this.condominio) {
      this.carregar(this.condominio);
    }

    this.lista.addEventListener('click', function (evento) {
      var tirar = evento.target.closest('[data-eithra-chip-tirar]');
      if (tirar) {
        self.tirar(tirar.closest('[data-eithra-chip]'));
        return;
      }
      var somar = evento.target.closest('[data-eithra-chip-somar]');
      if (somar) {
        self.somar(somar.closest('[data-eithra-chip]'));
      }
    });

    if (this.busca) {
      this.busca.addEventListener('change', function () {
        self.adicionar(self.busca.value);
        self.busca.value = '';
      });
    }

    if (this.textoNome && modal) {
      var campo = modal.querySelector('[name="' + this.textoNome + '"]');
      if (campo) {
        var relogio = null;
        campo.addEventListener('input', function () {
          clearTimeout(relogio);
          relogio = setTimeout(function () {
            self.sugerir(campo.value);
          }, 300);
        });
      }
    }
  };

  Material.prototype.reiniciar = function () {
    this.chips = [];
    this.tocado = false;
    this.render();
  };

  Material.prototype.carregar = function (condominio) {
    var self = this;
    if (!this.url || !condominio || this.carregando) {
      if (!condominio) {
        this.chips = [];
        this.render();
      }
      return;
    }
    this.carregando = true;

    var endereco = this.url + '?condominio=' + encodeURIComponent(condominio);
    window
      .fetch(endereco, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (resposta) {
        return resposta.ok ? resposta.json() : { chips: [], itens: [] };
      })
      .then(function (dados) {
        self.tocado = false;
        self.chips = (dados.chips || []).map(function (chip) {
          return {
            pk: String(chip.pk),
            nome: chip.nome,
            qtd: Number(chip.quantidade) || 0,
          };
        });
        self.preencherBusca(dados.itens || []);
        self.preencherLocal(dados.locais || [], dados.ultimo_local || '');
        self.render();
      })
      .catch(function () {
        self.chips = [];
        self.render();
      })
      .then(function () {
        self.carregando = false;
      });
  };

  Material.prototype.preencherBusca = function (itens) {
    if (!this.busca) {
      return;
    }
    var escolhido = this.busca.value;
    this.busca.innerHTML = '<option value="">Buscar outro item…</option>';
    itens.forEach(function (item) {
      var opcao = document.createElement('option');
      opcao.value = String(item.pk);
      opcao.textContent = item.nome;
      this.busca.appendChild(opcao);
    }, this);
    this.busca.value = escolhido;
    this.busca.hidden = itens.length === 0;
  };

  Material.prototype.preencherLocal = function (locais, ultimo) {
    if (!this.pedeLocal || !this.modal) {
      return;
    }
    var select = this.modal.querySelector('[data-eithra-material-local]');
    if (!select) {
      return;
    }
    var campo = select.closest('[data-eithra-material-campo-local]');
    if (campo) {
      campo.hidden = locais.length === 0;
    }
    select.innerHTML = '<option value="">Sem local</option>';
    locais.forEach(function (local) {
      var opcao = document.createElement('option');
      opcao.value = String(local.pk);
      opcao.textContent = local.nome;
      select.appendChild(opcao);
    });
    select.value = ultimo || '';
  };

  Material.prototype.achar = function (pk) {
    for (var i = 0; i < this.chips.length; i += 1) {
      if (this.chips[i].pk === pk) {
        return this.chips[i];
      }
    }
    return null;
  };

  Material.prototype.somar = function (elemento) {
    var chip = elemento && this.achar(elemento.getAttribute('data-pk'));
    if (!chip) {
      return;
    }
    chip.qtd += 1;
    this.tocado = true;
    this.render();
  };

  Material.prototype.tirar = function (elemento) {
    var chip = elemento && this.achar(elemento.getAttribute('data-pk'));
    if (!chip) {
      return;
    }
    chip.qtd = 0;
    this.tocado = true;
    this.render();
  };

  Material.prototype.adicionar = function (pk) {
    if (!pk) {
      return;
    }
    var chip = this.achar(String(pk));
    if (!chip && this.busca) {
      var opcao = this.busca.querySelector('option[value="' + pk + '"]');
      if (opcao) {
        chip = { pk: String(pk), nome: opcao.textContent, qtd: 0 };
        this.chips.push(chip);
      }
    }
    if (!chip) {
      return;
    }
    chip.qtd += 1;
    this.tocado = true;
    this.render();
  };

  Material.prototype.sugerir = function (texto) {
    if (this.tocado) {
      return;
    }
    this.chips.forEach(function (chip) {
      chip.qtd = sugestao(chip.nome, texto);
    });
    this.render();
  };

  Material.prototype.render = function () {
    this.lista.querySelectorAll('[data-eithra-chip]').forEach(function (no) {
      no.remove();
    });
    this.vazio.hidden = this.chips.length !== 0;

    var marcados = [];
    this.chips.forEach(function (chip) {
      this.lista.appendChild(this.construir(chip));
      if (chip.qtd > 0) {
        marcados.push(chip.pk + ':' + chip.qtd);
      }
    }, this);

    this.valor.value = marcados.join(',');
  };

  Material.prototype.construir = function (chip) {
    var marca = document.createElement('span');
    marca.className = 'eithra-material-chip';
    marca.setAttribute('data-eithra-chip', '');
    marca.setAttribute('data-pk', chip.pk);
    if (chip.qtd > 0) {
      marca.classList.add('eithra-material-chip--marcado');
    }

    var somar = document.createElement('button');
    somar.type = 'button';
    somar.className = 'eithra-material-chip-somar';
    somar.setAttribute('data-eithra-chip-somar', '');
    somar.setAttribute('aria-label', 'Somar ' + chip.nome);
    somar.setAttribute('aria-pressed', chip.qtd > 0 ? 'true' : 'false');

    var nome = document.createElement('span');
    nome.className = 'eithra-material-chip-nome';
    nome.textContent = chip.nome;
    somar.appendChild(nome);

    var quantidade = document.createElement('span');
    quantidade.className = 'eithra-material-chip-qtd';
    quantidade.textContent = '×' + chip.qtd;
    quantidade.hidden = chip.qtd <= 0;
    somar.appendChild(quantidade);
    marca.appendChild(somar);

    var tirar = document.createElement('button');
    tirar.type = 'button';
    tirar.className = 'eithra-material-chip-tirar';
    tirar.setAttribute('data-eithra-chip-tirar', '');
    tirar.setAttribute('aria-label', 'Tirar ' + chip.nome);
    tirar.textContent = '×';
    tirar.hidden = chip.qtd <= 0;
    marca.appendChild(tirar);

    return marca;
  };

  function iniciar() {
    document.querySelectorAll('[data-eithra-material]').forEach(function (container) {
      if (!container.eithraMaterial) {
        container.eithraMaterial = new Material(container);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();

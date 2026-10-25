"""Contrato do que o Eithra entende ao ler um texto.

Camada 3 do núcleo de áudio — depois da transcrição fiel e antes de qualquer
registro. Este módulo é só o formato: ele não lê nada e não decide nada.

Quem lê é o domínio, e cada um responde pelas suas próprias regras:
``apps/portaria/interpretacao.py`` transforma texto em intenção operacional de
portaria; ``apps/demandas/interpretacao.py`` faz a classificação da demanda, que
já existia antes deste núcleo e continua sendo a única.

Existe para que a mesma leitura signifique a mesma coisa em qualquer domínio:
intenção principal, intenções secundárias, entidades com confiança, o que faltou
para decidir, o que perguntar e o que sugerir. Estrutura, e não JSON solto — o
que não tem contrato vira campo inventado na terceira integração.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import models


class Confianca(models.TextChoices):
    """O quanto a evidência sustenta o que foi entendido.

    Não é probabilidade e nunca aparece como número para quem usa o sistema:
    alta é o que o cadastro do condomínio confirma, média é o que a fala
    declara, e baixa é o que só se sustenta com a confirmação de quem lê.

    Espelha ``apps/demandas/derivacao.Confianca``. As duas nasceram separadas —
    uma dentro do domínio de Demandas, outra no núcleo —, e unificá-las mexeria
    em Demandas, que está fechado; a limpeza fica para uma rodada própria, como
    já acontece com ``apps/core/texto.py``.
    """

    ALTA = "alta", "Alta"
    MEDIA = "media", "Média"
    BAIXA = "baixa", "Baixa"


class Estado(models.TextChoices):
    """De onde veio o que foi entendido — a origem, separada da confiança.

    Confiança diz o quanto se sabe; estado diz onde se soube. Não são a mesma
    coisa, e por isso uma não substitui a outra: a fala pode declarar algo com
    toda a clareza do mundo e continuar sendo declaração, não fato. Só o
    cadastro do condomínio promove uma entidade a confirmada.
    """

    CONFIRMADA = "confirmada", "Confirmada"  # o cadastro do condomínio sustenta
    DECLARADA = "declarada", "Declarada"  # quem falou disse
    INFERIDA = "inferida", "Inferida"  # o sistema concluiu sozinho
    AMBIGUA = "ambigua", "Ambígua"  # mais de um cadastro responde por isto
    FALTANTE = "faltante", "Faltante"  # não veio


@dataclass(frozen=True)
class Entidade:
    """Um dado que a leitura reconheceu — de onde veio e com que firmeza.

    ``valor`` é o que a operação usa; ``rotulo`` é o que a pessoa lê ao
    confirmar. ``objeto`` é o registro do condomínio que confirma aquilo, quando
    existe: guardá-lo é o que separa "o cadastro diz" de "a fala disse". O
    primeiro é fato, o segundo é declaração — e é ``estado`` que nomeia qual dos
    dois, para que a confiança nunca precise fazer esse papel sozinha.
    """

    campo: str = ""
    valor: str = ""
    rotulo: str = ""
    objeto: object | None = None
    confianca: str = Confianca.BAIXA
    estado: str = Estado.DECLARADA
    motivo: str = ""

    @property
    def existe(self) -> bool:
        return bool(self.valor or self.rotulo)

    @property
    def texto(self) -> str:
        """Como a entidade aparece para quem confirma."""
        return self.rotulo or self.valor

    @property
    def confirmada(self) -> bool:
        """Se o cadastro sustenta isto, e não apenas a fala."""
        return self.estado == Estado.CONFIRMADA


@dataclass(frozen=True)
class Intencao:
    """O que o texto quer dizer — sem ter feito nada ainda.

    ``tipo`` é a intenção principal; ``secundarias`` guarda o que a mesma fala
    também pediu (uma chegada que traz junto um relato de problema, por
    exemplo). Nada aqui executa: ``acoes`` são sugestões a mostrar, e a execução
    só acontece depois que a pessoa confirma.
    """

    tipo: str = ""
    confianca: str = Confianca.BAIXA
    resumo: str = ""
    entidades: dict[str, Entidade] = field(default_factory=dict)
    secundarias: tuple[str, ...] = ()
    faltantes: tuple[str, ...] = ()
    perguntas: tuple[str, ...] = ()
    acoes: tuple[str, ...] = ()

    @property
    def entendeu(self) -> bool:
        """Se chegou a alguma intenção — ou só devolveu dúvida."""
        return bool(self.tipo)

    def entidade(self, campo: str) -> Entidade | None:
        return self.entidades.get(campo)

    def valor(self, campo: str) -> str:
        entidade = self.entidades.get(campo)
        return entidade.valor if entidade else ""

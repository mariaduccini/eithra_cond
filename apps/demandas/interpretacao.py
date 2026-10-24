"""A leitura do relato com as três fontes, na ordem em que elas pesam.

O relato é lido primeiro pelas regras: determinísticas, sempre disponíveis, e
é delas que saem o assunto, a prioridade e o local. O cadastro do condomínio
confirma ou recusa o que aparece — equipamento e local só existem se estiverem
cadastrados ali. O histórico mostra o que já aconteceu no mesmo ponto.

Quando essas três não alcançam — a cena descrita sem o nome técnico, o
equipamento chamado pelo jeito de falar e não pelo nome do cadastro —, e só
então, entra a leitura semântica. Ela propõe; o que ela propõe é conferido
contra o mesmo cadastro e o mesmo vocabulário antes de virar sugestão. Proposta
que não cabe é descartada em silêncio: melhor não sugerir do que sugerir um
equipamento que não existe.

Três compromissos atravessam este módulo:

* **o que o relato disse não é reescrito.** A leitura semântica só preenche
  campo que a leitura determinística deixou em aberto.
* **proposta não é fato.** O que vem da leitura semântica sai como sugestão a
  confirmar; o que o cadastro confirma pode ser preenchido direto.
* **o silêncio é resposta.** Sem base, o campo fica vazio e a pergunta vai para
  a tela — uma de cada vez, e só quando responder muda a classificação.
"""
from __future__ import annotations

from dataclasses import replace

from apps.core import llm
from apps.cadastros.models import Equipamento, LocalOperacional

from . import derivacao
from .constants import (
    ImpactoDemanda,
    RecorrenciaDemanda,
    RiscoDemanda,
    TipoProblema,
)

# Os campos que a leitura semântica pode propor. Prioridade não está aqui: ela
# é regra do domínio, e um serviço de linguagem não opina sobre peso.
VOCABULARIO = {
    "tipo_problema": TipoProblema,
    "impacto": ImpactoDemanda,
    "risco": RiscoDemanda,
    "recorrencia": RecorrenciaDemanda,
}

# O que é aceito de cada campo. A recorrência vem com uma porta só: "observada"
# é o que o histórico mostra, e o histórico não é do serviço — ele só pode
# perceber que o relato conta de algo que se repete.
VALORES_PERMITIDOS = {
    "tipo_problema": set(TipoProblema.values),
    "impacto": set(ImpactoDemanda.values),
    "risco": set(RiscoDemanda.values),
    "recorrencia": {RecorrenciaDemanda.DECLARADA},
}

# O porquê que acompanha toda proposta vinda da leitura semântica. É uma frase
# honesta e fechada: o raciocínio do serviço não é mostrado, e motivo inventado
# por ele também não.
MOTIVO_DA_LEITURA = "Interpretado a partir do relato."

INSTRUCAO = (
    "Você ajuda a classificar o relato de um problema de condomínio para uso "
    "interno do sistema Eithra.\n"
    "\n"
    "Responda SOMENTE com um objeto JSON, sem texto em volta, com exatamente "
    "estas chaves:\n"
    '{"local": "", "equipamento": "", "tipo_problema": "", "impacto": "", '
    '"risco": "", "recorrencia": ""}\n'
    "\n"
    "Regras, sem exceção:\n"
    "- Use apenas os valores listados para cada campo.\n"
    "- Sem certeza, deixe a string vazia. Vazio é uma resposta correta.\n"
    "- Nunca invente nome de local ou de equipamento: copie exatamente como "
    "aparece na lista do condomínio.\n"
    "- Não explique, não comente, não numere, não use bloco de código."
)


def interpretar(relato, *, condominio=None, hoje=None, ignorar=None) -> derivacao.Leitura:
    """Lê o relato e devolve o que ele sustenta, com o apoio que houver.

    A assinatura é a da leitura determinística porque, para quem chama, existe
    uma leitura só: o que muda é ela saber pedir ajuda quando precisa. Sem
    serviço configurado — ou quando ele não responde —, o resultado é
    exatamente o da leitura por regras.
    """
    leitura = derivacao.interpretar(
        relato, condominio=condominio, hoje=hoje, ignorar=ignorar
    )
    if not _vale_consultar(leitura):
        return leitura

    resposta = llm.pedir_json(
        sistema=INSTRUCAO, pedido=_pedido(relato, leitura, condominio)
    )
    if not resposta:
        return leitura
    return _aplicar(relato, leitura, resposta, condominio)


def _vale_consultar(leitura) -> bool:
    """Se ainda sobra algo que a leitura semântica poderia resolver.

    Consultar quando tudo já está classificado seria pagar por uma resposta que
    não muda nada. Quando nada está em aberto, o silêncio é a resposta certa.
    """
    return not all(
        (
            leitura.equipamento,
            leitura.tipo_problema.existe,
            leitura.impacto.existe,
            leitura.risco.existe,
            leitura.recorrencia.existe,
        )
    )


def _texto(resposta, campo: str) -> str:
    """O valor de um campo na resposta, quando ele veio como texto útil."""
    valor = resposta.get(campo)
    return valor.strip() if isinstance(valor, str) else ""


def _valores(campo: str) -> list[str]:
    """Os valores aceitos de um campo, na ordem em que o domínio os declara."""
    escolhas = VOCABULARIO[campo]
    return [
        valor for valor in escolhas.values if valor in VALORES_PERMITIDOS[campo]
    ]


def _cadastro(condominio) -> tuple[list[str], list[str]]:
    """Os nomes de locais e equipamentos do condomínio, como o serviço os vê.

    É esta lista que permite ao serviço reconhecer "o portão da garagem" como o
    equipamento cadastrado com outro nome — e é ela também que impede que ele
    invente um.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return [], []
    locais = [
        local.nome
        for local in LocalOperacional.objects.do_condominio(condominio).ativos()
    ]
    equipamentos = [
        equipamento.nome
        for equipamento in Equipamento.objects.do_condominio(condominio).ativos()
    ]
    return locais, equipamentos


def _pedido(relato, leitura, condominio) -> str:
    """O que se pede ao serviço: o relato, o cadastro e o vocabulário.

    O que as regras já leram com segurança vai junto, marcado como tal — não
    para o serviço repetir, mas para que ele não contrarie o que já está firme
    sem motivo.
    """
    locais, equipamentos = _cadastro(condominio)
    linhas = [f"Relato: {(relato or '').strip()}"]
    linhas.append(
        "Locais cadastrados neste condomínio: "
        + (", ".join(locais) if locais else "nenhum")
    )
    linhas.append(
        "Equipamentos cadastrados neste condomínio: "
        + (", ".join(equipamentos) if equipamentos else "nenhum")
    )
    for campo in VOCABULARIO:
        rotulos = ", ".join(
            f"{valor} = {VOCABULARIO[campo](valor).label}"
            for valor in _valores(campo)
        )
        linhas.append(f"{campo} (valores aceitos: {rotulos})")

    firmes = [
        f"{campo}: {sugestao.valor}"
        for campo, sugestao in leitura.sugestoes.items()
        if sugestao.existe
    ]
    linhas.append(
        "Já lido com segurança no relato: "
        + ("; ".join(firmes) if firmes else "nada")
    )
    return "\n".join(linhas)


def _sugestao_do_vocabulario(resposta, campo: str) -> derivacao.Sugestao:
    """A proposta do serviço, quando ela cabe no vocabulário do domínio.

    O serviço responde em texto livre; o que não for um valor conhecido é
    descartado sem alarde. Vocabulário pequeno não é limitação imposta a ele —
    é a garantia de que nada entra na classificação sem ser verificável.
    """
    valor = _texto(resposta, campo)
    if valor not in VALORES_PERMITIDOS[campo]:
        return derivacao.Sugestao()
    return derivacao.Sugestao(
        valor=valor,
        origem=derivacao.OrigemSugestao.EITHRA,
        confianca=derivacao.Confianca.MEDIA,
        motivo=MOTIVO_DA_LEITURA,
    )


def _aplicar(relato, leitura, resposta, condominio) -> derivacao.Leitura:
    """A leitura determinística com o que a leitura semântica acrescentou.

    A proposta de local e de equipamento entra pelo mesmo caminho que uma
    menção literal: é o cadastro que decide se ela vale, e é ele que decide com
    que confiança. Assim, um nome que não corresponde a nada cadastrado não
    vira sugestão — vira nada.
    """
    equipamento = leitura.equipamento
    if equipamento is None:
        equipamento, _ = derivacao.equipamento_do_relato(
            _texto(resposta, "equipamento"), condominio
        )

    local = leitura.local
    origem_do_local = leitura.origem_do_local
    if local is None:
        local = derivacao.local_do_relato(_texto(resposta, "local"), condominio)
        if local is not None:
            origem_do_local = derivacao.OrigemSugestao.EITHRA

    # A área nunca vem do serviço: ela é derivada do cadastro, e o cadastro
    # continua sendo a única fonte dela.
    area = leitura.area
    if not area.existe:
        area = derivacao.area_do_contexto(local, equipamento)

    refinada = replace(
        leitura,
        local=local,
        origem_do_local=origem_do_local,
        equipamento=equipamento,
        area=area,
        tipo_problema=leitura.tipo_problema
        if leitura.tipo_problema.existe
        else _sugestao_do_vocabulario(resposta, "tipo_problema"),
        impacto=leitura.impacto
        if leitura.impacto.existe
        else _sugestao_do_vocabulario(resposta, "impacto"),
        risco=leitura.risco
        if leitura.risco.existe
        else _sugestao_do_vocabulario(resposta, "risco"),
        recorrencia=leitura.recorrencia
        if leitura.recorrencia.existe
        else _sugestao_do_vocabulario(resposta, "recorrencia"),
    )
    # A pergunta é refeita depois: o que o serviço identificou pode ter
    # resolvido justamente a ambiguidade que ela vinha cobrar.
    return replace(
        refinada,
        pergunta=derivacao.pergunta_do_relato(
            relato,
            condominio,
            equipamento=refinada.equipamento,
            tipo_problema=refinada.tipo_problema.valor,
        ),
    )

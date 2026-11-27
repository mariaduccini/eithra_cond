"""A lista curta do que ficou por classificar, com uma sugestão ao lado.

A leitura classifica o que as palavras alcançam. O que sobra — ``A classificar``
e ``Outros`` — é justamente o que a gestão precisa olhar. Este módulo monta
essa lista curta e propõe uma categoria para cada linha: primeiro a regra que o
condomínio já aprendeu (ou o contrato do fornecedor), depois a sugestão da IA,
quando há uma configurada.

Uma regra governa tudo aqui: **a sugestão nunca grava nada.** Ela é uma
proposta marcada como tal, e quem responde pela classificação continua sendo a
gestão — a confirmação é que vira regra, e só ela. Nenhum valor é lido nem
alterado; a categoria e o sistema são a única coisa em jogo.
"""
from __future__ import annotations

from apps.balancos import correcoes
from apps.balancos.categorias import CategoriaEithra, categoria_para, rotulo

# As categorias que significam "ainda não sei": o que a leitura não alcançou e o
# que ela jogou em "Outros". São essas linhas que a tela põe em fila.
CATEGORIAS_EM_ABERTO = (CategoriaEithra.NAO_CLASSIFICADO, CategoriaEithra.OUTROS)

ORIGEM_REGRA = "regra"
ORIGEM_IA = "ia"

# O que se pede à IA: uma categoria da lista fechada do Eithra, e só.
SISTEMA_DA_IA = (
    "Você classifica despesas de condomínio em uma lista fechada de categorias. "
    "Responda apenas com um objeto JSON no formato {\"categoria\": \"codigo\"}, "
    "usando um dos códigos que o pedido lista."
)


def linhas_a_classificar(leitura: dict, *, condominio=None) -> list[dict]:
    """As linhas em aberto, cada uma com a sugestão e a origem dela.

    Devolve pouca coisa por linha — o que a tela precisa para pedir a decisão
    em um toque: identificador, descrição, valor, grupo, a categoria de hoje e a
    sugestão. Nada é gravado: quem decide é a gestão.
    """
    linhas = []
    for item in correcoes.despesas_efetivas(leitura):
        if item["categoria_eithra"] not in CATEGORIAS_EM_ABERTO:
            continue

        sugestao, origem = _sugestao(item, condominio=condominio)
        linhas.append(
            {
                "id": item["id"],
                "descricao": item["descricao"],
                "valor": item["valor"],
                "grupo": item["grupo"] or "",
                "categoria": rotulo(item["categoria_eithra"]),
                "sugestao": sugestao,
                "sugestao_rotulo": rotulo(sugestao) if sugestao else "",
                "sugestao_origem": origem,
            }
        )
    return linhas


def _sugestao(item: dict, *, condominio) -> tuple[str, str]:
    """Propõe uma categoria para a linha, sem gravar nada.

    A regra vem antes da IA: o que o condomínio já ensinou é mais confiável do
    que uma leitura semântica. A IA só entra quando há uma configurada e quando
    ela responde algo que cabe no catálogo. Sem sugestão, a resposta é vazia — e
    vazio é resposta honesta.
    """
    deterministica = categoria_para(
        condominio,
        descricao=item["descricao"],
        categoria_original=item["grupo"],
        fornecedor=item.get("fornecedor"),
    )
    if deterministica not in CATEGORIAS_EM_ABERTO:
        return deterministica, ORIGEM_REGRA

    da_ia = _sugestao_da_ia(item["descricao"])
    if da_ia:
        return da_ia, ORIGEM_IA
    return "", ""


def _sugestao_da_ia(descricao: str) -> str:
    """Pede uma categoria à IA, quando há uma disponível. Nunca levanta.

    A resposta é aceita só se for um código do catálogo e se não for uma das
    categorias em aberto — sugerir "A classificar" não sugere nada. Qualquer
    falha termina em string vazia: a ausência de sugestão não pode impedir a
    tela de funcionar.
    """
    from apps.core import llm

    opcoes = [categoria.value for categoria in CategoriaEithra if categoria not in CATEGORIAS_EM_ABERTO]
    if not opcoes:
        return ""
    resposta = llm.pedir_json(
        sistema=SISTEMA_DA_IA,
        pedido=(
            f"Descrição da despesa: {descricao}\n"
            f"Categorias possíveis (use o código): {', '.join(opcoes)}"
        ),
    )
    if not resposta:
        return ""
    escolhida = str(resposta.get("categoria", "") or "").strip()
    return escolhida if escolhida in opcoes else ""

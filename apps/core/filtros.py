"""O que a tela precisa saber sobre os filtros de uma listagem.

Filtrar é opcional, e a lista só oferece "Limpar filtros" quando há algo a
limpar. O que conta é se algum filtro está em uso: o número da página não é
filtro, e voltar uma escolha para o valor vazio também não é.
"""


def tem_filtros(filtros) -> bool:
    """Diz se algum filtro da listagem está em uso.

    A lista de trabalho nunca exige filtro, então a resposta é "não" quando o
    formulário nem chegou preenchido — e, quando chegou, basta que uma escolha
    tenha sobrado com valor: as vazias ficam de fora da conta.
    """
    return filtros.is_valid() and any(filtros.cleaned_data.values())

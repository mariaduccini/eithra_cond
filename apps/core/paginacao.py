"""A paginação das listagens operacionais, num lugar só.

Uma lista de trabalho não é um arquivo: ela serve para encontrar e agir. Quando
o volume cresce, mostrar tudo de uma vez deixa de ajudar e passa a esconder —
antes, o que passava do limite simplesmente desaparecia da tela sem avisar.

A regra é sempre a mesma e vale para toda listagem: primeiro o conjunto é
reduzido (condomínio, permissões, busca e filtros), depois ordenado, e só então
recortado em páginas. Paginar antes de filtrar daria páginas de um conjunto que
não é o que a pessoa pediu.

O tamanho da página é escolha de quem está olhando, não do código: as listagens
aceitam os mesmos três valores, e quem não escolheu nada recebe o padrão. Ler
essa escolha é a única coisa que cada tela precisaria repetir — e é justamente
por isso que ela mora aqui.
"""
from django.core.paginator import Paginator

# O tamanho da página que a listagem usa quando ninguém escolheu outro. É um
# número só, para a pessoa não reaprender a tela a cada módulo.
TAMANHO_DA_PAGINA = 20

# Os tamanhos que qualquer listagem aceita. São poucos de propósito: uma lista
# longa é para varrer, não para rolar.
TAMANHOS_DA_PAGINA = (20, 50, 100)


def numero_da_pagina(pedido, padrao=1):
    """A página pedida, como número, ou o padrão quando não dá para ler.

    A leitura é tolerante de propósito: quem chega por um endereço antigo, com
    o número escrito à mão ou já apontando para uma página que encolheu, não
    pode ver erro — vê uma página válida. O recorte final ainda passa pelo
    paginador, que corrige o que ficou fora do alcance.
    """
    bruto = (pedido or {}).get("page")
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return padrao


def tamanho_da_pagina(pedido, padrao=TAMANHO_DA_PAGINA) -> int:
    """O tamanho da página escolhido, ou o padrão quando não é um dos aceitos.

    Um valor fora da lista não é recusado com erro: é simplesmente ignorado.
    Endereço escrito à mão não pode virar tela quebrada, e o padrão é sempre uma
    resposta honesta.
    """
    bruto = (pedido or {}).get("per_page")
    try:
        escolhido = int(bruto)
    except (TypeError, ValueError):
        return padrao
    return escolhido if escolhido in TAMANHOS_DA_PAGINA else padrao


def paginar(queryset, numero, tamanho=TAMANHO_DA_PAGINA):
    """Recorta o conjunto já filtrado e ordenado na página pedida.

    Devolve a página do próprio Django com "faixa" a mais: os números de página
    a mostrar, resumidos por reticências quando são muitos — o template não
    consegue pedir isso ao paginador, porque o Django não deixa chamar método
    com argumento de dentro do template.

    A lista da tela continua sendo o que sempre foi: a view entrega ao contexto
    a coleção que a tela lê, e a página em "pagina_<nome>", só para a navegação.
    """
    paginador = Paginator(queryset, tamanho)
    pagina = paginador.get_page(numero)
    pagina.faixa = list(paginador.get_elided_page_range(pagina.number))
    return pagina


def paginar_do_pedido(queryset, pedido, padrao=TAMANHO_DA_PAGINA):
    """Recorta o conjunto lendo página e tamanho do que veio no endereço.

    É o que as listagens chamam: elas não precisam saber como se lê "page" nem
    quais tamanhos existem — só entregar o conjunto já filtrado e o pedido.

    A página sai com "tamanho" e "tamanhos" a mais, para o rodapé poder mostrar
    o que está em vigor e oferecer a troca sem que a view monte essa lista.
    """
    tamanho = tamanho_da_pagina(pedido, padrao)
    pagina = paginar(queryset, numero_da_pagina(pedido), tamanho)
    pagina.tamanho = tamanho
    pagina.tamanhos = TAMANHOS_DA_PAGINA
    return pagina

"""A paginação das listagens operacionais, num lugar só.

Uma lista de trabalho não é um arquivo: ela serve para encontrar e agir. Quando
o volume cresce, mostrar tudo de uma vez deixa de ajudar e passa a esconder —
antes, o que passava do limite simplesmente desaparecia da tela sem avisar.

A regra é sempre a mesma e vale para toda listagem: primeiro o conjunto é
reduzido (condomínio, permissões, busca e filtros), depois ordenado, e só então
recortado em páginas. Paginar antes de filtrar daria páginas de um conjunto que
não é o que a pessoa pediu.
"""
from django.core.paginator import Paginator

# O tamanho da página vale para as listagens inteiras. É um número só, para a
# pessoa não reaprender a tela a cada módulo.
TAMANHO_DA_PAGINA = 20


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

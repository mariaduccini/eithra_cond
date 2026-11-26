"""Onde cada lugar do condomínio fica no desenho do prédio.

O prédio tem níveis: a cobertura, os andares dúplex, os andares baixos, o
playground e as quatro garagens. Cada local cadastrado pertence a um deles, e
quem diz isso é o nome que a gestão já usa no dia a dia — "Cobertura · salão",
"10º andar", "3º andar", "G4 · Casa de bombas de recalque". Nada aqui exige
campo novo: o nome basta, e o que não se encaixa em nenhum nível é do prédio
inteiro — a torre, as fachadas, o depósito, as vagas.

Este módulo responde a uma pergunta só — a que nível este lugar pertence — e
mais nenhuma. O que fazer com a resposta é da leitura do prédio.
"""
import re
import unicodedata

# Os níveis desenhados no prédio, na ordem em que se olha de cima para baixo.
COBERTURA = "cobertura"
DUPLEX = "duplex"
BAIXOS = "baixos"
PLAYGROUND = "playground"
G1 = "g1"
G2 = "g2"
G3 = "g3"
G4 = "g4"
# O que não pertence a um nível é do prédio inteiro: torre, fachadas, depósito.
PREDIO = "predio"

NIVEIS = (COBERTURA, DUPLEX, BAIXOS, PLAYGROUND, G1, G2, G3, G4, PREDIO)

# Os andares que compõem cada bloco: do 1º ao 8º são os baixos, do 9º ao 16º o
# dúplex. Fora dessa faixa o andar não é nenhum dos dois — e o local fica no
# prédio inteiro, porque o desenho não tem onde mostrá-lo.
PRIMEIRO_ANDAR_DOS_BAIXOS = 1
ULTIMO_ANDAR_DOS_BAIXOS = 8
PRIMEIRO_ANDAR_DO_DUPLEX = 9
ULTIMO_ANDAR_DO_DUPLEX = 16

# O andar escrito como se escreve no prédio: "10º andar", "3° andar".
_ANDAR = re.compile(r"^(\d{1,2})\s*[ºªo°]")
# A garagem escrita como a gestão a chama: "G4 · Casa de bombas", "G1 (rua)".
_GARAGEM = re.compile(r"^g\s*([1-4])\b")


def _sem_acento(texto: str) -> str:
    """A mesma palavra sem acento: é o que deixa a comparação previsível."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return decomposto.encode("ascii", "ignore").decode("ascii")


def _normalizado(nome) -> str:
    """O nome do local pronto para ser comparado: sem acento, minúsculo."""
    return _sem_acento((nome or "").strip()).lower()


def nivel_do_local(nome) -> str:
    """Diz a que nível do prédio este lugar pertence.

    A resposta vem do nome, na ordem em que o prédio se apresenta: a cobertura
    primeiro, as garagens em seguida, depois o playground e, por fim, os
    andares. O que não se encaixa em nada é do prédio inteiro — e é isso que
    mantém os lugares que não têm nível visíveis, em vez de escondidos.
    """
    texto = _normalizado(nome)
    if not texto:
        return PREDIO

    if texto.startswith("cobertura"):
        return COBERTURA

    garagem = _GARAGEM.match(texto)
    if garagem:
        return f"g{garagem.group(1)}"

    if "playground" in texto:
        return PLAYGROUND

    andar = _ANDAR.match(texto)
    if andar:
        numero = int(andar.group(1))
        if PRIMEIRO_ANDAR_DO_DUPLEX <= numero <= ULTIMO_ANDAR_DO_DUPLEX:
            return DUPLEX
        if PRIMEIRO_ANDAR_DOS_BAIXOS <= numero <= ULTIMO_ANDAR_DOS_BAIXOS:
            return BAIXOS

    return PREDIO

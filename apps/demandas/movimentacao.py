"""A leitura do relato novo de uma demanda que já existe.

Registrar uma atualização é contar o que aconteceu com uma demanda em
andamento. Às vezes essa frase carrega uma consequência operacional — o serviço
foi concluído, o fornecedor prometeu voltar — e às vezes ela é só o fato.

Esta leitura devolve apenas o que o próprio relato sustenta. Ela não completa
lacuna nenhuma: sem data escrita no relato não há previsão, sem empresa
reconhecida no cadastro do condomínio não há fornecedor, sem frase de
encerramento não há conclusão. Nada aqui é aplicado à demanda — a leitura
propõe, a tela mostra, e só a confirmação de quem registrou chama o serviço
oficial do domínio.

Por isso esta leitura é a mesma para o relato digitado e para o relato ditado:
o que chega aqui é texto, e a fala só produz texto.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

from apps.cadastros.models import Fornecedor

from .derivacao import _contem, _normalizado

# Frases que dizem, com todas as letras, que o serviço acabou. Nenhuma delas
# vale sozinha: perto de uma negação, elas dizem o contrário — "ainda não
# concluído" é o oposto de "concluído".
FRASES_DE_CONCLUSAO = (
    "concluido",
    "concluida",
    "concluidos",
    "concluidas",
    "resolvido",
    "resolvida",
    "resolvidos",
    "resolvidas",
    "finalizado",
    "finalizada",
    "finalizados",
    "finalizadas",
    "consertado",
    "consertada",
    "consertados",
    "consertadas",
    "reparado",
    "reparada",
    "reparados",
    "reparadas",
    "substituido",
    "substituida",
    "substituidos",
    "substituidas",
    "testado e funcionando",
    "testada e funcionando",
    "funcionando normalmente",
    "voltou a funcionar",
    "esta funcionando",
    "esta operando",
    "servico feito",
    "servico executado",
    "servico realizado",
    "problema resolvido",
)

# Palavras que, logo antes da frase, invertem o que ela diz.
PALAVRAS_DE_NEGACAO = ("nao", "nenhum", "nenhuma")

# O que anuncia algo que ainda vai acontecer. É o que separa "virá amanhã" de
# "concluído hoje": a data só vira previsão quando o relato diz que o fato
# ainda está por vir.
SINAIS_DE_PREVISAO = (
    "vira",
    "vem",
    "volta",
    "voltam",
    "retorna",
    "retornam",
    "chega",
    "chegam",
    "passa",
    "passam",
    "atende",
    "atendem",
    "agendou",
    "agendado",
    "agendada",
    "marcou",
    "marcado",
    "marcada",
    "previsto",
    "prevista",
    "prometeu",
    "prometido",
    "prometida",
    "combinou",
    "combinado",
    "combinada",
    "fica de",
    "ficou de",
    "informou que",
    "avisou que",
)

# As expressões relativas mais diretas. A ordem importa: "depois de amanhã"
# contém "amanhã", e a leitura fica com a primeira que casar.
DIAS_RELATIVOS = (
    ("depois de amanha", 2),
    ("amanha", 1),
    ("hoje", 0),
)

# O dia da semana dito pelo nome, do jeito que a gestão fala.
DIAS_DA_SEMANA = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
}

_DATA_ESCRITA = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_HORA_INTEIRA = re.compile(r"\b(\d{1,2})\s*(?:h|horas)\b")
_HORA_COM_MINUTOS = re.compile(r"\b(\d{1,2}):(\d{2})\b")

# Palavras que aparecem em nome de empresa e em qualquer relato. Casar por elas
# seria adivinhar quem falou: o fornecedor só é reconhecido por um nome que
# identifique alguma coisa.
PALAVRAS_GENERICAS = (
    "elevador",
    "elevadores",
    "servico",
    "servicos",
    "comercio",
    "manutencao",
    "instalacao",
    "instalacoes",
    "tecnica",
    "tecnico",
    "tecnologia",
    "engenharia",
    "solucoes",
    "sistema",
    "sistemas",
    "grupo",
    "construtora",
    "empreendimentos",
    "administradora",
    "consultoria",
    "assistencia",
    "ltda",
    "eireli",
    "epp",
    "me",
    "sa",
    "cia",
)

MINIMO_DA_PALAVRA = 4


@dataclass(frozen=True)
class Previsao:
    """A data que o relato prometeu, com a hora quando ela também foi dita."""

    data: date
    hora: str = ""

    def por_extenso(self) -> str:
        """Como a gestão lê a previsão: "30/10 às 14h"."""
        if not self.hora:
            return self.data.strftime("%d/%m")
        return f"{self.data.strftime('%d/%m')} às {self.hora}"


@dataclass(frozen=True)
class Movimentacao:
    """O que um relato novo sobre uma demanda que já existe sustenta.

    Vazia é uma resposta legítima: um relato que só conta um fato não propõe
    consequência nenhuma, e a movimentação registra apenas o que foi dito.
    """

    texto: str = ""
    conclusao: bool = False
    previsao: Previsao | None = None
    fornecedor: str = ""

    @property
    def identificado(self):
        """O que a tela destaca como identificado, em linguagem humana.

        Só entra o que existe: o que o relato não disse não vira linha vazia.
        """
        itens = []
        if self.fornecedor:
            itens.append(f"Fornecedor reconhecido no cadastro: {self.fornecedor}")
        if self.previsao is not None:
            itens.append(f"Previsão informada: {self.previsao.por_extenso()}")
        if self.conclusao:
            itens.append("O serviço foi dado como concluído.")
        return itens


def ler(texto: str, *, demanda=None, hoje: date | None = None) -> Movimentacao:
    """Lê o relato novo e devolve só o que ele sustenta.

    ``demanda`` entra por uma razão só: reconhecer o fornecedor exige o cadastro
    do condomínio onde a demanda vive. Sem relato, a leitura é vazia, e a
    movimentação registra apenas o fato — nunca o que ninguém escreveu.
    """
    limpo = _normalizado(texto)
    if not limpo:
        return Movimentacao()

    referencia = hoje or timezone.localdate()
    return Movimentacao(
        texto=texto,
        conclusao=_concluido(limpo),
        previsao=_previsao(limpo, referencia),
        fornecedor=_fornecedor_do_relato(limpo, demanda),
    )


def _concluido(texto_normalizado: str) -> bool:
    """Se o relato afirma o encerramento — e não o contrário dele."""
    for frase in FRASES_DE_CONCLUSAO:
        for encontro in re.finditer(
            rf"\b{re.escape(frase)}s?\b", texto_normalizado
        ):
            antes = texto_normalizado[: encontro.start()].split()
            if any(palavra in PALAVRAS_DE_NEGACAO for palavra in antes[-2:]):
                continue
            return True
    return False


def _previsao(texto_normalizado: str, hoje: date) -> Previsao | None:
    """A data que o relato promete, quando ele promete alguma coisa.

    Sem sinal de coisa por vir, uma data no relato é só a data de um fato já
    acontecido — "concluído hoje" não é previsão de nada.
    """
    if not _contem(texto_normalizado, SINAIS_DE_PREVISAO):
        return None

    data = _data_escrita(texto_normalizado, hoje)
    if data is None:
        data = _data_relativa(texto_normalizado, hoje)
    if data is None:
        return None
    return Previsao(data=data, hora=_hora(texto_normalizado))


def _data_escrita(texto_normalizado: str, hoje: date) -> date | None:
    """A data em números, quando ela foi escrita assim no relato."""
    encontro = _DATA_ESCRITA.search(texto_normalizado)
    if not encontro:
        return None

    dia, mes, ano = encontro.groups()
    try:
        if ano:
            numero = int(ano)
            return date(numero + 2000 if numero < 100 else numero, int(mes), int(dia))
        data = date(hoje.year, int(mes), int(dia))
    except ValueError:
        return None

    # Data escrita sem ano que já passou é a do ano que vem: quem escreve
    # "dia 05/01" em dezembro está falando de janeiro.
    if data < hoje:
        try:
            data = data.replace(year=data.year + 1)
        except ValueError:
            return None
    return data


def _data_relativa(texto_normalizado: str, hoje: date) -> date | None:
    """A data dita por extenso, quando ela foi dita assim no relato."""
    for expressao, dias in DIAS_RELATIVOS:
        if _contem(texto_normalizado, (expressao,)):
            return hoje + timedelta(days=dias)

    for nome, indice in DIAS_DA_SEMANA.items():
        if _contem(texto_normalizado, (nome,)):
            adiante = (indice - hoje.weekday()) % 7
            return hoje + timedelta(days=adiante or 7)
    return None


def _hora(texto_normalizado: str) -> str:
    """A hora dita no relato, como a gestão a lê — ou nada, quando não há."""
    encontro = _HORA_COM_MINUTOS.search(texto_normalizado)
    if encontro:
        hora, minuto = int(encontro.group(1)), int(encontro.group(2))
        if hora <= 23 and minuto <= 59:
            return f"{hora}h" if minuto == 0 else f"{hora}h{minuto:02d}"

    encontro = _HORA_INTEIRA.search(texto_normalizado)
    if encontro and int(encontro.group(1)) <= 23:
        return f"{encontro.group(1)}h"
    return ""


def _nome_do_fornecedor(fornecedor) -> str:
    """O nome pelo qual o fornecedor é reconhecido no condomínio."""
    return fornecedor.nome_fantasia or fornecedor.nome_razao_social


def _palavras_que_identificam(nome: str) -> list[str]:
    """As palavras do nome que podem reconhecer o fornecedor no relato.

    O nome inteiro primeiro; depois cada palavra que identifique alguma coisa —
    "elevadores", "serviços" e "ltda" não identificam ninguém.
    """
    normalizado = _normalizado(nome)
    if not normalizado:
        return []
    return [normalizado] + [
        palavra
        for palavra in normalizado.split()
        if len(palavra) >= MINIMO_DA_PALAVRA and palavra not in PALAVRAS_GENERICAS
    ]


def _fornecedor_do_relato(texto_normalizado: str, demanda) -> str:
    """O nome do fornecedor do condomínio que o relato cita, quando só um cabe.

    Dois candidatos não são evidência: se mais de um fornecedor do cadastro
    caísse no relato, a leitura não escolhe nenhum — quem sabe quem falou é
    quem escreveu. Fornecedor fora do cadastro do condomínio também não entra:
    citar uma empresa não a transforma em cadastro.
    """
    condominio = getattr(demanda, "condominio", None)
    if condominio is None:
        return ""

    encontrados = set()
    for fornecedor in Fornecedor.objects.ativos().do_condominio(condominio):
        nome = _nome_do_fornecedor(fornecedor)
        if any(
            _contem(texto_normalizado, (palavra,))
            for palavra in _palavras_que_identificam(nome)
        ):
            encontrados.add(nome)

    return encontrados.pop() if len(encontrados) == 1 else ""

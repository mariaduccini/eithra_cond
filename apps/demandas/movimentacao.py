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

from .constants import AguardandoTipo
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

# O que diz, com todas as letras, que a demanda está esperando alguém. Sem uma
# dessas — ou sem algo prometido para depois —, um nome citado é só um nome
# citado: não se inventa espera onde o relato não declarou nenhuma.
SINAIS_DE_ESPERA = (
    "aguardando",
    "aguardo",
    "aguardamos",
    "no aguardo",
    "esperando",
    "espero",
    "esperamos",
    "sem resposta",
    "nao respondeu",
    "nao responderam",
    "nao retornou",
    "nao retornaram",
    "pendente",
    "pendentes",
    "nao recebi",
    "nao recebemos",
    "nao chegou",
    "nao chegaram",
    "prazo de entrega",
    "cobrando",
    "cobrei",
    "vou cobrar",
)

# O que a demanda espera, na ordem em que se decide. A primeira que casar
# responde, e a ordem é a leitura: quem espera o técnico espera o fornecedor,
# mesmo citando "peça" no mesmo relato.
ESPERA_POR_FORNECEDOR = (
    "fornecedor",
    "fornecedores",
    "empresa",
    "empresas",
    "tecnico",
    "tecnicos",
    "orcamento",
    "orcamentos",
    "proposta",
    "assistencia",
    "prestador",
    "visita",
)

CLASSIFICACAO_DA_ESPERA = (
    (
        AguardandoTipo.MORADOR,
        (
            "morador",
            "moradores",
            "condomino",
            "condominos",
            "proprietario",
            "proprietarios",
            "inquilino",
            "inquilinos",
        ),
    ),
    (
        AguardandoTipo.APROVACAO,
        ("aprovacao", "aprovar", "autorizacao", "autorizar", "assembleia"),
    ),
    (AguardandoTipo.FORNECEDOR, ESPERA_POR_FORNECEDOR),
    (
        AguardandoTipo.MATERIAL,
        ("material", "peca", "pecas", "insumo", "insumos", "entrega", "sobressalente"),
    ),
    (
        AguardandoTipo.ADMINISTRACAO,
        ("administracao", "administradora", "sindico", "sindica", "gestao"),
    ),
    (
        AguardandoTipo.TERCEIRO,
        ("terceiro", "terceiros", "engenheiro", "perito", "vistoriador", "seguradora"),
    ),
)

# O próximo passo que cada espera pede. É uma frase de operação, não uma
# obrigação: quem confirma na tela pode ajustá-la antes de gravar.
ACOES_POR_ESPERA = {
    AguardandoTipo.MORADOR: "Cobrar retorno do morador",
    AguardandoTipo.APROVACAO: "Cobrar a aprovação",
    AguardandoTipo.MATERIAL: "Conferir a entrega do material",
    AguardandoTipo.ADMINISTRACAO: "Cobrar retorno da administração",
    AguardandoTipo.TERCEIRO: "Cobrar retorno do terceiro",
    AguardandoTipo.OUTRO: "Cobrar retorno",
}

# O que o relato diz que deixou de travar a demanda. Só vale sobre um
# impedimento que existe: sem nada travando, não há o que resolver.
FRASES_DE_RESOLUCAO = (
    "chegou",
    "chegaram",
    "entregue",
    "entregaram",
    "recebido",
    "recebida",
    "recebemos",
    "recebi",
    "liberado",
    "liberada",
    "liberaram",
    "aprovado",
    "aprovada",
    "aprovaram",
    "autorizado",
    "autorizada",
    "autorizaram",
    "disponivel",
    "disponiveis",
    "retirado",
    "retirada",
    "retiramos",
    "instalado",
    "instalada",
    "instalaram",
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

    ``conclusao``, ``previsao`` e ``fornecedor`` são a consequência sobre o
    ciclo. ``aguardando_tipo``, ``aguardando_descricao``, ``proxima_acao`` e
    ``impedimento_resolvido`` são a consequência sobre o acompanhamento — o que
    a demanda espera, o que vem depois e o que deixou de travar. São coisas
    diferentes e continuam separadas: esperar o fornecedor não muda a situação.
    """

    texto: str = ""
    conclusao: bool = False
    previsao: Previsao | None = None
    fornecedor: str = ""
    aguardando_tipo: str = ""
    aguardando_descricao: str = ""
    proxima_acao: str = ""
    impedimento_resolvido: bool = False

    @property
    def tem_acompanhamento(self) -> bool:
        """Se o relato sustenta alguma mudança de acompanhamento."""
        return bool(
            self.aguardando_tipo
            or self.aguardando_descricao
            or self.proxima_acao
            or self.impedimento_resolvido
        )

    def acompanhamento_proposto(self) -> dict:
        """Só os campos que o relato sustenta, prontos para o serviço oficial.

        Campo ausente quer dizer "o relato não falou disso", e o que não foi
        dito não é tocado. Só o impedimento aparece como limpeza explícita, e
        apenas quando o relato dá como resolvido o que travava a demanda.
        """
        proposto = {}
        if self.aguardando_tipo or self.aguardando_descricao:
            proposto["aguardando_tipo"] = self.aguardando_tipo
            proposto["aguardando_descricao"] = self.aguardando_descricao
        if self.proxima_acao:
            proposto["proxima_acao"] = self.proxima_acao
        if self.previsao is not None:
            proposto["previsao"] = self.previsao.data
        if self.impedimento_resolvido:
            proposto["impedimento"] = ""
        return proposto

    def aguardando_por_extenso(self) -> str:
        """Por quem se espera, como a gestão lê — o nome, quando houver."""
        if self.aguardando_descricao:
            return self.aguardando_descricao
        return AguardandoTipo(self.aguardando_tipo).label if self.aguardando_tipo else ""

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
        if self.aguardando_tipo or self.aguardando_descricao:
            itens.append(f"Aguardando: {self.aguardando_por_extenso()}")
        if self.proxima_acao:
            itens.append(f"Próxima ação: {self.proxima_acao}")
        if self.impedimento_resolvido:
            itens.append("O impedimento anterior foi dado como resolvido.")
        if self.conclusao:
            itens.append("O serviço foi dado como concluído.")
        return itens


def ler(texto: str, *, demanda=None, hoje: date | None = None) -> Movimentacao:
    """Lê o relato novo e devolve só o que ele sustenta.

    ``demanda`` entra por duas razões: reconhecer o fornecedor exige o cadastro
    do condomínio onde a demanda vive, e dar um impedimento por resolvido exige
    que exista um impedimento. Sem relato, a leitura é vazia, e a movimentação
    registra apenas o fato — nunca o que ninguém escreveu.
    """
    limpo = _normalizado(texto)
    if not limpo:
        return Movimentacao()

    referencia = hoje or timezone.localdate()
    previsao = _previsao(limpo, referencia)
    fornecedor = _fornecedor_do_relato(limpo, demanda)
    aguardando_tipo, aguardando_descricao = _aguardando(
        limpo, fornecedor=fornecedor, previsao=previsao
    )
    return Movimentacao(
        texto=texto,
        conclusao=_concluido(limpo),
        previsao=previsao,
        fornecedor=fornecedor,
        aguardando_tipo=aguardando_tipo,
        aguardando_descricao=aguardando_descricao,
        proxima_acao=_proxima_acao(limpo, tipo=aguardando_tipo, previsao=previsao),
        impedimento_resolvido=_impedimento_resolvido(limpo, demanda),
    )


def _afirma(texto_normalizado: str, frases) -> bool:
    """Se alguma das frases aparece afirmada — e não negada logo antes dela."""
    for frase in frases:
        for encontro in re.finditer(rf"\b{re.escape(frase)}s?\b", texto_normalizado):
            antes = texto_normalizado[: encontro.start()].split()
            if any(palavra in PALAVRAS_DE_NEGACAO for palavra in antes[-2:]):
                continue
            return True
    return False


def _concluido(texto_normalizado: str) -> bool:
    """Se o relato afirma o encerramento — e não o contrário dele."""
    return _afirma(texto_normalizado, FRASES_DE_CONCLUSAO)


def _aguardando(
    texto_normalizado: str, *, fornecedor: str, previsao: Previsao | None
) -> tuple[str, str]:
    """Por quem a demanda passa a esperar, quando o relato diz que espera.

    Duas coisas autorizam a espera: as palavras de espera, ou algo prometido
    para depois — quem prometeu vir é por quem se espera. Sem nenhuma das duas,
    não há espera: uma data sozinha só diz quando um fato aconteceu.

    O fornecedor reconhecido no cadastro responde antes das palavras, porque o
    nome é a resposta mais exata para "quem". Sem nome, a categoria mais
    próxima do que o relato diz é que responde; sem nenhuma, resta o genérico.
    """
    if not (_contem(texto_normalizado, SINAIS_DE_ESPERA) or previsao is not None):
        return "", ""

    if fornecedor:
        return AguardandoTipo.FORNECEDOR, fornecedor

    for tipo, palavras in CLASSIFICACAO_DA_ESPERA:
        if _contem(texto_normalizado, palavras):
            return tipo, ""
    return AguardandoTipo.OUTRO, ""


def _proxima_acao(
    texto_normalizado: str, *, tipo: str, previsao: Previsao | None
) -> str:
    """O próximo passo que a espera pede — vazio quando não há espera."""
    if not tipo:
        return ""
    if tipo == AguardandoTipo.FORNECEDOR:
        if _contem(texto_normalizado, ("orcamento", "orcamentos", "proposta")):
            return "Cobrar o orçamento"
        if previsao is not None:
            return "Acompanhar a visita"
        return "Cobrar retorno do fornecedor"
    return ACOES_POR_ESPERA[tipo]


def _impedimento_resolvido(texto_normalizado: str, demanda) -> bool:
    """Se o relato dá como resolvido o que estava travando a demanda.

    Só vale sobre um impedimento que existe: sem nada travando, não há o que
    resolver. E a negação continua valendo — "a peça não chegou" é o contrário
    de "a peça chegou".
    """
    if not (getattr(demanda, "impedimento", "") or "").strip():
        return False
    return _afirma(texto_normalizado, FRASES_DE_RESOLUCAO)


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

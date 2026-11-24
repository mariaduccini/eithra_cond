"""A memória operacional de um ativo e de um lugar do condomínio.

Um equipamento e um local não são só cadastro: são onde a operação deixa marcas
— manutenção feita, problema aberto, contrato que cobre, garantia vigente,
documento guardado, prova anexada. Nada disso é copiado para cá. A leitura é
feita na hora, dos vínculos que já existem: a demanda aponta para o equipamento
e para o local, a manutenção aponta para os dois, a Governança responde por
contrato e garantia, os documentos e as evidências continuam sendo deles.

Esta é a única tradução entre o que cada domínio registrou e o que a tela do
ativo e a do local mostram. Ela não guarda nada, não decide nada e não substitui
nenhum domínio: pergunta a cada um pelos vínculos que ele já tem. Se um dia
existir persistência de reincidência, será porque uma pergunta real passou a
exigir — não porque a tela ficou mais bonita.

A reincidência aqui é conservadora de propósito: só conta como repetição o que
tem a mesma natureza estrutural — o mesmo equipamento, ou o mesmo local, e a
mesma classificação de problema — dentro de uma janela de tempo. Dois títulos
parecidos não são reincidência.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.demandas.constants import TipoProblema
from apps.demandas.models import Demanda
from apps.governanca.consultas import vigencia_dos_vinculos
from apps.manutencoes.models import EvidenciaManutencao

# A janela em que duas ocorrências da mesma natureza contam como repetição. É a
# mesma leitura de "nos últimos seis meses" — e é parâmetro, não regra fixa:
# quem pergunta é que sabe de quanto tempo faz sentido falar.
JANELA_DA_REINCIDENCIA_DIAS = 182

# Quantas linhas cada bloco mostra. A memória não é o arquivo: o que importa
# aparece primeiro, e o resto continua a um clique de distância.
LIMITE_DE_LINHAS = 5


def _situacao(ativo: bool) -> dict:
    """Como o estado do cadastro é lido na memória, igual ao resto do sistema."""
    return {"texto": "Ativo" if ativo else "Inativo", "tom": "success" if ativo else "secondary"}


def _pares(*itens) -> list[dict]:
    """Só o que existe vira par: campo sem resposta não aparece vazio."""
    return [{"rotulo": rotulo, "valor": valor} for rotulo, valor in itens if valor]


def _data_por_extenso(valor) -> str:
    """A data como a gestão a lê, ou nada quando não há."""
    return valor.strftime("%d/%m/%Y") if valor else ""


def _momento_por_extenso(valor) -> str:
    """O instante como a gestão o lê, com o dia por extenso."""
    if not valor:
        return ""
    local = timezone.localtime(valor) if timezone.is_aware(valor) else valor
    hoje = timezone.localdate(local)
    if local.date() == hoje:
        return f"Hoje {local:%H:%M}"
    return f"{local:%d/%m/%Y %H:%M}"


# ---------------------------------------------------------------------------
# Reincidência: repetição estrutural, derivada — nunca guardada
# ---------------------------------------------------------------------------


def _reincidencia(demandas, *, referencia=None) -> list[dict]:
    """Os temas que se repetiram neste equipamento ou neste local.

    A conta é estrutural: mesma classificação de problema, mesma procedência
    (o equipamento ou o local que já recortou a lista), dentro da janela. Só
    entra o que tem classificação — "não identificado" não é tema —, e só o que
    passou de uma ocorrência. Um tema só, ou nenhum, devolve lista vazia.

    O agrupamento acontece aqui, em memória, sobre a lista já recortada: não há
    consulta de agregação nova nem campo de contagem. Melhor uma conta lida do
    que existe do que um número gravado que envelhece.
    """
    momento = referencia or timezone.now()
    inicio = momento - timedelta(days=JANELA_DA_REINCIDENCIA_DIAS)
    contagem: dict[str, int] = {}
    for demanda in demandas:
        if not demanda.tipo_problema:
            continue
        if demanda.criado_em < inicio:
            continue
        contagem[demanda.tipo_problema] = contagem.get(demanda.tipo_problema, 0) + 1

    reincidencias = [
        {"tema": rotulo, "quantidade": quantidade, "janela": "6 meses"}
        for classificacao, quantidade in contagem.items()
        if quantidade > 1
        for rotulo in [_rotulo_do_problema(classificacao)]
    ]
    return sorted(reincidencias, key=lambda item: item["quantidade"], reverse=True)


def _rotulo_do_problema(classificacao: str) -> str:
    """O tema como a tela o lê, sem expor o valor gravado."""
    try:
        return TipoProblema(classificacao).label
    except ValueError:
        return classificacao


# ---------------------------------------------------------------------------
# Linhas: o que cada registro mostra na memória
# ---------------------------------------------------------------------------


def _demanda_aberta_em_linha(demanda) -> dict:
    """Uma demanda em aberto, como a memória a lista."""
    return {
        "tipo": "Demanda",
        "titulo": demanda.titulo,
        "detalhe": demanda.get_status_display(),
        "quando": _momento_por_extenso(demanda.criado_em),
        "tom": "warning",
        "url": reverse("demandas:detalhe", args=[demanda.pk]),
    }


def _manutencao_aberta_em_linha(manutencao) -> dict:
    """Uma manutenção em aberto, como a memória a lista."""
    return {
        "tipo": "Manutenção",
        "titulo": manutencao.titulo,
        "detalhe": manutencao.get_status_display(),
        "quando": _momento_por_extenso(manutencao.criado_em),
        "tom": "info",
        "url": reverse("manutencoes:detalhe", args=[manutencao.pk]),
    }


def _manutencao_em_linha(manutencao) -> dict:
    """Uma manutenção já registrada, com o resultado que ela deixou."""
    return {
        "titulo": manutencao.titulo,
        "situacao": manutencao.get_status_display(),
        "resultado": manutencao.descricao_servico,
        "quando": _momento_por_extenso(
            manutencao.data_conclusao or manutencao.data_execucao or manutencao.criado_em
        ),
        "fornecedor": str(manutencao.fornecedor) if manutencao.fornecedor_id else "",
        "url": reverse("manutencoes:detalhe", args=[manutencao.pk]),
    }


def _demanda_em_linha(demanda) -> dict:
    """Uma demanda já registrada, com o tema e a situação dela."""
    return {
        "titulo": demanda.titulo,
        "tema": demanda.get_tipo_problema_display() if demanda.tipo_problema else "",
        "situacao": demanda.get_status_display(),
        "quando": _momento_por_extenso(demanda.criado_em),
        "url": reverse("demandas:detalhe", args=[demanda.pk]),
    }


def _evidencia_em_linha(evidencia) -> dict:
    """Uma prova anexada, referida pela manutenção a que pertence.

    A memória não guarda o arquivo nem monta endereço de storage: diz que a
    prova existe, de que tipo é e onde foi tirada. Abrir o arquivo continua
    sendo o que a tela da manutenção já faz — e é lá que a autorização é
    decidida, uma vez só.
    """
    return {
        "tipo": evidencia.get_tipo_midia_display(),
        "contexto": evidencia.get_contexto_display(),
        "quando": _momento_por_extenso(evidencia.capturada_em or evidencia.enviada_em),
        "url": reverse("manutencoes:detalhe", args=[evidencia.manutencao_id]),
    }


def _documento_em_linha(vinculo) -> dict:
    """Um documento vinculado, como a memória o lista."""
    documento = vinculo.documento
    return {
        "titulo": documento.titulo,
        "tipo": documento.tipo_documento.nome if documento.tipo_documento_id else "",
        "validade": _data_por_extenso(documento.data_validade),
        "url": reverse("governanca:documentos"),
    }


def _proxima_manutencao(planos) -> dict | None:
    """A próxima visita que a prevenção já marcou para este ativo ou lugar.

    A data vem do plano — a única autoridade sobre quando a manutenção volta —,
    e nunca de uma cópia guardada aqui. Sem plano ativo não há próxima
    manutenção: campo vazio seria promessa, não informação.
    """
    plano = planos.ativos().order_by("proxima_data_prevista").first()
    if plano is None:
        return None
    dias = (plano.proxima_data_prevista - timezone.localdate()).days
    if dias < 0:
        detalhe, tom = f"atrasada há {_dias(-dias)}", "warning"
    elif dias == 0:
        detalhe, tom = "prevista para hoje", "warning"
    else:
        detalhe, tom = f"em {_dias(dias)}", "info"
    return {
        "titulo": plano.titulo,
        "quando": _data_por_extenso(plano.proxima_data_prevista),
        "detalhe": detalhe,
        "tom": tom,
    }


def _dias(quantidade: int) -> str:
    """O tempo como a gestão o lê, no singular quando é um só."""
    return f"{quantidade} dia" if quantidade == 1 else f"{quantidade} dias"


# ---------------------------------------------------------------------------
# Consultas que a memória faz — cada uma recortada pelo vínculo que já existe
# ---------------------------------------------------------------------------


def _demandas_do_equipamento(equipamento):
    return Demanda.objects.filter(equipamento=equipamento, ativo=True).select_related("responsavel")


def _demandas_do_local(local):
    return Demanda.objects.filter(local=local, ativo=True).select_related("responsavel")


def _manutencoes_do_equipamento(equipamento):
    return equipamento.manutencoes.select_related("fornecedor", "local", "responsavel")


def _manutencoes_do_local(local):
    return local.manutencoes.select_related("fornecedor", "equipamento", "responsavel")


def _evidencias(*, equipamento=None, local=None):
    """As últimas provas anexadas às manutenções deste ativo ou deste local."""
    filtro = {"manutencao__equipamento": equipamento} if equipamento else {"manutencao__local": local}
    return (
        EvidenciaManutencao.objects.filter(**filtro)
        .select_related("manutencao")
        .order_by("-enviada_em")[:LIMITE_DE_LINHAS]
    )


def _abertos(demandas, manutencoes) -> list[dict]:
    """O que continua dependendo de uma ação aqui, seja demanda ou manutenção."""
    linhas = [_demanda_aberta_em_linha(demanda) for demanda in demandas]
    linhas += [_manutencao_aberta_em_linha(manutencao) for manutencao in manutencoes]
    return linhas[:LIMITE_DE_LINHAS]


def _fornecedores(governanca, manutencoes) -> list[dict]:
    """Quem atende este ativo: o que o contrato diz e o que a manutenção mostra.

    As duas fontes são declaradas na própria linha — um fornecedor por contrato
    e um fornecedor que já executou serviço não são a mesma afirmação, e a tela
    não deve confundi-las.
    """
    linhas = []
    vistos = set()
    for contrato in governanca["contratos"]:
        if contrato["fornecedor"] and contrato["fornecedor"] not in vistos:
            vistos.add(contrato["fornecedor"])
            linhas.append({"nome": contrato["fornecedor"], "via": "Contrato"})
    for manutencao in manutencoes:
        if manutencao.fornecedor_id:
            nome = str(manutencao.fornecedor)
            if nome not in vistos:
                vistos.add(nome)
                linhas.append({"nome": nome, "via": "Manutenção"})
    return linhas


# ---------------------------------------------------------------------------
# As duas memórias
# ---------------------------------------------------------------------------


def memoria_do_equipamento(equipamento, usuario) -> dict:
    """O que já aconteceu com este equipamento, e o que o cobre hoje.

    Responde o que a tela do ativo precisa saber sem inventar nada: o que é,
    onde está, quem atende, o que cobre, o que já foi feito, o que falhou, o que
    está aberto, o que se repete e o que está guardado sobre ele.
    """
    demandas = list(_demandas_do_equipamento(equipamento))
    manutencoes = list(_manutencoes_do_equipamento(equipamento))
    governanca = vigencia_dos_vinculos(
        usuario=usuario,
        condominio=equipamento.condominio,
        equipamento_id=equipamento.pk,
        local_id=equipamento.local_id,
    )

    abertas = [demanda for demanda in demandas if not demanda.esta_encerrada]
    em_aberto = [manutencao for manutencao in manutencoes if not manutencao.esta_encerrada]

    return {
        "ativo": _identidade_do_equipamento(equipamento),
        "local": (
            {
                "nome": equipamento.local.nome,
                "url": reverse("cadastros:local", args=[equipamento.local_id]),
            }
            if equipamento.local_id
            else None
        ),
        "governanca": governanca,
        "fornecedores": _fornecedores(governanca, manutencoes),
        "proxima": _proxima_manutencao(equipamento.planos_preventivos),
        "abertos": _abertos(abertas, em_aberto),
        "manutencoes": [_manutencao_em_linha(manutencao) for manutencao in manutencoes[:LIMITE_DE_LINHAS]],
        "demandas": [_demanda_em_linha(demanda) for demanda in demandas[:LIMITE_DE_LINHAS]],
        "documentos": [
            _documento_em_linha(vinculo)
            for vinculo in equipamento.documentos.select_related("documento__tipo_documento")
            .filter(documento__ativo=True)[:LIMITE_DE_LINHAS]
        ],
        "reincidencia": _reincidencia(demandas),
        "evidencias": [_evidencia_em_linha(item) for item in _evidencias(equipamento=equipamento)],
    }


def memoria_do_local(local, usuario) -> dict:
    """O que já aconteceu neste lugar, e o que continua acontecendo aqui.

    O lugar é o recorte mais largo da memória: reúne o que está aberto nele —
    demandas e manutenções —, os ativos que ficam ali, o que já se repetiu e as
    provas recentes. Não copia histórica nenhuma: projeta o que cada domínio já
    relaciona a este local.
    """
    demandas = list(_demandas_do_local(local))
    manutencoes = list(_manutencoes_do_local(local))
    governanca = vigencia_dos_vinculos(
        usuario=usuario,
        condominio=local.condominio,
        local_id=local.pk,
    )

    abertas = [demanda for demanda in demandas if not demanda.esta_encerrada]
    equipamentos = list(local.equipamentos.order_by("nome"))

    return {
        "local": _identidade_do_local(local),
        "governanca": governanca,
        "proxima": _proxima_manutencao(local.planos_preventivos),
        "abertos": _abertos(abertas, [m for m in manutencoes if not m.esta_encerrada]),
        "ativos": [
            {
                "nome": equipamento.nome,
                "tipo": (
                    equipamento.tipo_equipamento.nome
                    if equipamento.tipo_equipamento_id
                    else equipamento.tipo_texto
                ),
                "situacao": _situacao(equipamento.ativo),
                "url": reverse("cadastros:equipamento", args=[equipamento.pk]),
            }
            for equipamento in equipamentos[:LIMITE_DE_LINHAS]
        ],
        "total_de_ativos": len(equipamentos),
        "manutencoes": [_manutencao_em_linha(manutencao) for manutencao in manutencoes[:LIMITE_DE_LINHAS]],
        "demandas": [_demanda_em_linha(demanda) for demanda in demandas[:LIMITE_DE_LINHAS]],
        "reincidencia": _reincidencia(demandas),
        "evidencias": [_evidencia_em_linha(item) for item in _evidencias(local=local)],
    }


def _identidade_do_equipamento(equipamento) -> dict:
    """O que o ativo é, sem repetir na ficha o que já está no cabeçalho."""
    return {
        "pk": equipamento.pk,
        "nome": equipamento.nome,
        "tipo": (
            equipamento.tipo_equipamento.nome
            if equipamento.tipo_equipamento_id
            else equipamento.tipo_texto
        ),
        "ativo": equipamento.ativo,
        "situacao": _situacao(equipamento.ativo),
        "pares": _pares(
            ("Fabricante", equipamento.fabricante),
            ("Modelo", equipamento.modelo),
            ("Número de série", equipamento.numero_serie),
            ("Patrimônio", equipamento.patrimonio),
            ("Adquirido em", _data_por_extenso(equipamento.data_aquisicao)),
            ("Instalado em", _data_por_extenso(equipamento.data_instalacao)),
            ("Observação", equipamento.observacao),
        ),
    }


def _identidade_do_local(local) -> dict:
    """O que o lugar é, na mesma leitura enxuta do ativo."""
    return {
        "pk": local.pk,
        "nome": local.nome,
        "tipo": local.tipo_local.nome if local.tipo_local_id else local.get_tipo_display(),
        "ativo": local.ativo,
        "situacao": _situacao(local.ativo),
        "pares": _pares(
            ("Descrição", local.descricao),
        ),
    }

"""Confirmação humana das propostas lidas de e-mail.

Uma proposta nunca é gravada. Quando alguém confirma uma ação, tudo é
reconstruído na hora: a mensagem é recuperada de novo no provedor, a leitura é
refeita, a reconciliação acontece outra vez e só então a consequência é
executada — sempre por um service do domínio, nunca por escrita direta.

O motivo é simples: a leitura é uma foto do que o sistema sabia naquele
instante. Entre a leitura e a confirmação, uma demanda pode ter sido encerrada,
uma manutenção pode ter surgido, o condomínio pode ter mudado. Executar a ação
antiga seria agir sobre um mundo que não existe mais.

O que a pessoa confirma é a **ação**, não um destino. O destino é sempre
reconstruído a partir da proposta atual — nenhum identificador vindo da tela
é usado como se fosse verdade.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.condominios.acesso import tem_acesso_ativo
from apps.core import gmail
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.email_interpretacao import (
    AcaoProposta,
    LeituraEmail,
    LeituraIndisponivel,
    OrigemNaoSuportada,
    TipoCandidato,
    interpretar,
    reconciliar,
)
from apps.core.models import ItemImportacaoHistorica, VinculoImportacaoHistorica

logger = logging.getLogger(__name__)


# Só estas ações têm service de domínio e contrato claro o bastante para serem
# executadas. O resto continua sendo assunto do fluxo habitual: a leitura
# aponta a situação, mas quem decide é a pessoa, na tela do próprio domínio.
ACOES_DESTA_FASE = frozenset(
    {
        AcaoProposta.NENHUMA,
        AcaoProposta.RELACIONAR_DEMANDA,
        AcaoProposta.RELACIONAR_MANUTENCAO,
        AcaoProposta.CRIAR_NOVA_DEMANDA,
        AcaoProposta.CRIAR_AUTORIZACAO_PORTARIA,
    }
)

# O rótulo é o botão que a pessoa clica — nunca o valor técnico da ação.
ROTULO_DA_ACAO = {
    AcaoProposta.NENHUMA: "Concluir sem ação",
    AcaoProposta.RELACIONAR_DEMANDA: "Relacionar à demanda",
    AcaoProposta.RELACIONAR_MANUTENCAO: "Relacionar à manutenção",
    AcaoProposta.CRIAR_NOVA_DEMANDA: "Registrar demanda",
    AcaoProposta.CRIAR_AUTORIZACAO_PORTARIA: "Registrar autorização",
}

# Uma mensagem cria no máximo uma demanda nesta fase. A chave fixa é o que
# permite reconhecer a segunda confirmação como a mesma consequência.
CHAVE_DA_NOVA_DEMANDA = "criar_nova_demanda"

# O mesmo vale para a autorização de acesso: uma mensagem vira uma autorização.
CHAVE_DA_AUTORIZACAO = "criar_autorizacao_portaria"

# Quanto tempo vale uma autorização lida de e-mail. A mensagem não traz uma
# janela explícita, e sem prazo a portaria não teria como reconhecer a chegada:
# o horizonte é o de um turno, contado do momento da mensagem. Quem precisa de
# outro prazo registra a autorização no fluxo próprio da portaria.
JANELA_DA_AUTORIZACAO = timedelta(hours=12)

MENSAGEM_DE_CONTEXTO_MUDOU = "O contexto mudou desde a análise. Revise a nova proposta."
MENSAGEM_DE_FALHA = "Não conseguimos concluir agora. Tente novamente em instantes."
MENSAGEM_DE_FALTA_DE_INFORMACAO = (
    "Ainda falta informação para concluir. Revise a mensagem e tente de novo."
)
MENSAGEM_SEM_ACAO = "Esta mensagem não pede ação. Nada foi criado."
MENSAGEM_DA_DEMANDA = "Registramos uma demanda a partir desta mensagem."
MENSAGEM_DA_DEMANDA_RELACIONADA = "Esta mensagem ficou ligada à demanda."
MENSAGEM_DA_MANUTENCAO_RELACIONADA = "Esta mensagem ficou ligada à manutenção."
MENSAGEM_DA_AUTORIZACAO = "Registramos uma autorização de acesso a partir desta mensagem."


class ErroConfirmacaoEmail(Exception):
    """Falha controlada da confirmação de uma proposta de e-mail."""


class ItemNaoConfirmavel(ErroConfirmacaoEmail):
    """O item não está no ponto do ciclo em que a confirmação acontece."""


class PermissaoNegada(ErroConfirmacaoEmail):
    """Quem confirma não responde por este condomínio ou por esta ação."""


class AcaoNaoImplementada(ErroConfirmacaoEmail):
    """A ação ainda não é executada pelo Eithra nesta fase."""


@dataclass(frozen=True)
class ResultadoConfirmacaoEmail:
    """O desfecho de uma confirmação: o que foi executado e onde o item parou.

    Vive só em memória. Quando ``contexto_mudou`` é verdadeiro, nada foi
    executado e a leitura trazida é a nova — a que a pessoa deve revisar.
    """

    item: object = None
    estado_final: str = ""
    acao: str = ""
    executado: bool = False
    contexto_mudou: bool = False
    destino: object | None = None
    leitura: LeituraEmail | None = None
    falha: str = ""
    mensagem: str = ""


# ---------------------------------------------------------------------------
# Pendências
# ---------------------------------------------------------------------------


def pendencias_para(usuario):
    """Itens aguardando decisão nos condomínios em que a pessoa decide.

    Não é uma caixa de entrada do Gmail: é a lista do que a leitura identificou
    e ainda depende de alguém. Quem só executa não recebe mensagens
    administrativas aqui — o critério é o mesmo de quem pode registrar demanda.
    """
    if usuario is None or not getattr(usuario, "is_authenticated", False):
        return ItemImportacaoHistorica.objects.none()

    # Com a integração desligada não existe fila de e-mail: nada a decidir, e o
    # bloco some das telas. A leitura manual passa a ser o caminho.
    if not gmail.ativo():
        return ItemImportacaoHistorica.objects.none()

    from apps.demandas.permissoes import condominios_para_criar

    condominios = condominios_para_criar(usuario)
    return (
        ItemImportacaoHistorica.objects.filter(
            condominio__in=condominios,
            origem=OrigemImportacaoHistorica.GMAIL,
            estado_processamento=EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
        )
        .select_related("condominio")
        .order_by("-momento_da_mensagem", "-criado_em")
    )


def leitura_atual(item, *, provedor=None, interpretador=None):
    """A leitura reconstruída agora, sem executar nada.

    Devolve ``(leitura, falha)``: a leitura pronta para a tela, ou a falha que
    impediu de ler. Nenhum estado do item é tocado por aqui.
    """
    # Integração desligada: nenhuma mensagem é recuperada e nenhuma leitura de
    # IA de e-mail é disparada. A falha devolvida é o próprio desligamento.
    if not gmail.ativo():
        return None, "GmailDesligado"
    return _reconstruir(item, provedor=provedor, interpretador=interpretador)


def acao_disponivel(leitura: LeituraEmail) -> str:
    """A ação que esta leitura permite confirmar agora, se houver alguma.

    Fora das ações desta fase, devolve vazio: a situação continua visível, mas
    a execução pertence ao fluxo do domínio.
    """
    if leitura is None:
        return ""
    acao = leitura.proposta.acao
    if acao not in ACOES_DESTA_FASE:
        return ""
    if not _acao_corresponde(acao, leitura):
        return ""
    return acao


def rotulo_da_acao(acao: str) -> str:
    """O rótulo humano da ação, para o botão."""
    return ROTULO_DA_ACAO.get(acao, "")


# ---------------------------------------------------------------------------
# Confirmação
# ---------------------------------------------------------------------------


def confirmar_acao_email(
    item,
    acao,
    *,
    usuario,
    provedor=None,
    interpretador=None,
) -> ResultadoConfirmacaoEmail:
    """Executa a ação confirmada — depois de reconstruir tudo e revalidar.

    A ordem é obrigatória e não admite atalho: valida quem confirma, revalida o
    item, recupera a mensagem de novo, reinterpreta, reconcilia, confere se a
    ação confirmada ainda corresponde à proposta atual e só então executa.

    Quando o contexto mudou, nada é executado: o item continua aguardando
    confirmação e a nova leitura volta para ser revista.
    """
    _exigir_usuario(usuario)
    _exigir_item_confirmavel(item)

    if acao not in ACOES_DESTA_FASE:
        raise AcaoNaoImplementada("Esta ação ainda não é executada pelo Eithra.")

    _exigir_condominio(usuario, item)

    leitura, falha = _reconstruir(
        item, provedor=provedor, interpretador=interpretador
    )
    if falha:
        _marcar_erro(item)
        logger.warning("Confirmação do item %s terminou em erro (%s).", item.pk, falha)
        return ResultadoConfirmacaoEmail(
            item=item,
            estado_final=EstadoProcessamentoImportacao.ERRO,
            acao=acao,
            falha=falha,
            mensagem=MENSAGEM_DE_FALHA,
        )

    if not _acao_corresponde(acao, leitura):
        logger.info(
            "Item %s: a proposta deixou de ser %s. Nada foi executado.",
            item.pk,
            acao,
        )
        return ResultadoConfirmacaoEmail(
            item=item,
            estado_final=item.estado_processamento,
            acao=acao,
            contexto_mudou=True,
            leitura=leitura,
            mensagem=MENSAGEM_DE_CONTEXTO_MUDOU,
        )

    if acao == AcaoProposta.NENHUMA:
        return _concluir_sem_acao(item, leitura)
    if acao == AcaoProposta.RELACIONAR_DEMANDA:
        return _relacionar_demanda(item, leitura, usuario)
    if acao == AcaoProposta.RELACIONAR_MANUTENCAO:
        return _relacionar_manutencao(item, leitura, usuario)
    if acao == AcaoProposta.CRIAR_AUTORIZACAO_PORTARIA:
        return _criar_autorizacao_portaria(item, leitura, usuario)
    return _criar_nova_demanda(item, leitura, usuario)


# ---------------------------------------------------------------------------
# Ações
# ---------------------------------------------------------------------------


def _concluir_sem_acao(item, leitura) -> ResultadoConfirmacaoEmail:
    """Fecha o item sem criar consequência nenhuma.

    Não cria vínculo artificial: quando não há nada a ligar, o silêncio é a
    resposta — e não se inventa uma relação só para ter uma linha gravada.
    """
    item = _concluir(item)
    return ResultadoConfirmacaoEmail(
        item=item,
        estado_final=EstadoProcessamentoImportacao.PROCESSADO,
        acao=AcaoProposta.NENHUMA,
        executado=True,
        leitura=leitura,
        mensagem=MENSAGEM_SEM_ACAO,
    )


def _relacionar_demanda(item, leitura, usuario) -> ResultadoConfirmacaoEmail:
    """Liga a mensagem a uma demanda existente, sem tocar na demanda.

    A consequência é a proveniência: fica registrado por qual mensagem aquela
    demanda foi mencionada. Nada de status, nada de observação — o corpo do
    e-mail não entra no histórico da demanda.
    """
    from apps.demandas.permissoes import pode_ver_demanda

    demanda = _destino_da_proposta(leitura, AcaoProposta.RELACIONAR_DEMANDA)
    if not _destino_valido(demanda, item):
        return _contexto_mudou(item, leitura)
    if not pode_ver_demanda(usuario, demanda):
        raise PermissaoNegada("Você não acompanha esta demanda.")

    with transaction.atomic():
        _registrar(
            item=item,
            entidade_destino=EntidadeImportacaoHistorica.DEMANDA,
            destino=demanda,
            usuario=usuario,
        )
        item = _concluir(item)

    return ResultadoConfirmacaoEmail(
        item=item,
        estado_final=EstadoProcessamentoImportacao.PROCESSADO,
        acao=AcaoProposta.RELACIONAR_DEMANDA,
        executado=True,
        destino=demanda,
        leitura=leitura,
        mensagem=MENSAGEM_DA_DEMANDA_RELACIONADA,
    )


def _relacionar_manutencao(item, leitura, usuario) -> ResultadoConfirmacaoEmail:
    """Liga a mensagem a uma manutenção existente, sem concluí-la.

    "O fornecedor informou que concluiu" continua sendo uma declaração — quem
    encerra o serviço é a gestão, no fluxo próprio. Aqui só se registra de onde
    veio a informação.
    """
    from apps.manutencoes.permissoes import pode_ver_manutencao

    manutencao = _destino_da_proposta(leitura, AcaoProposta.RELACIONAR_MANUTENCAO)
    if not _destino_valido(manutencao, item):
        return _contexto_mudou(item, leitura)
    if not pode_ver_manutencao(usuario, manutencao):
        raise PermissaoNegada("Você não acompanha esta manutenção.")

    with transaction.atomic():
        _registrar(
            item=item,
            entidade_destino=EntidadeImportacaoHistorica.MANUTENCAO,
            destino=manutencao,
            usuario=usuario,
        )
        item = _concluir(item)

    return ResultadoConfirmacaoEmail(
        item=item,
        estado_final=EstadoProcessamentoImportacao.PROCESSADO,
        acao=AcaoProposta.RELACIONAR_MANUTENCAO,
        executado=True,
        destino=manutencao,
        leitura=leitura,
        mensagem=MENSAGEM_DA_MANUTENCAO_RELACIONADA,
    )


def _criar_nova_demanda(item, leitura, usuario) -> ResultadoConfirmacaoEmail:
    """Registra a demanda pelos fatos da leitura atual — nunca pelo payload.

    Quem cria é o service do domínio, com as regras dele: nada de status, nada
    de prioridade, nada de responsável inventado. Só entra o que a mensagem
    sustenta: o assunto vira o título, e local e equipamento só entram quando o
    cadastro do condomínio os confirmou.
    """
    from apps.demandas.models import Demanda
    from apps.demandas.services import criar_demanda

    existente = _vinculo_existente(
        item, EntidadeImportacaoHistorica.DEMANDA, CHAVE_DA_NOVA_DEMANDA
    )
    if existente is not None and existente.destino_id:
        demanda = Demanda.objects.filter(
            pk=existente.destino_id, condominio_id=item.condominio_id
        ).first()
        if demanda is not None:
            item = _concluir(item)
            return ResultadoConfirmacaoEmail(
                item=item,
                estado_final=EstadoProcessamentoImportacao.PROCESSADO,
                acao=AcaoProposta.CRIAR_NOVA_DEMANDA,
                executado=True,
                destino=demanda,
                leitura=leitura,
                mensagem=MENSAGEM_DA_DEMANDA,
            )

    titulo = _titulo_da_mensagem(leitura)
    if not titulo:
        return ResultadoConfirmacaoEmail(
            item=item,
            estado_final=item.estado_processamento,
            acao=AcaoProposta.CRIAR_NOVA_DEMANDA,
            leitura=leitura,
            mensagem=MENSAGEM_DE_FALTA_DE_INFORMACAO,
        )

    from apps.demandas.constants import OrigemDemanda

    with transaction.atomic():
        demanda = criar_demanda(
            condominio=item.condominio,
            criado_por=usuario,
            titulo=titulo,
            origem=OrigemDemanda.EMAIL,
            local=_objeto_confirmado(leitura, "local", item),
            equipamento=_objeto_confirmado(leitura, "equipamento", item),
        )
        _registrar(
            item=item,
            entidade_destino=EntidadeImportacaoHistorica.DEMANDA,
            destino=demanda,
            usuario=usuario,
            chave=CHAVE_DA_NOVA_DEMANDA,
        )
        item = _concluir(item)

    return ResultadoConfirmacaoEmail(
        item=item,
        estado_final=EstadoProcessamentoImportacao.PROCESSADO,
        acao=AcaoProposta.CRIAR_NOVA_DEMANDA,
        executado=True,
        destino=demanda,
        leitura=leitura,
        mensagem=MENSAGEM_DA_DEMANDA,
    )


def _criar_autorizacao_portaria(item, leitura, usuario) -> ResultadoConfirmacaoEmail:
    """Registra a autorização de acesso pelos fatos da leitura — nunca pelo payload.

    O que entra é só o que a mensagem sustenta: o nome que ela declarou e o
    destino que o cadastro confirmou — a unidade de verdade ou o local de
    verdade. Nada de pessoa nem de unidade inventadas: faltando o essencial, a
    autorização não é criada pela metade e o item continua aguardando decisão.
    E, em nenhum caso, esta ação cria uma demanda.
    """
    from apps.portaria.constants import OrigemPortaria
    from apps.portaria.models import AutorizacaoPortaria
    from apps.portaria.services import registrar_esperado

    existente = _vinculo_existente(
        item, EntidadeImportacaoHistorica.AUTORIZACAO_PORTARIA, CHAVE_DA_AUTORIZACAO
    )
    if existente is not None and existente.destino_id:
        autorizacao = AutorizacaoPortaria.objects.filter(
            pk=existente.destino_id, condominio_id=item.condominio_id
        ).first()
        if autorizacao is not None:
            item = _concluir(item)
            return ResultadoConfirmacaoEmail(
                item=item,
                estado_final=EstadoProcessamentoImportacao.PROCESSADO,
                acao=AcaoProposta.CRIAR_AUTORIZACAO_PORTARIA,
                executado=True,
                destino=autorizacao,
                leitura=leitura,
                mensagem=MENSAGEM_DA_AUTORIZACAO,
            )

    dados = _dados_da_autorizacao(leitura, item)
    if dados is None:
        return ResultadoConfirmacaoEmail(
            item=item,
            estado_final=item.estado_processamento,
            acao=AcaoProposta.CRIAR_AUTORIZACAO_PORTARIA,
            leitura=leitura,
            mensagem=MENSAGEM_DE_FALTA_DE_INFORMACAO,
        )

    with transaction.atomic():
        autorizacao = registrar_esperado(
            condominio=item.condominio,
            usuario=usuario,
            dados=dados,
            origem=OrigemPortaria.EMAIL,
        )
        _registrar(
            item=item,
            entidade_destino=EntidadeImportacaoHistorica.AUTORIZACAO_PORTARIA,
            destino=autorizacao,
            usuario=usuario,
            chave=CHAVE_DA_AUTORIZACAO,
        )
        item = _concluir(item)

    return ResultadoConfirmacaoEmail(
        item=item,
        estado_final=EstadoProcessamentoImportacao.PROCESSADO,
        acao=AcaoProposta.CRIAR_AUTORIZACAO_PORTARIA,
        executado=True,
        destino=autorizacao,
        leitura=leitura,
        mensagem=MENSAGEM_DA_AUTORIZACAO,
    )


def _dados_da_autorizacao(leitura, item) -> dict | None:
    """O que a leitura sustenta para virar uma autorização da portaria.

    Devolve ``None`` quando falta o essencial: o domínio exige quem entra e um
    destino, e uma autorização sem isso não se sustenta. A unidade só entra se o
    cadastro confirmar uma só; o nome é o que a mensagem declarou.
    """
    nome = _nome_declarado(leitura)
    unidade = _unidade_confirmada(leitura, item)
    local = _objeto_confirmado(leitura, "local", item)
    if not nome or (unidade is None and local is None):
        return None

    inicio = item.momento_da_mensagem or timezone.now()
    dados = {
        "nome": nome[:180],
        "unidade": unidade,
        "local": local,
        "inicio_previsto": inicio,
        "fim_previsto": inicio + JANELA_DA_AUTORIZACAO,
    }
    placa = _placa_declarada(leitura)
    if placa:
        dados["placa"] = placa
    return dados


def _nome_declarado(leitura) -> str:
    """O nome que a mensagem disse — nunca um cadastro inventado."""
    fato = leitura.fato("nome")
    return fato.texto if fato is not None and fato.existe else ""


def _placa_declarada(leitura) -> str:
    """A placa citada na mensagem, quando houver."""
    fato = leitura.fato("placa")
    return fato.valor if fato is not None and fato.existe else ""


def _unidade_confirmada(leitura, item):
    """A unidade que o cadastro confirma a partir do número citado, ou nenhuma."""
    from apps.portaria.interpretacao import unidade_citada

    fato = leitura.fato("unidade")
    if fato is None or not fato.existe:
        return None
    return unidade_citada(fato.texto, item.condominio)


# ---------------------------------------------------------------------------
# Reconstrução
# ---------------------------------------------------------------------------


def _reconstruir(item, *, provedor=None, interpretador=None):
    """Recupera a mensagem de novo e refaz a leitura, do zero.

    O provedor é montado antes de qualquer coisa: falta de configuração não é
    falha da mensagem e não deve marcar item nenhum como erro. Devolve
    ``(leitura, falha)`` — uma das duas sempre vazia.
    """
    leitor = provedor if provedor is not None else gmail.obter_provedor()
    ler = interpretador or interpretar

    try:
        mensagem = leitor.obter_mensagem(item.message_id)
    except gmail.ErroGmail as erro:
        logger.warning("Item %s: a mensagem não pôde ser recuperada.", item.pk)
        return None, type(erro).__name__

    try:
        leitura = ler(mensagem, condominio=item.condominio)
    except LeituraIndisponivel:
        logger.warning("Item %s: a leitura está indisponível.", item.pk)
        return None, LeituraIndisponivel.__name__

    leitura = reconciliar(
        leitura, mensagem=mensagem, item=item, condominio=item.condominio
    )
    return leitura, ""


def _acao_corresponde(acao: str, leitura: LeituraEmail) -> bool:
    """Se a ação confirmada ainda é a que a leitura atual propõe."""
    proposta = leitura.proposta
    if acao == AcaoProposta.NENHUMA:
        return not proposta.existe
    if proposta.acao != acao:
        return False
    if acao in {AcaoProposta.RELACIONAR_DEMANDA, AcaoProposta.RELACIONAR_MANUTENCAO}:
        return _destino_da_proposta(leitura, acao) is not None
    return True


def _destino_da_proposta(leitura: LeituraEmail, acao: str):
    """O registro que a proposta atual aponta — reconstruído, não recebido."""
    candidato = leitura.proposta.candidato
    if candidato is None or candidato.objeto is None:
        return None
    esperado = (
        TipoCandidato.DEMANDA
        if acao == AcaoProposta.RELACIONAR_DEMANDA
        else TipoCandidato.MANUTENCAO
    )
    if candidato.tipo != esperado:
        return None
    return candidato.objeto


def _destino_valido(destino, item) -> bool:
    """Se o registro de destino continua servindo: mesmo condomínio e aberto."""
    if destino is None or getattr(destino, "pk", None) is None:
        return False
    if getattr(destino, "condominio_id", None) != item.condominio_id:
        return False
    return not bool(getattr(destino, "esta_encerrada", False))


def _titulo_da_mensagem(leitura: LeituraEmail) -> str:
    """O assunto declarado na mensagem, quando a leitura o sustenta."""
    assunto = leitura.fato("assunto")
    if assunto is not None and assunto.existe:
        return assunto.texto
    return ""


def _objeto_confirmado(leitura: LeituraEmail, campo: str, item):
    """O cadastro que confirma este campo — nunca o nome solto da mensagem."""
    fato = leitura.fato(campo)
    if fato is None or not fato.confirmado or fato.objeto is None:
        return None
    if getattr(fato.objeto, "condominio_id", None) != item.condominio_id:
        return None
    return fato.objeto


# ---------------------------------------------------------------------------
# Vínculo e estado
# ---------------------------------------------------------------------------


def _registrar(*, item, entidade_destino, destino, usuario, chave: str = ""):
    """Grava a proveniência: de qual mensagem este registro veio.

    A identidade do vínculo é o item, o tipo de entidade e a identificação do
    destino. Confirmar de novo a mesma relação encontra a linha existente, em
    vez de criar uma segunda.
    """
    return VinculoImportacaoHistorica.objects.registrar_vinculo(
        item=item,
        entidade_destino=entidade_destino,
        identificador_destino=chave or str(destino.pk),
        destino_id=destino.pk,
        resultado=ResultadoImportacaoHistorica.CRIADO,
        revisao_manual=True,
        criado_por=usuario,
    )


def _vinculo_existente(item, entidade_destino, chave):
    """O vínculo já registrado para esta consequência, se houver."""
    return (
        VinculoImportacaoHistorica.objects.filter(
            item=item,
            entidade_destino=entidade_destino,
            identificador_destino=chave,
        )
        .order_by("-criado_em")
        .first()
    )


def _concluir(item):
    """Fecha o item como processado, pela API de estado de sempre."""
    return type(item).objects.alterar_estado_processamento(
        item=item, novo_estado=EstadoProcessamentoImportacao.PROCESSADO
    )


def _marcar_erro(item):
    """Leva o item a erro passando pelo ciclo válido de transições.

    A confirmação parte de ``AGUARDANDO_CONFIRMACAO``, que não vai direto para
    ``ERRO``: o retry usa o mesmo caminho de sempre, e passar por
    ``PROCESSANDO`` é o que mantém a trilha coerente.
    """
    objetos = type(item).objects
    item = objetos.alterar_estado_processamento(
        item=item,
        novo_estado=EstadoProcessamentoImportacao.PROCESSANDO,
        reprocessamento_explicito=True,
    )
    return objetos.alterar_estado_processamento(
        item=item, novo_estado=EstadoProcessamentoImportacao.ERRO
    )


def _contexto_mudou(item, leitura) -> ResultadoConfirmacaoEmail:
    """Nada é executado, e o item continua aguardando decisão."""
    return ResultadoConfirmacaoEmail(
        item=item,
        estado_final=item.estado_processamento,
        acao=leitura.proposta.acao,
        contexto_mudou=True,
        leitura=leitura,
        mensagem=MENSAGEM_DE_CONTEXTO_MUDOU,
    )


# ---------------------------------------------------------------------------
# Validações
# ---------------------------------------------------------------------------


def _exigir_usuario(usuario):
    if usuario is None or not getattr(usuario, "is_authenticated", False):
        raise PermissaoNegada("Entre no sistema para confirmar esta ação.")


def _exigir_item_confirmavel(item):
    if item is None or not getattr(item, "pk", None):
        raise ValidationError({"item": "Diga qual mensagem deve ser confirmada."})
    if item.origem != OrigemImportacaoHistorica.GMAIL:
        raise ItemNaoConfirmavel("Esta confirmação só se aplica a mensagens do Gmail.")
    if not (item.message_id or "").strip():
        raise ItemNaoConfirmavel("A mensagem está sem o identificador de origem.")
    if item.estado_processamento != EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO:
        raise ItemNaoConfirmavel("Esta mensagem não está aguardando confirmação.")


def _exigir_condominio(usuario, item):
    """Quem confirma responde por este condomínio — e pelo mesmo critério de agir."""
    from apps.demandas.permissoes import condominios_para_criar

    if not tem_acesso_ativo(usuario, item.condominio):
        raise PermissaoNegada("Você não responde por este condomínio.")
    if not condominios_para_criar(usuario).filter(pk=item.condominio_id).exists():
        raise PermissaoNegada("Você não decide sobre mensagens deste condomínio.")


__all__ = [
    "AcaoNaoImplementada",
    "ErroConfirmacaoEmail",
    "ItemNaoConfirmavel",
    "PermissaoNegada",
    "ResultadoConfirmacaoEmail",
    "acao_disponivel",
    "confirmar_acao_email",
    "leitura_atual",
    "pendencias_para",
    "rotulo_da_acao",
]


# ``OrigemNaoSuportada`` segue disponível para quem chamar a camada, mas é a
# confirmação que decide o que fazer com o item — por isso ela não sobe daqui.
_ = OrigemNaoSuportada

"""Operações do domínio de demandas operacionais.

Criar, editar, mover a situação, concluir, cancelar ou observar sempre grava o
histórico na mesma transação: ou a demanda e sua memória mudam juntas, ou nada
muda. Nenhuma regra de tela mora aqui — a view traduz o que a gestão decidiu, e
o domínio decide e registra.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .constants import (
    AguardandoTipo,
    ImpactoDemanda,
    OrigemDemanda,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
    TipoEventoDemanda,
    TipoProblema,
)
from .models import Demanda, HistoricoDemanda

# A descrição do evento cabe no histórico como uma frase curta. Observações
# longas são cortadas aqui, e não na tela.
LIMITE_DA_OBSERVACAO = 255

# Os campos do acompanhamento operacional, na ordem em que a frase do evento os
# lê. Nenhum deles é situação: o ciclo da demanda continua exatamente onde está.
ROTULOS_DO_ACOMPANHAMENTO = {
    "aguardando_tipo": "Aguardando",
    "aguardando_descricao": "O que se espera",
    "proxima_acao": "Próxima ação",
    "prazo_proxima_acao": "Prazo da próxima ação",
    "previsao": "Previsão",
    "impedimento": "Impedimento",
    "responsavel_acompanhamento": "Quem acompanha",
}
CAMPOS_DO_ACOMPANHAMENTO = tuple(ROTULOS_DO_ACOMPANHAMENTO)

# Marca de "o chamador não falou sobre este campo". É diferente de ``None``, que
# é uma decisão: limpar o equipamento ou tirar o tipo de problema. Sem essa
# distinção, abrir e salvar o formulário antigo apagaria o que ninguém pediu
# para apagar — e o histórico registraria uma mudança que não houve.
_NAO_INFORMADO = object()


class ErroDeDemanda(Exception):
    """Uma decisão do domínio sobre a demanda que a gestão precisa saber."""


def _texto(valor: str | None) -> str:
    """Colapsa espaços e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


def _nome_de(usuario) -> str:
    """O nome pelo qual a pessoa é reconhecida no histórico."""
    if usuario is None:
        return "Sem responsável"
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "short_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsável"
    )


def _data_por_extenso(valor) -> str:
    """A data como a gestão a lê, ou a ausência dela."""
    return valor.strftime("%d/%m/%Y") if valor else "sem prazo"


def _nome_do_local(local) -> str:
    """O local como a gestão o lê no histórico, ou a ausência dele."""
    if local is None or not getattr(local, "pk", None):
        return "sem local"
    return local.nome


def _nome_do_equipamento(equipamento) -> str:
    """O equipamento como a gestão o lê no histórico, ou a ausência dele."""
    if equipamento is None or not getattr(equipamento, "pk", None):
        return "sem equipamento"
    return equipamento.nome


def _rotulo(escolhas, valor, vazio: str = "não informado") -> str:
    """Como a gestão lê um valor de escolha do cadastro, ou a ausência dele.

    O valor gravado nunca aparece cru: o histórico fala a mesma língua da tela.
    """
    if not valor:
        return vazio
    try:
        return escolhas(valor).label
    except ValueError:
        return valor


def _registrar(
    demanda,
    usuario,
    tipo_evento,
    descricao: str,
    *,
    anteriores=None,
    novos=None,
) -> HistoricoDemanda:
    """Acrescenta um evento ao histórico. Nunca reescreve o que já existe."""
    return HistoricoDemanda.objects.create(
        demanda=demanda,
        usuario=usuario,
        tipo_evento=tipo_evento,
        descricao=descricao[:LIMITE_DA_OBSERVACAO],
        dados_anteriores=anteriores,
        dados_novos=novos,
    )


@transaction.atomic
def criar_demanda(
    *,
    condominio,
    criado_por,
    titulo: str,
    descricao: str = "",
    origem: str = OrigemDemanda.MANUAL,
    prioridade: str = PrioridadeDemanda.NORMAL,
    responsavel=None,
    prazo=None,
    local=None,
    local_contexto: str = "",
    equipamento=None,
    tipo_problema=None,
    impacto=None,
    risco=None,
    classificacao_sugerida=None,
) -> Demanda:
    """Registra a demanda e nasce o primeiro evento do histórico.

    O local vem do cadastro do condomínio. ``local_contexto`` é o texto legado
    das demandas antigas: continua aceito, mas não é preenchido por ninguém.

    ``classificacao_sugerida`` guarda o que a leitura do relato propôs, com a
    origem e a confiança de cada campo. Não é o mesmo que a demanda passou a
    valer: a sugestão existe para ser comparada com a confirmação mais adiante,
    e nunca é apresentada como fato.

    A área não é coluna: ela é derivada do tipo do local cadastrado, e a
    recorrência é uma leitura do histórico — guardar qualquer uma delas criaria
    uma segunda fonte de verdade que envelhece sozinha.
    """
    demanda = Demanda.objects.create(
        condominio=condominio,
        titulo=_texto(titulo),
        descricao=_texto_longo(descricao),
        origem=origem,
        prioridade=prioridade,
        responsavel=responsavel,
        prazo=prazo,
        local=local,
        local_contexto=_texto(local_contexto),
        equipamento=equipamento,
        tipo_problema=tipo_problema or None,
        impacto=impacto or None,
        risco=risco or None,
        classificacao_sugerida=classificacao_sugerida,
        criado_por=criado_por,
    )
    _registrar(demanda, criado_por, TipoEventoDemanda.CRIADA, "Demanda criada")
    return demanda


@transaction.atomic
def editar_demanda(
    *,
    demanda: Demanda,
    usuario,
    titulo: str,
    descricao: str = "",
    prioridade: str,
    responsavel=None,
    prazo=None,
    local=None,
    local_contexto=None,
    equipamento=_NAO_INFORMADO,
    tipo_problema=_NAO_INFORMADO,
    impacto=_NAO_INFORMADO,
    risco=_NAO_INFORMADO,
) -> Demanda:
    """Atualiza o conteúdo da demanda, guardando cada mudança que importa.

    Só o que realmente mudou vira evento — o histórico registra decisão, não o
    ato de abrir e salvar o formulário. ``local_contexto`` só é tocado quando
    vem explícito: sem isso, o texto legado das demandas antigas fica intacto.

    Equipamento e classificação seguem a mesma regra, com uma marca própria de
    ausência: quem não fala do campo não o apaga, e quem manda vazio está
    decidindo limpá-lo. A classificação que a leitura propôs não é reescrita
    aqui — o que a gestão confirma vira o valor da demanda, e é isso que muda.
    """
    titulo = _texto(titulo)
    descricao = _texto_longo(descricao)

    campos = ["atualizado_em"]

    if titulo and titulo != demanda.titulo:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            "Título atualizado",
            anteriores={"titulo": demanda.titulo},
            novos={"titulo": titulo},
        )
        demanda.titulo = titulo
        campos.append("titulo")

    if descricao != (demanda.descricao or ""):
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            "Descrição atualizada",
            anteriores={"descricao": demanda.descricao},
            novos={"descricao": descricao},
        )
        demanda.descricao = descricao
        campos.append("descricao")

    if getattr(local, "pk", None) != demanda.local_id:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            f"Local: {_nome_do_local(demanda.local)} → {_nome_do_local(local)}",
            anteriores={"local": str(demanda.local_id) if demanda.local_id else None},
            novos={"local": str(local.pk) if getattr(local, "pk", None) else None},
        )
        demanda.local = local
        campos.append("local")

    if (
        equipamento is not _NAO_INFORMADO
        and getattr(equipamento, "pk", None) != demanda.equipamento_id
    ):
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            f"Equipamento: {_nome_do_equipamento(demanda.equipamento)} → "
            f"{_nome_do_equipamento(equipamento)}",
            anteriores={
                "equipamento": str(demanda.equipamento_id)
                if demanda.equipamento_id
                else None
            },
            novos={
                "equipamento": str(equipamento.pk)
                if getattr(equipamento, "pk", None)
                else None
            },
        )
        demanda.equipamento = equipamento
        campos.append("equipamento")

    if local_contexto is not None:
        local_contexto = _texto(local_contexto)
        if local_contexto != (demanda.local_contexto or ""):
            _registrar(
                demanda,
                usuario,
                TipoEventoDemanda.EDICAO,
                "Local atualizado",
                anteriores={"local_contexto": demanda.local_contexto},
                novos={"local_contexto": local_contexto},
            )
            demanda.local_contexto = local_contexto
            campos.append("local_contexto")

    if tipo_problema is not _NAO_INFORMADO:
        novo = tipo_problema or None
        if novo != demanda.tipo_problema:
            _registrar(
                demanda,
                usuario,
                TipoEventoDemanda.EDICAO,
                "Tipo de problema: "
                f"{_rotulo(TipoProblema, demanda.tipo_problema)} → "
                f"{_rotulo(TipoProblema, novo)}",
                anteriores={"tipo_problema": demanda.tipo_problema},
                novos={"tipo_problema": novo},
            )
            demanda.tipo_problema = novo
            campos.append("tipo_problema")

    if impacto is not _NAO_INFORMADO:
        novo = impacto or None
        if novo != demanda.impacto:
            _registrar(
                demanda,
                usuario,
                TipoEventoDemanda.EDICAO,
                f"Impacto: {_rotulo(ImpactoDemanda, demanda.impacto)} → "
                f"{_rotulo(ImpactoDemanda, novo)}",
                anteriores={"impacto": demanda.impacto},
                novos={"impacto": novo},
            )
            demanda.impacto = novo
            campos.append("impacto")

    if risco is not _NAO_INFORMADO:
        novo = risco or None
        if novo != demanda.risco:
            _registrar(
                demanda,
                usuario,
                TipoEventoDemanda.EDICAO,
                f"Risco: {_rotulo(RiscoDemanda, demanda.risco)} → "
                f"{_rotulo(RiscoDemanda, novo)}",
                anteriores={"risco": demanda.risco},
                novos={"risco": novo},
            )
            demanda.risco = novo
            campos.append("risco")

    if prioridade != demanda.prioridade:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.PRIORIDADE,
            f"Prioridade: {PrioridadeDemanda(demanda.prioridade).label} → "
            f"{PrioridadeDemanda(prioridade).label}",
            anteriores={"prioridade": demanda.prioridade},
            novos={"prioridade": prioridade},
        )
        demanda.prioridade = prioridade
        campos.append("prioridade")

    if getattr(responsavel, "pk", None) != demanda.responsavel_id:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.RESPONSAVEL,
            f"Responsável: {_nome_de(demanda.responsavel)} → {_nome_de(responsavel)}",
            anteriores={
                "responsavel": str(demanda.responsavel_id) if demanda.responsavel_id else None
            },
            novos={
                "responsavel": str(responsavel.pk)
                if getattr(responsavel, "pk", None)
                else None
            },
        )
        demanda.responsavel = responsavel
        campos.append("responsavel")

    if prazo != demanda.prazo:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.PRAZO,
            f"Prazo: {_data_por_extenso(demanda.prazo)} → {_data_por_extenso(prazo)}",
            anteriores={"prazo": demanda.prazo.isoformat() if demanda.prazo else None},
            novos={"prazo": prazo.isoformat() if prazo else None},
        )
        demanda.prazo = prazo
        campos.append("prazo")

    demanda.save(update_fields=campos)
    return demanda


@transaction.atomic
def alterar_status(
    *, demanda: Demanda, usuario, novo_status: str
) -> HistoricoDemanda | None:
    """Move a demanda para outra situação, ajustando as datas que a acompanham.

    Concluir registra quando; cancelar registra quando; voltar a andar limpa as
    duas. A situação e as datas nunca se contradizem — e o evento que descreve a
    mudança é do tipo certo: conclusão, cancelamento ou simples movimento.

    Devolve o evento que a mudança produziu — é ele que uma evidência anexada na
    mesma atualização acompanha —, ou ``None`` quando nada mudou.
    """
    if novo_status not in StatusDemanda.values:
        raise ErroDeDemanda("Situação inválida.")

    anterior = demanda.status
    if novo_status == anterior:
        return None

    campos = ["status", "atualizado_em"]

    if novo_status == StatusDemanda.CONCLUIDA:
        demanda.concluido_em = timezone.now()
        demanda.cancelado_em = None
        campos += ["concluido_em", "cancelado_em"]
        tipo_evento = TipoEventoDemanda.CONCLUIDA
        descricao = "Demanda concluída"
    elif novo_status == StatusDemanda.CANCELADA:
        demanda.cancelado_em = timezone.now()
        demanda.concluido_em = None
        campos += ["concluido_em", "cancelado_em"]
        tipo_evento = TipoEventoDemanda.CANCELADA
        descricao = "Demanda cancelada"
    else:
        demanda.concluido_em = None
        demanda.cancelado_em = None
        campos += ["concluido_em", "cancelado_em"]
        tipo_evento = TipoEventoDemanda.STATUS
        descricao = (
            f"Situação: {StatusDemanda(anterior).label} → "
            f"{StatusDemanda(novo_status).label}"
        )

    evento = _registrar(
        demanda,
        usuario,
        tipo_evento,
        descricao,
        anteriores={"status": anterior},
        novos={"status": novo_status},
    )
    demanda.status = novo_status
    demanda.save(update_fields=campos)
    return evento


@transaction.atomic
def registrar_observacao(*, demanda: Demanda, usuario, texto: str):
    """Registra o que se sabe sobre a demanda, sem mexer em nenhum campo dela."""
    texto = _texto(texto)
    if not texto:
        raise ErroDeDemanda("Escreva a observação antes de registrar.")

    return _registrar(demanda, usuario, TipoEventoDemanda.OBSERVACAO, texto)


@transaction.atomic
def registrar_evidencia_anexada(*, demanda: Demanda, usuario):
    """Registra na memória da demanda que uma prova passou a sustentá-la.

    Existe para o caso em que a gestão anexa uma foto ou um vídeo sem escrever
    relato e sem mexer na situação: o arquivo precisa de um lugar na linha do
    tempo, e esse lugar é um evento — nunca uma imagem solta, sem quando nem
    por quem. A frase do evento é o próprio nome do tipo; repeti-la aqui seria
    guardar duas versões da mesma explicação.
    """
    return _registrar(demanda, usuario, TipoEventoDemanda.EVIDENCIA, "")


@transaction.atomic
def atualizar_acompanhamento(
    *, demanda: Demanda, usuario, **mudancas
) -> HistoricoDemanda | None:
    """Atualiza o acompanhamento operacional, sem tocar na situação.

    O acompanhamento responde outra pergunta que a situação: o que a demanda
    espera agora, qual é o próximo passo, para quando foi prometida, o que está
    travando e quem a segue de perto. Por isso nada aqui muda o ciclo — esperar
    o fornecedor não move a demanda, previsão vencida não conclui, impedimento
    resolvido não encerra.

    Só os campos que o chamador nomeia são considerados: quem não fala de um
    campo não o apaga, e quem manda vazio está decidindo limpá-lo. Só o que
    realmente mudou vira evento, e o evento guarda o antes e o depois.

    Devolve o evento produzido, ou ``None`` quando nada mudou.
    """
    anteriores, novos, frases = {}, {}, []

    for campo in CAMPOS_DO_ACOMPANHAMENTO:
        if campo not in mudancas:
            continue
        novo = _normalizar_do_acompanhamento(campo, mudancas[campo])
        atual = getattr(demanda, campo)
        if novo == atual:
            continue
        frases.append(
            f"{ROTULOS_DO_ACOMPANHAMENTO[campo]}: "
            f"{_valor_do_acompanhamento(campo, atual)} → "
            f"{_valor_do_acompanhamento(campo, novo)}"
        )
        anteriores[campo] = _guardado_do_acompanhamento(campo, atual)
        novos[campo] = _guardado_do_acompanhamento(campo, novo)
        setattr(demanda, campo, novo)

    if not frases:
        return None

    demanda.save(update_fields=[*anteriores, "atualizado_em"])
    return _registrar(
        demanda,
        usuario,
        TipoEventoDemanda.ACOMPANHAMENTO,
        "; ".join(frases),
        anteriores=anteriores,
        novos=novos,
    )


def _normalizar_do_acompanhamento(campo: str, valor):
    """O valor como ele deve ser guardado — vazio é ausência, nunca sujeira."""
    if campo in ("previsao", "prazo_proxima_acao"):
        return valor or None
    if campo == "responsavel_acompanhamento":
        return valor or None
    if campo == "aguardando_tipo":
        if valor not in AguardandoTipo.values:
            raise ErroDeDemanda("Tipo de espera inválido.")
        return valor
    return _texto(valor)


def _guardado_do_acompanhamento(campo: str, valor):
    """O que vai para o histórico: valor simples, sem objeto nem identificador."""
    if campo in ("previsao", "prazo_proxima_acao"):
        return valor.isoformat() if valor else None
    if campo == "responsavel_acompanhamento":
        return str(valor.pk) if getattr(valor, "pk", None) else None
    return valor if valor else None


def _valor_do_acompanhamento(campo: str, valor) -> str:
    """Um valor do acompanhamento como a gestão lê na frase do evento."""
    if campo == "aguardando_tipo":
        return _rotulo(AguardandoTipo, valor, vazio="ninguém")
    if campo == "previsao":
        return valor.strftime("%d/%m/%Y") if valor else "sem previsão"
    if campo == "prazo_proxima_acao":
        return valor.strftime("%d/%m/%Y") if valor else "sem data"
    if campo == "responsavel_acompanhamento":
        return _nome_de(valor) if valor is not None else "ninguém"
    return valor or "não informado"


def contexto_de_governanca(*, demanda: Demanda, usuario) -> dict:
    """O que a Governança já sabe sobre o local e o equipamento desta demanda.

    Contrato e garantia não são copiados para a demanda: quem responde por eles
    é a Governança, e a pergunta é feita na hora pelos vínculos que já existem —
    contrato que cobre o equipamento, contrato que cobre o local, garantia que
    cobre o equipamento. A leitura é a mesma que a tela do ativo e a do local
    usam: há uma só resposta para esta pergunta, e ela mora na Governança.

    Sem permissão de consultar contratos e garantias ali, a resposta é vazia:
    saber que existe um contrato não é informação de quem não responde por ele.

    Sobre o contrato aplicável, esta leitura acrescenta o prazo contratual: o
    SLA estruturado do contrato projetado a partir do marco da demanda. É a
    única ponte entre contrato e prazo — sem SLA no contrato, não há data, e
    nada é deduzido do texto do documento.
    """
    from apps.governanca.consultas import vigencia_dos_vinculos

    contexto = vigencia_dos_vinculos(
        usuario=usuario,
        condominio=getattr(demanda, "condominio", None),
        equipamento_id=demanda.equipamento_id,
        local_id=demanda.local_id,
    )

    base = (
        timezone.localdate(demanda.criado_em)
        if demanda.criado_em
        else timezone.localdate()
    )
    prazo_do_contrato = None
    for contrato in contexto.get("contratos", []):
        contrato["prazo_contratual"] = prazo_contratual_do_sla(
            contrato.get("sla_dias"), base
        )
        if prazo_do_contrato is None and contrato["prazo_contratual"] is not None:
            prazo_do_contrato = contrato["prazo_contratual"]

    contexto["prazo_do_contrato"] = prazo_do_contrato
    contexto["prazo_vem_do_contrato"] = bool(
        demanda.prazo and prazo_do_contrato and demanda.prazo == prazo_do_contrato
    )
    return contexto


def prazo_contratual_do_sla(sla_dias, base):
    """A data formal que o SLA do contrato projeta a partir do marco do chamado.

    O prazo contratual nasce do SLA estruturado e de nada mais: sem número no
    contrato, não há data. É a base formal de onde o prazo de uma demanda pode
    vir — nunca uma data inventada pela prioridade.
    """
    if not sla_dias:
        return None
    return base + timedelta(days=sla_dias)


@transaction.atomic
def registrar_comunicado(demanda, usuario, descricao: str) -> HistoricoDemanda:
    """Anota, na memória da demanda, que um comunicado foi gerado a partir dela.

    Este é o único evento que a demanda recebe de fora da própria operação. O
    fato pertence à comunicação, e por isso não se copia para cá o histórico
    dela: registra-se a lembrança de que existiu um comunicado, e nada mais. A
    autoridade sobre o texto oficial continua sendo a comunicação.
    """
    texto = _texto(descricao) or "Comunicado gerado para os moradores"
    return _registrar(demanda, usuario, TipoEventoDemanda.COMUNICADO, texto)

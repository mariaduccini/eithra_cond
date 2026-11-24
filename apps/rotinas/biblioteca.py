"""Sugestões e decisões humanas sobre modelos de atividades programadas."""
import re
import unicodedata

from django.db import transaction

from apps.core.recorrencia import Periodicidade

from .constants import TipoRotina
from .models import (
    DecisaoModeloAtividade,
    EstadoModeloAtividade,
    ModeloAtividadeProgramada,
    RotinaRecorrente,
)
from .services import (
    ErroDeRotina,
    criar_checklist,
    criar_item_do_checklist,
    criar_rotina,
)


class ErroDeBiblioteca(Exception):
    """Uma decisão sobre um modelo precisa ser revista pela pessoa."""


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return " ".join(re.sub(r"[^\w]+", " ", texto.casefold()).split())


def _contem_termo(texto, termos):
    normalizado = _normalizar(texto)
    return any(
        re.search(rf"(?<!\w){re.escape(_normalizar(termo))}(?!\w)", normalizado)
        for termo in termos
        if _normalizar(termo)
    )


def _equipamentos_compativeis(modelo, equipamentos):
    encontrados = []
    for equipamento in equipamentos:
        tipo = equipamento.tipo_equipamento.nome if equipamento.tipo_equipamento_id else ""
        textos = (tipo, equipamento.tipo_texto, equipamento.nome)
        if any(_contem_termo(texto, modelo.gatilhos_equipamento) for texto in textos):
            encontrados.append(equipamento)
    return encontrados


def _locais_compativeis(modelo, locais):
    encontrados = []
    for local in locais:
        tipo = local.tipo_local.nome if local.tipo_local_id else ""
        tipo_legado = local.get_tipo_display()
        if any(
            _contem_termo(texto, modelo.gatilhos_local)
            for texto in (local.nome, tipo, tipo_legado)
        ):
            encontrados.append(local)
    return encontrados


def _evidencias(modelo, equipamentos, locais):
    """Só sugere por correspondência explícita em cadastros deste condomínio."""
    por_equipamento = _equipamentos_compativeis(modelo, equipamentos)
    por_local = _locais_compativeis(modelo, locais)

    if modelo.gatilhos_equipamento and not por_equipamento:
        return []
    if modelo.gatilhos_local and not por_local:
        return []
    if modelo.gatilhos_equipamento or modelo.gatilhos_local:
        partes = []
        if por_equipamento:
            partes.append(
                "Equipamento cadastrado: "
                + ", ".join(item.nome for item in por_equipamento[:3])
            )
        if por_local:
            partes.append(
                "Local cadastrado: " + ", ".join(item.nome for item in por_local[:3])
            )
        return partes
    if modelo.sugerir_sempre:
        return ["Sugestão geral para revisar o plano do condomínio."]
    return []


def _rotina_existente(modelo, rotinas):
    """Detecção conservadora por título exato; não associa por semelhança."""
    equivalentes = {_normalizar(modelo.titulo)}
    equivalentes.update(
        _normalizar(titulo) for titulo in modelo.titulos_equivalentes
    )
    return next(
        (
            rotina
            for rotina in rotinas
            if _normalizar(rotina.titulo) in equivalentes
        ),
        None,
    )


def modelos_para_condominio(condominio):
    """Devolve sugestões sustentadas e os demais modelos para revisão manual."""
    equipamentos = list(
        condominio.equipamentos.ativos()
        .select_related("tipo_equipamento", "local")
        .order_by("nome")
    )
    locais = list(
        condominio.locais.ativos().select_related("tipo_local").order_by("nome")
    )
    rotinas = list(
        RotinaRecorrente.objects.filter(condominio=condominio)
    )
    decisoes = {
        decisao.modelo_id: decisao
        for decisao in DecisaoModeloAtividade.objects.filter(condominio=condominio)
        .select_related("rotina")
    }
    sugestoes = []
    demais = []

    for modelo in ModeloAtividadeProgramada.objects.ativos():
        decisao = decisoes.get(modelo.pk)
        evidencias = _evidencias(modelo, equipamentos, locais)
        existente = (
            _rotina_existente(modelo, rotinas)
            if decisao is None or decisao.estado == EstadoModeloAtividade.DISPONIVEL
            else None
        )
        item = {
            "modelo": modelo,
            "estado": (
                decisao.estado if decisao else EstadoModeloAtividade.DISPONIVEL
            ),
            "decisao": decisao,
            "evidencias": evidencias,
            "rotina_existente": existente,
            "sugerido": bool(evidencias),
        }
        (sugestoes if item["sugerido"] else demais).append(item)
    return sugestoes, demais


@transaction.atomic
def ativar_modelo(*, modelo, condominio, usuario, dados):
    """Copia o modelo para checklist local e cria rotina pelo serviço canônico."""
    if not modelo.ativo:
        raise ErroDeBiblioteca("Este modelo não está disponível para ativação.")

    decisao, _ = DecisaoModeloAtividade.objects.get_or_create(
        condominio=condominio,
        modelo=modelo,
        defaults={"estado": EstadoModeloAtividade.DISPONIVEL},
    )
    decisao = DecisaoModeloAtividade.objects.select_for_update().get(pk=decisao.pk)
    if decisao.estado == EstadoModeloAtividade.ATIVADA:
        raise ErroDeBiblioteca("Este modelo já foi ativado neste condomínio.")
    if decisao.estado == EstadoModeloAtividade.NAO_APLICA:
        raise ErroDeBiblioteca(
            "Este modelo foi marcado como não aplicável. Reabra-o antes de ativar."
        )

    if not dados.get("confirmar_frequencia"):
        raise ErroDeBiblioteca(
            "Confirme a frequência ou a ocorrência única antes de ativar."
        )
    if modelo.condicional and not dados.get("confirmar_condicao"):
        raise ErroDeBiblioteca(
            "Confirme que a estrutura indicada existe neste condomínio."
        )
    if (
        modelo.exige_fornecedor_especializado
        and not dados.get("confirmar_fornecedor")
    ):
        raise ErroDeBiblioteca(
            "Confirme a contratação de fornecedor especializado antes de ativar."
        )
    if (
        modelo.exige_validacao_tecnica
        and not dados.get("confirmar_validacao_tecnica")
    ):
        raise ErroDeBiblioteca(
            "Confirme a frequência com profissional ou documentação técnica."
        )

    existente = _rotina_existente(
        modelo,
        RotinaRecorrente.objects.filter(condominio=condominio),
    )
    if existente is not None:
        raise ErroDeBiblioteca(
            "Já existe uma atividade com este título. Confira a lista antes "
            "de criar outra."
        )

    inicio = dados["data_inicio"]
    unica = dados.get("execucao_unica", False)
    periodicidade_tipo = dados.get("periodicidade_tipo")
    intervalo = dados.get("intervalo") or 1
    dia_semana = dados.get("dia_semana")
    dia_mes = dados.get("dia_mes")
    mes_ano = dados.get("mes_ano")
    data_fim = dados.get("data_fim")

    if unica:
        periodicidade_tipo = Periodicidade.MENSAL
        intervalo = 1
        dia_mes = inicio.day
        mes_ano = None
        dia_semana = None
        data_fim = inicio
    elif not periodicidade_tipo:
        raise ErroDeBiblioteca(
            "Escolha uma periodicidade ou marque que será uma única ocorrência."
        )

    checklist = None
    if modelo.checklist_sugerido:
        checklist = criar_checklist(
            condominio=condominio,
            criado_por=usuario,
            nome=f"Checklist: {dados['titulo']}",
            descricao=modelo.descricao,
        )
        for ordem, descricao in enumerate(modelo.checklist_sugerido):
            criar_item_do_checklist(
                checklist=checklist, descricao=descricao, ordem=ordem
            )

    try:
        rotina = criar_rotina(
            condominio=condominio,
            criado_por=usuario,
            titulo=dados["titulo"],
            descricao=dados["descricao"] or modelo.observacoes,
            tipo=dados["tipo"] or modelo.tipo or TipoRotina.PREVENTIVA,
            periodicidade_tipo=periodicidade_tipo,
            intervalo=intervalo,
            dia_semana=dia_semana,
            dia_mes=dia_mes,
            mes_ano=mes_ano,
            data_inicio=inicio,
            data_fim=data_fim,
            responsavel=dados.get("responsavel"),
            local=dados.get("local"),
            equipamento=dados.get("equipamento"),
            fornecedor=dados.get("fornecedor"),
            checklist_modelo=checklist,
            prioridade=dados.get("prioridade") or modelo.prioridade,
            gera_manutencao=dados.get("gera_manutencao", False),
        )
    except ErroDeRotina as erro:
        raise ErroDeBiblioteca(str(erro)) from erro

    decisao.estado = EstadoModeloAtividade.ATIVADA
    decisao.rotina = rotina
    decisao.decidido_por = usuario
    decisao.observacao = ""
    decisao.save()
    decisao.full_clean()
    return rotina


@transaction.atomic
def marcar_nao_aplica(*, modelo, condominio, usuario, observacao):
    decisao, _ = DecisaoModeloAtividade.objects.get_or_create(
        condominio=condominio,
        modelo=modelo,
        defaults={"estado": EstadoModeloAtividade.DISPONIVEL},
    )
    decisao = DecisaoModeloAtividade.objects.select_for_update().get(pk=decisao.pk)
    if decisao.estado == EstadoModeloAtividade.ATIVADA:
        raise ErroDeBiblioteca("Um modelo ativado não pode ser dispensado.")
    decisao.estado = EstadoModeloAtividade.NAO_APLICA
    decisao.rotina = None
    decisao.decidido_por = usuario
    decisao.observacao = (observacao or "").strip()
    decisao.save()
    decisao.full_clean()
    return decisao


@transaction.atomic
def reabrir_modelo(*, modelo, condominio, usuario):
    decisao = DecisaoModeloAtividade.objects.select_for_update().filter(
        condominio=condominio, modelo=modelo
    ).first()
    if decisao is None or decisao.estado != EstadoModeloAtividade.NAO_APLICA:
        raise ErroDeBiblioteca("Este modelo não está marcado como não aplicável.")
    decisao.estado = EstadoModeloAtividade.DISPONIVEL
    decisao.decidido_por = usuario
    decisao.observacao = ""
    decisao.save()
    decisao.full_clean()
    return decisao

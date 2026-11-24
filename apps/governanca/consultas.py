"""Leituras de Governança que outras telas consultam sem copiar nada.

Contrato e garantia não são copiados para a demanda, para o ativo nem para o
local: quem responde por eles é a Governança, e a pergunta é feita na hora pelos
vínculos que já existem — contrato que cobre o equipamento, contrato que cobre o
local, garantia que cobre o equipamento. Guardar um atalho no outro domínio
criaria duas respostas para a mesma pergunta, e uma delas envelheceria.

Esta é a única leitura que responde "o que cobre este equipamento / este local?".
Quem pergunta entrega o que tem — uma demanda diz o equipamento e o local dela,
a tela do ativo diz o equipamento, a do local diz o local — e recebe a mesma
resposta, na mesma forma.

O import dos models é local porque nenhum domínio conhece a Governança de
antemão; quem pergunta só o faz quando alguém abre a tela naquele condomínio.
"""
from .constants import SituacaoVigencia


def vigencia_dos_vinculos(
    *,
    usuario,
    condominio,
    equipamento_id=None,
    local_id=None,
) -> dict:
    """O que a Governança já sabe sobre este equipamento e este local.

    Sem permissão de consultar contratos e garantias ali, a resposta é vazia:
    saber que existe um contrato não é informação de quem não responde por ele.

    A mesma forma de sempre: ``pode_consultar``, ``contratos`` e ``garantias``,
    cada lista já na ordem de atenção da Governança e com o tom que a tela usa.
    """
    from .models import (
        ContratoEquipamento,
        ContratoFornecedor,
        ContratoLocal,
        Garantia,
        GarantiaEquipamento,
    )
    from .permissoes import pode_ver_governanca

    resposta = {"pode_consultar": False, "contratos": [], "garantias": []}
    if not pode_ver_governanca(usuario, condominio):
        return resposta

    resposta["pode_consultar"] = True

    contratos_vinculados = set()
    if equipamento_id:
        contratos_vinculados.update(
            ContratoEquipamento.objects.filter(
                equipamento_id=equipamento_id
            ).values_list("contrato_id", flat=True)
        )
    if local_id:
        contratos_vinculados.update(
            ContratoLocal.objects.filter(local_id=local_id).values_list(
                "contrato_id", flat=True
            )
        )
    resposta["contratos"] = [
        {
            "objeto": contrato.objeto,
            "fornecedor": str(contrato.fornecedor),
            "fim": contrato.data_fim,
            "situacao": SituacaoVigencia(contrato.situacao).label,
            "tom": contrato.tom,
            # O SLA estruturado do contrato, quando existe. É a única fonte
            # formal de prazo contratual: ausente, o contrato continua vigente e
            # não origina prazo — nada é deduzido do texto do documento.
            "sla_dias": contrato.sla_dias,
        }
        for contrato in ContratoFornecedor.objects.ativos()
        .filter(pk__in=contratos_vinculados)
        .select_related("fornecedor")
        .na_ordem_de_atencao()
    ]

    garantias_vinculadas = set()
    if equipamento_id:
        garantias_vinculadas.update(
            GarantiaEquipamento.objects.filter(
                equipamento_id=equipamento_id
            ).values_list("garantia_id", flat=True)
        )
    resposta["garantias"] = [
        {
            "descricao": garantia.descricao,
            "fornecedor": str(garantia.fornecedor) if garantia.fornecedor_id else "",
            "fim": garantia.data_fim,
            "situacao": SituacaoVigencia(garantia.situacao).label,
            "tom": garantia.tom,
        }
        for garantia in Garantia.objects.ativos()
        .filter(pk__in=garantias_vinculadas)
        .select_related("fornecedor")
        .na_ordem_de_atencao()
    ]

    return resposta

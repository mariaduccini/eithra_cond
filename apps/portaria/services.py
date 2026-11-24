"""Operações do domínio da Portaria."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cadastros.models import Fornecedor

from .constants import OrigemPortaria, StatusAutorizacaoPortaria
from .models import AcessoPortaria, AutorizacaoPortaria


class ErroDePortaria(Exception):
    """Uma decisão da portaria que a operação precisa saber."""


# Quantas empresas a portaria lembra de uma vez. É sugestão, não catálogo: a
# lista serve para não reescrever o que já passou por aqui, e quem não achar
# digita do mesmo jeito — nada depende dela.
QUANTAS_EMPRESAS = 20


def empresas_conhecidas(condominio, limite: int = QUANTAS_EMPRESAS) -> list[str]:
    """As empresas que já passaram por esta portaria, da mais recente à antiga.

    O campo Empresa é texto livre: não existe cadastro prévio e a portaria não
    bloqueia a passagem de quem não está na base. O que existe é memória — o que
    já foi registrado volta como sugestão, para o próximo prestador da mesma
    empresa não ser digitado de novo.

    Grafias iguais sem diferenciar maiúsculas viram uma só, e a que fica é a
    mais recente: quem escreveu "Bombas Eithra" da última vez vê o que escreveu.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return []
    cadastradas = list(
        Fornecedor.objects.do_condominio(condominio)
        .ativos()
        .order_by("nome_razao_social")
        .values_list("nome_fantasia", "nome_razao_social")[:limite]
    )
    registradas = list(
        AcessoPortaria.objects.do_condominio(condominio)
        .exclude(empresa="")
        .order_by("-entrada_em")
        .values_list("empresa", flat=True)[:limite]
    )
    esperadas = list(
        AutorizacaoPortaria.objects.do_condominio(condominio)
        .exclude(empresa="")
        .order_by("-criado_em")
        .values_list("empresa", flat=True)[:limite]
    )
    conhecidas: dict[str, str] = {}
    for fantasia, razao in cadastradas:
        nome = fantasia or razao or ""
        if nome:
            conhecidas.setdefault(nome.casefold(), nome)
    for nome in registradas + esperadas:
        conhecidas.setdefault(nome.casefold(), nome)
    return list(conhecidas.values())[:limite]


def _primeiro_recado(erro: ValidationError) -> str:
    if hasattr(erro, "message_dict"):
        for mensagens in erro.message_dict.values():
            if mensagens:
                return mensagens[0]
    if erro.messages:
        return erro.messages[0]
    return "Não foi possível registrar a operação."


def _validar(instancia):
    try:
        instancia.full_clean()
    except ValidationError as erro:
        raise ErroDePortaria(_primeiro_recado(erro)) from erro


def _aplicar(objeto, dados):
    for campo, valor in dados.items():
        setattr(objeto, campo, valor)
    _validar(objeto)
    objeto.save()
    return objeto


def _dados_de_autorizacao(dados: dict) -> dict:
    """Só o que pertence ao domínio da autorização."""
    permitidos = {
        "tipo_acesso",
        "status",
        "pessoa",
        "autorizador_pessoa",
        "nome",
        "autorizador_nome",
        "meio_autorizacao",
        "telefone",
        "documento",
        "empresa",
        "unidade",
        "local",
        "veiculo",
        "placa",
        "motivo",
        "observacao",
        "natureza_objeto",
        "instrucao_entrega",
        "inicio_previsto",
        "fim_previsto",
        "origem",
        "resultado_registrado_por",
        "resultado_registrado_em",
    }
    return {chave: valor for chave, valor in dados.items() if chave in permitidos}


@transaction.atomic
def registrar_esperado(
    *,
    condominio,
    usuario,
    dados: dict,
    origem: str = OrigemPortaria.PORTARIA_MANUAL,
) -> AutorizacaoPortaria:
    """Registra uma autorização esperada — o que a portaria já sabia antes.

    A origem diz de onde veio o aviso; quem digita na portaria não é o único
    caminho, e uma autorização lida de e-mail continua sendo uma autorização
    registrada pela portaria. O padrão segue sendo o registro manual.
    """
    autorizacao = AutorizacaoPortaria(
        condominio=condominio,
        registrado_por=usuario,
        origem=origem,
    )
    return _aplicar(autorizacao, dados)


@transaction.atomic
def registrar_chegada(*, autorizacao: AutorizacaoPortaria, usuario) -> AcessoPortaria:
    if autorizacao.status != StatusAutorizacaoPortaria.AUTORIZADO:
        raise ErroDePortaria("Esta pessoa não está autorizada para entrada.")
    if autorizacao.esta_expirada:
        raise ErroDePortaria("O horário desta autorização já expirou.")

    return _abrir_presenca(autorizacao=autorizacao, usuario=usuario)


def _abrir_presenca(*, autorizacao: AutorizacaoPortaria, usuario) -> AcessoPortaria:
    """Abre a presença física a partir de uma autorização já decidida."""
    acesso = AcessoPortaria(
        condominio=autorizacao.condominio,
        autorizacao=autorizacao,
        tipo_acesso=autorizacao.tipo_acesso,
        origem=autorizacao.origem,
        pessoa=autorizacao.pessoa,
        unidade=autorizacao.unidade,
        local=autorizacao.local,
        veiculo=autorizacao.veiculo,
        nome=autorizacao.nome,
        telefone=autorizacao.telefone,
        documento=autorizacao.documento,
        empresa=autorizacao.empresa,
        placa=autorizacao.placa,
        motivo=autorizacao.motivo,
        observacao=autorizacao.observacao,
        natureza_objeto=autorizacao.natureza_objeto,
        instrucao_entrega=autorizacao.instrucao_entrega,
        entrada_registrada_por=usuario,
    )
    try:
        _validar(acesso)
        acesso.save()
    except IntegrityError as erro:
        raise ErroDePortaria("Esta presença já está aberta na portaria.") from erro
    return acesso


@transaction.atomic
def registrar_chegada_com_resultado(
    *, condominio, usuario, dados: dict
) -> tuple[AutorizacaoPortaria, AcessoPortaria | None]:
    """Registra a chegada e o resultado do contato com quem autoriza.

    A decisão pode terminar em entrada, sem resposta, não autorização ou
    simples pendência. Só "Autorizado" abre presença; os demais resultados
    registram o contexto e param aí.
    """
    agora = timezone.localtime()
    autorizacao = AutorizacaoPortaria(
        condominio=condominio,
        registrado_por=usuario,
        origem=OrigemPortaria.CHEGADA_NAO_PREVISTA,
        inicio_previsto=agora,
        fim_previsto=agora + timedelta(hours=2),
    )
    dados_da_chegada = {**dados}
    # Quem chega sem autorização prévia é sempre o terceiro que está na
    # portaria agora. Mesmo que algum chamador envie uma pessoa cadastrada por
    # engano, a chegada não prevista não reaproveita esse vínculo humano.
    dados_da_chegada["pessoa"] = None
    _aplicar(
        autorizacao,
        _dados_de_autorizacao(
            {
                **dados_da_chegada,
                "resultado_registrado_por": usuario,
                "resultado_registrado_em": agora,
            }
        ),
    )
    if autorizacao.status != StatusAutorizacaoPortaria.AUTORIZADO:
        return autorizacao, None
    return autorizacao, _abrir_presenca(autorizacao=autorizacao, usuario=usuario)


@transaction.atomic
def verificar_autorizacao(
    *, autorizacao: AutorizacaoPortaria, usuario, dados: dict
) -> tuple[AutorizacaoPortaria, AcessoPortaria | None]:
    """Atualiza o resultado de uma chegada ainda sem conclusão definitiva."""
    autorizacao = AutorizacaoPortaria.objects.select_for_update().get(pk=autorizacao.pk)
    agora = timezone.localtime()
    _aplicar(
        autorizacao,
        _dados_de_autorizacao(
            {
            **dados,
            "resultado_registrado_por": usuario,
            "resultado_registrado_em": agora,
            }
        ),
    )
    if autorizacao.status != StatusAutorizacaoPortaria.AUTORIZADO:
        return autorizacao, None
    if AcessoPortaria.objects.filter(autorizacao=autorizacao).exists():
        raise ErroDePortaria("A entrada desta pessoa já foi registrada.")
    return autorizacao, _abrir_presenca(autorizacao=autorizacao, usuario=usuario)


@transaction.atomic
def registrar_chegada_nao_prevista(
    *, condominio, usuario, dados: dict
) -> AutorizacaoPortaria:
    """Terceiro que chegou sem autorização prévia.

    Não abre entrada. A chegada entra como aguardando autorização, para que a
    portaria procure quem responde pela unidade antes de liberar a passagem.

    Quem chegou é sempre o terceiro — um nome solto, sem vínculo com o cadastro
    da comunidade. Quem mora autoriza a entrada, não a recebe; por isso esta
    operação nunca aponta para uma pessoa cadastrada, mesmo que algum dado
    extra tente trazê-la.
    """
    autorizacao, _ = registrar_chegada_com_resultado(
        condominio=condominio,
        usuario=usuario,
        dados={**dados, "status": StatusAutorizacaoPortaria.AGUARDANDO},
    )
    return autorizacao


@transaction.atomic
def registrar_saida(*, acesso: AcessoPortaria, usuario) -> AcessoPortaria:
    if acesso.saida_em is not None:
        raise ErroDePortaria("Esta saída já foi registrada.")

    acesso.saida_em = timezone.localtime()
    acesso.saida_registrada_por = usuario
    _validar(acesso)
    acesso.save(update_fields=["saida_em", "saida_registrada_por", "atualizado_em"])
    return acesso

"""Operações de acesso a condomínio.

Dar acesso a alguém é uma decisão sobre pessoas, e por isso ela não é um
formulário solto de cadastro. Aqui ficam as regras que não podem depender da
tela: quem entra, com qual papel, o que não pode ser feito e o que fica
registrado para sempre.

Quatro ideias governam o arquivo:

1. **O vínculo é do condomínio.** Ativar ou inativar acesso mexe em
   ``AcessoUsuarioCondominio``, nunca em ``User.is_active``. A conta global não
   é alterada por uma decisão que vale só para um condomínio.
2. **Nada é apagado.** O vínculo é desativado; o histórico de quem mudou o quê
   é acrescentado e nunca reescrito.
3. **O condomínio não fica sem gestão.** O último vínculo ativo de
   administração não pode ser inativado nem rebaixado.
4. **O acesso é decidido no backend.** A tela não autoriza nada; ela pede, e
   aqui se decide.

Toda operação roda em uma transação: a alteração do vínculo e o registro do
histórico acontecem juntos, ou não acontecem.
"""
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.condominios.constants import EventoAcesso, PAPEIS_DE_GESTAO, Papel
from apps.condominios.models import AcessoUsuarioCondominio, HistoricoAcessoCondominio


class ErroDeAcesso(Exception):
    """Uma decisão do domínio sobre acesso que a gestão precisa saber."""


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _retrato(acesso: AcessoUsuarioCondominio) -> dict:
    """O estado do vínculo como ele fica guardado no histórico."""
    return {"papel": acesso.papel, "ativo": acesso.ativo}


def _registrar(
    *,
    acesso: AcessoUsuarioCondominio,
    executor,
    evento: str,
    anteriores: dict | None,
    novos: dict | None,
) -> HistoricoAcessoCondominio:
    """Acrescenta uma linha ao histórico. Aplicação nunca reescreve nem apaga."""
    if executor is None or not getattr(executor, "pk", None):
        raise ErroDeAcesso(
            "Não foi possível identificar quem está fazendo a alteração."
        )
    return HistoricoAcessoCondominio.objects.create(
        acesso=acesso,
        usuario_executor=executor,
        evento=evento,
        dados_anteriores=anteriores,
        dados_novos=novos,
    )


def _travado(acesso: AcessoUsuarioCondominio) -> AcessoUsuarioCondominio:
    """Relê o vínculo travando a linha, para duas alterações não se cruzarem."""
    return AcessoUsuarioCondominio.objects.select_for_update().get(pk=acesso.pk)


def _eh_o_proprio(acesso: AcessoUsuarioCondominio, executor) -> bool:
    """Diz se quem está alterando é o dono do vínculo alterado."""
    return bool(
        executor is not None
        and getattr(executor, "pk", None)
        and acesso.usuario_id == executor.pk
    )


def _eh_ultimo_gestor(acesso: AcessoUsuarioCondominio) -> bool:
    """Diz se este é o único vínculo ativo de gestão do condomínio.

    Chamado antes de tirar o vínculo de circulação: se não houver outro, tirar
    este deixaria o condomínio sem ninguém que o administre.
    """
    outros = AcessoUsuarioCondominio.objects.filter(
        condominio_id=acesso.condominio_id,
        ativo=True,
        papel__in=PAPEIS_DE_GESTAO,
    ).exclude(pk=acesso.pk)
    return not outros.exists()


@transaction.atomic
def adicionar_acesso(
    *,
    condominio,
    email: str,
    nome_completo: str = "",
    papel: str,
    executor,
    senha: str = "",
) -> AcessoUsuarioCondominio:
    """Dá acesso a uma pessoa a este condomínio.

    Se o e-mail já pertence a alguém conhecido, a identidade existente é usada
    como está. Se não, cria-se a identidade — sem senha utilizável por padrão:
    a pessoa entra quando existir um caminho seguro de primeiro acesso, e não
    por uma senha que alguém de fora escolheria por ela.

    A exceção é declarada: quando a gestão escolhe definir a senha agora, ela
    vem por ``senha`` e é gravada pelo mesmo ``set_password`` de qualquer outra
    porta de entrada. A senha nunca é guardada em texto, nunca é registrada no
    histórico, e o fato fica registrado como ``SENHA_DEFINIDA_PELA_GESTAO``.

    Um vínculo já existente e inativo é reativado com o papel pedido, e não
    duplicado: o mesmo par pessoa/condomínio tem um histórico só.
    """
    email = (email or "").strip().lower()
    if not email:
        raise ErroDeAcesso("Informe o e-mail da pessoa que vai ter acesso.")
    if papel not in Papel.values:
        raise ErroDeAcesso("Escolha o papel desta pessoa no condomínio.")
    if condominio is None or not getattr(condominio, "pk", None):
        raise ErroDeAcesso("Não foi possível identificar o condomínio.")

    # A senha nunca é normalizada: espaço faz parte do que a pessoa escolheu.
    senha = senha or ""
    definir_senha_agora = bool(senha)

    Usuario = get_user_model()
    usuario = Usuario.objects.filter(email__iexact=email).first()

    if usuario is None:
        nome = _texto(nome_completo)
        if not nome:
            raise ErroDeAcesso("Diga o nome da pessoa que vai ter acesso.")
        usuario = Usuario(email=email, full_name=nome)
        if definir_senha_agora:
            usuario.set_password(senha)
        else:
            # Sem senha utilizável: ninguém define senha por outra pessoa, e não
            # existe senha provisória trafegando em texto aberto.
            usuario.set_unusable_password()
        usuario.save()
    elif definir_senha_agora:
        # A pessoa já é conhecida; o que a gestão pediu agora foi uma senha nova.
        usuario.set_password(senha)
        usuario.save(update_fields=["password", "updated_at"])

    vinculo = (
        AcessoUsuarioCondominio.objects.select_for_update()
        .filter(usuario=usuario, condominio=condominio)
        .first()
    )

    if vinculo is not None and vinculo.ativo:
        raise ErroDeAcesso("Esta pessoa já tem acesso a este condomínio.")

    if vinculo is None:
        vinculo = AcessoUsuarioCondominio.objects.create(
            usuario=usuario,
            condominio=condominio,
            papel=papel,
            ativo=True,
        )
        _registrar(
            acesso=vinculo,
            executor=executor,
            evento=EventoAcesso.CRIADO,
            anteriores=None,
            novos=_retrato(vinculo),
        )
        if definir_senha_agora:
            _registrar(
                acesso=vinculo,
                executor=executor,
                evento=EventoAcesso.SENHA_DEFINIDA_PELA_GESTAO,
                anteriores=None,
                novos=_retrato(vinculo),
            )
        return vinculo

    anteriores = _retrato(vinculo)
    vinculo.papel = papel
    vinculo.ativo = True
    vinculo.save(update_fields=["papel", "ativo", "atualizado_em"])
    _registrar(
        acesso=vinculo,
        executor=executor,
        evento=EventoAcesso.ATIVADO,
        anteriores=anteriores,
        novos=_retrato(vinculo),
    )
    if definir_senha_agora:
        _registrar(
            acesso=vinculo,
            executor=executor,
            evento=EventoAcesso.SENHA_DEFINIDA_PELA_GESTAO,
            anteriores=None,
            novos=_retrato(vinculo),
        )
    return vinculo


@transaction.atomic
def alterar_papel(
    *, acesso: AcessoUsuarioCondominio, papel: str, executor
) -> AcessoUsuarioCondominio:
    """Muda o papel de uma pessoa neste condomínio.

    Ninguém muda o próprio papel: quem tem acesso não amplia o próprio poder
    sozinho. E o último acesso de gestão não pode ser rebaixado — o condomínio
    ficaria sem ninguém que o administre.
    """
    if papel not in Papel.values:
        raise ErroDeAcesso("Escolha um papel válido.")

    vinculo = _travado(acesso)
    if papel == vinculo.papel:
        return vinculo

    if _eh_o_proprio(vinculo, executor):
        raise ErroDeAcesso("Você não pode alterar o seu próprio papel.")

    if (
        vinculo.ativo
        and vinculo.papel in PAPEIS_DE_GESTAO
        and papel not in PAPEIS_DE_GESTAO
        and _eh_ultimo_gestor(vinculo)
    ):
        raise ErroDeAcesso(
            "Este é o único acesso de gestão do condomínio. "
            "Dê a administração a outra pessoa antes de mudar este papel."
        )

    anteriores = _retrato(vinculo)
    vinculo.papel = papel
    vinculo.save(update_fields=["papel", "atualizado_em"])
    _registrar(
        acesso=vinculo,
        executor=executor,
        evento=EventoAcesso.PAPEL_ALTERADO,
        anteriores=anteriores,
        novos=_retrato(vinculo),
    )
    return vinculo


@transaction.atomic
def definir_ativo_acesso(
    *, acesso: AcessoUsuarioCondominio, ativo: bool, executor
) -> AcessoUsuarioCondominio:
    """Ativa ou inativa o acesso de uma pessoa a este condomínio.

    Desativar é a forma de retirar o acesso sem apagar o vínculo: o histórico
    de quem teve acesso a quê continua legível. A conta global da pessoa não é
    tocada — ela pode continuar existindo, e ter acesso a outros condomínios.
    """
    vinculo = _travado(acesso)
    ativo = bool(ativo)
    if ativo == vinculo.ativo:
        return vinculo

    if not ativo and vinculo.papel in PAPEIS_DE_GESTAO and _eh_ultimo_gestor(vinculo):
        raise ErroDeAcesso(
            "Este é o único acesso de gestão do condomínio. "
            "Dê a administração a outra pessoa antes de retirar este acesso."
        )

    anteriores = _retrato(vinculo)
    vinculo.ativo = ativo
    vinculo.save(update_fields=["ativo", "atualizado_em"])
    _registrar(
        acesso=vinculo,
        executor=executor,
        evento=EventoAcesso.ATIVADO if ativo else EventoAcesso.INATIVADO,
        anteriores=anteriores,
        novos=_retrato(vinculo),
    )
    return vinculo


@transaction.atomic
def registrar_instrucoes_de_senha_enviadas(
    *, acesso: AcessoUsuarioCondominio, executor, primeiro_acesso: bool
) -> HistoricoAcessoCondominio:
    """Registra que a gestão pediu a entrega das instruções de senha.

    O que fica guardado é o pedido e o momento — primeiro acesso ou
    redefinição —, e nada mais. Senha, endereço e token não entram aqui: o
    histórico responde "quem pediu, para quem e quando", sem carregar
    credencial nenhuma.

    Quem pede é sempre alguém identificado: uma ação sobre o acesso de outra
    pessoa não pode ficar sem autor.
    """
    momento = "primeiro_acesso" if primeiro_acesso else "redefinicao"
    return _registrar(
        acesso=acesso,
        executor=executor,
        evento=EventoAcesso.INSTRUCOES_DE_SENHA_ENVIADAS,
        anteriores=None,
        novos={**_retrato(acesso), "momento": momento},
    )


@transaction.atomic
def definir_senha_de_acesso(
    *, acesso: AcessoUsuarioCondominio, senha: str, executor
) -> AcessoUsuarioCondominio:
    """Define uma nova senha para a conta ligada a este vínculo.

    É a única porta em que alguém escolhe a senha de outra pessoa, e ela existe
    para quando não houver e-mail que entregue um endereço de primeiro acesso.
    Quem pode entrar por ela é decisão da tela: aqui o domínio recebe o acesso,
    confere que existe um autor identificado e grava.

    Quem protege a senha é o Django, pelo mesmo ``set_password`` de qualquer
    outra porta de entrada: nada é cifrado nem guardado aqui. O histórico
    registra quem definiu, para quem e quando — e nunca a senha.
    """
    vinculo = _travado(acesso)
    conta = vinculo.usuario
    conta.set_password(senha)
    conta.save(update_fields=["password", "updated_at"])
    _registrar(
        acesso=vinculo,
        executor=executor,
        evento=EventoAcesso.SENHA_DEFINIDA_PELA_GESTAO,
        anteriores=None,
        novos=_retrato(vinculo),
    )
    return vinculo

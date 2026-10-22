"""O primeiro acesso e a troca de senha via endereço temporário.

Quem concede um acesso cria a conta, e não escolhe a senha de ninguém: a própria
pessoa define a dela. Para isso o Eithra emite um endereço temporário, que pode
ser enviado por e-mail e também serve para a troca posterior da senha.

O endereço não fica guardado em lugar nenhum: ele é calculado a partir do estado
da conta. Assim que a senha é definida o endereço perde a validade, porque o
próprio hash da senha entra na conta — e o mesmo acontece quando o prazo
termina. Quem faz essa conta é o Django, com o tempo de validade declarado em
``PASSWORD_RESET_TIMEOUT``.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode


class ErroDeEntregaDoPrimeiroAcesso(Exception):
    """O backend não conseguiu entregar o e-mail do endereço temporário."""


def conta_do_endereco(uidb64, token):
    """A conta que este endereço representa, ou nada quando ele não vale mais.

    Devolve nada em três casos, e todos são a mesma resposta para quem chega:
    o endereço foi adulterado, o prazo terminou ou a senha já foi definida.
    """
    if not uidb64 or not token:
        return None

    Usuario = get_user_model()
    try:
        chave = urlsafe_base64_decode(uidb64).decode()
        conta = Usuario.objects.get(pk=chave)
    except (TypeError, ValueError, OverflowError, Usuario.DoesNotExist):
        return None

    if not default_token_generator.check_token(conta, token):
        return None
    return conta


def endereco_de_primeiro_acesso(*, conta, request) -> str:
    """O endereço completo que a gestão copia e entrega à pessoa."""
    caminho = reverse(
        "accounts:primeiro_acesso",
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(conta.pk)),
            "token": default_token_generator.make_token(conta),
        },
    )
    return request.build_absolute_uri(caminho)


def precisa_definir_senha(conta) -> bool:
    """Diz se a pessoa ainda não tem senha própria.

    É a mesma pergunta que a tela faz para saber se há um endereço a entregar:
    conta criada sem senha utilizável ainda não entrou nunca.
    """
    return not conta.has_usable_password()


def prazo_em_dias() -> int:
    """Quantos dias o endereço vale, como a tela diz."""
    return max(1, settings.PASSWORD_RESET_TIMEOUT // 86400)


def enviar_endereco_por_email(*, conta, request, condominio_nome: str = "") -> None:
    """Entrega por e-mail o endereço para definir ou trocar a senha.

    O mesmo fluxo atende ao primeiro acesso e à troca posterior da senha. A
    mensagem é humana e deixa claro que a senha é sempre escolhida pela própria
    pessoa.
    """
    endereco = endereco_de_primeiro_acesso(conta=conta, request=request)
    primeira_senha = precisa_definir_senha(conta)
    assunto = (
        "Defina sua senha no Eithra"
        if primeira_senha
        else "Troque sua senha no Eithra"
    )
    corpo = _corpo_do_email(
        conta=conta,
        endereco=endereco,
        condominio_nome=condominio_nome,
        primeira_senha=primeira_senha,
    )
    email = EmailMultiAlternatives(
        subject=assunto,
        body=corpo,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[conta.email],
    )
    try:
        email.send(fail_silently=False)
    except Exception as erro:  # pragma: no cover - o teste exercita o chamador.
        raise ErroDeEntregaDoPrimeiroAcesso() from erro


# O backend que apenas imprime a mensagem no console, em vez de entregá-la. É o
# padrão do Django em desenvolvimento, e é o que este projeto usa enquanto o
# envio real não estiver configurado.
BACKEND_DE_CONSOLE = "console.EmailBackend"

RECADO_NEUTRO_DE_REDEFINICAO = (
    "Se houver uma conta com esse e-mail, enviaremos as instruções."
)


def entrega_simulada() -> bool:
    """Diz se o ambiente apenas simula a entrega, escrevendo no console.

    Quando é assim, a tela não pode afirmar que o e-mail chegou: ele não
    chegou. A pessoa esperaria por uma mensagem que nunca vem.
    """
    return settings.EMAIL_BACKEND.endswith(BACKEND_DE_CONSOLE)


def recado_de_entrega(destinatario: str) -> str:
    """O que dizer a quem pediu o envio, conforme o ambiente entrega de fato."""
    if entrega_simulada():
        return (
            "As instruções foram geradas, mas este ambiente não envia e-mail: "
            "o conteúdo aparece no console do servidor."
        )
    return f"Enviamos as instruções para {destinatario}."


def recado_neutro_de_redefinicao() -> str:
    """A resposta de "esqueci minha senha", igual exista ou não a conta.

    A frase é a mesma nos dois casos — é isso que impede alguém de descobrir
    quem tem conta no Eithra sondando o formulário. O que muda, quando o
    ambiente só simula a entrega, é o aviso interno: continua sem revelar se a
    conta existe, porque o aviso é o mesmo para todo mundo.
    """
    if entrega_simulada():
        return (
            f"{RECADO_NEUTRO_DE_REDEFINICAO} Neste ambiente o envio de e-mail "
            "não está configurado: as instruções aparecem no console do servidor."
        )
    return RECADO_NEUTRO_DE_REDEFINICAO


def _corpo_do_email(*, conta, endereco: str, condominio_nome: str, primeira_senha: bool) -> str:
    """Escreve o e-mail transacional do link de senha."""
    saudacao = conta.full_name or conta.email
    contexto = (
        f"para acessar o condomínio {condominio_nome}"
        if condominio_nome
        else "para acessar o Eithra"
    )
    acao = (
        "definir a sua senha"
        if primeira_senha
        else "trocar a sua senha"
    )
    return (
        f"Olá, {saudacao}.\n\n"
        f"Você recebeu este e-mail {contexto}.\n"
        f"Use o endereço abaixo para {acao}:\n\n"
        f"{endereco}\n\n"
        f"Este endereço vale por {prazo_em_dias()} dias e deixa de funcionar assim "
        "que a nova senha for salva.\n\n"
        "Se você não estava esperando este acesso, ignore esta mensagem.\n"
    )

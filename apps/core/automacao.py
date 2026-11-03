"""Quem assina as ações que o Eithra faz sozinho.

Nem toda ação do sistema tem uma pessoa por trás. Quando o gerador periódico
cria as preventivas que venceram, ninguém clicou nada — mas a manutenção nasce
com autor, e o histórico precisa poder dizer de onde ela veio. Uma geração
automática não pode ficar anônima e, pior, não pode ser assinada por quem não
fez nada: isso destruiria a rastreabilidade que o resto do Eithra mantém.

A saída é um ator declarado — uma pessoa técnica, configurada na instalação, que
assina o que o sistema fez por conta própria. Ela não é criada aqui nem
escolhida entre as que existem: se não estiver configurada, ou se o que está
configurado não existir, a ação automática não acontece e diz por quê. Nenhuma
pessoa é escolhida por conta própria — o sistema não decide sozinho quem
"provavelmente" fez aquilo.

Quem assina a automação é escolha de quem instala, e não do código: por isso o
e-mail vive só no ambiente (``EITHRA_AUTOMATION_USER_EMAIL``), como o resto das
credenciais e escolhas de instalação. Nada é fixado aqui.

Este módulo é neutro de propósito: qualquer ação automática futura que precise
de autoria persistida pergunta a mesma coisa, e a resposta é uma só.
"""
from django.conf import settings
from django.contrib.auth import get_user_model


class ErroDeAutomacao(Exception):
    """Uma ação automática não pôde acontecer: falta um ator declarado."""


def email_do_ator_automatico() -> str:
    """O e-mail configurado para o ator técnico, ou vazio quando não há."""
    return (getattr(settings, "EITHRA_AUTOMATION_USER_EMAIL", "") or "").strip()


def resolver_ator_automatico():
    """A pessoa que assina as ações automáticas do Eithra.

    Recusa quando não há ator configurado ou quando o ator configurado não
    existe: nos dois casos a ação automática não pode ser assinada com
    honestidade, e é melhor parar e dizer por quê do que atribuir a autoria a
    qualquer pessoa que estivesse por ali.
    """
    email = email_do_ator_automatico()
    if not email:
        raise ErroDeAutomacao(
            "Nenhum ator automático está configurado neste ambiente. Defina "
            "EITHRA_AUTOMATION_USER_EMAIL com quem assina as ações automáticas "
            "do Eithra."
        )

    modelo = get_user_model()
    usuario = modelo.objects.filter(**{modelo.USERNAME_FIELD: email}).first()
    if usuario is None:
        raise ErroDeAutomacao(
            "O ator automático indicado em EITHRA_AUTOMATION_USER_EMAIL não "
            "existe no Eithra; nenhuma ação automática será assinada por outra "
            "pessoa."
        )
    return usuario

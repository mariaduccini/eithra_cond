"""Cria os usuários locais de teste do piloto do Eithra.

O piloto precisa de uma conta para cada perfil que o sistema conhece, e criá-las
à mão é lento e fácil de errar. Este comando prepara o ambiente de uma vez: sete
contas de teste, todas no condomínio piloto já existente, cada uma com o vínculo
exato do seu perfil.

A administração global — ``admin@eithra.local`` — é diferente das demais: ela é
``is_staff`` e ``is_superuser``, e isso é o que a torna administradora do
sistema. Não depende de papel condominial, e por isso não recebe vínculo aqui;
o que ela faz no Eithra é o Django admin. Os outros seis perfis recebem
exatamente o vínculo esperado com o condomínio, e nenhum deles é staff.

Nada aqui é senha de verdade. Cada conta recebe uma senha forte, gerada na hora
e diferente das demais. A senha só é trocada quando a conta está sendo criada ou
quando a execução pede ``--redefinir-senhas``; rodar de novo sem essa opção
encontra as contas pelo e-mail, ajusta os vínculos sem duplicar e não mexe em
senha nenhuma. As senhas não ficam gravadas em migration, fixture, código ou
arquivo: aparecem uma única vez, na saída desta execução, e só para o que ela
criou ou redefiniu.
"""
from __future__ import annotations

import secrets
import string
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

# Os sete perfis do piloto. Cada linha é: rótulo legível, e-mail, nome de
# exibição, papel no condomínio (``None`` = administração global, sem vínculo)
# e se é administrador global do sistema.
USUARIOS_DO_PILOTO = (
    (
        "Administrador EITHRA",
        "admin@eithra.local",
        "Administrador EITHRA",
        None,
        True,
    ),
    (
        "Administradora Piloto",
        "administradora@eithra.local",
        "Administradora Piloto",
        Papel.ADMINISTRADORA,
        False,
    ),
    (
        "Síndica Piloto",
        "sindica@eithra.local",
        "Síndica Piloto",
        Papel.SINDICA,
        False,
    ),
    (
        "Supervisão Piloto",
        "supervisao@eithra.local",
        "Supervisão Piloto",
        Papel.SUPERVISORA,
        False,
    ),
    (
        "Operacional Piloto",
        "operacional@eithra.local",
        "Operacional Piloto",
        Papel.OPERACIONAL,
        False,
    ),
    (
        "Portaria Piloto",
        "portaria@eithra.local",
        "Portaria Piloto",
        Papel.PORTEIRO,
        False,
    ),
    (
        "Morador Piloto",
        "morador@eithra.local",
        "Morador Piloto",
        Papel.MORADOR,
        False,
    ),
)

TAMANHO_DA_SENHA = 20
ALFABETO_DA_SENHA = string.ascii_letters + string.digits + "!@#$%&*"


def gerar_senha(tamanho: int = TAMANHO_DA_SENHA) -> str:
    """Uma senha forte, diferente a cada chamada. Nunca é gravada em arquivo."""
    return "".join(secrets.choice(ALFABETO_DA_SENHA) for _ in range(tamanho))


class Command(BaseCommand):
    """Prepara as contas de teste do piloto, uma para cada perfil."""

    help = "Cria os usuários locais de teste do piloto, um por perfil."

    def add_arguments(self, parser):
        parser.add_argument(
            "--condominio",
            default="",
            help=(
                "Identificador do condomínio piloto. Obrigatório quando houver "
                "mais de um cadastrado; havendo um só, é usado automaticamente."
            ),
        )
        parser.add_argument(
            "--redefinir-senhas",
            action="store_true",
            help=(
                "Gera uma senha nova para todas as contas de teste. Sem esta "
                "opção, as contas existentes mantêm a senha atual."
            ),
        )

    def handle(self, *args, **opcoes):
        condominio = self._resolver_condominio(opcoes["condominio"])
        redefinir = opcoes["redefinir_senhas"]

        credenciais = []
        with transaction.atomic():
            for rotulo, email, nome, papel, admin_global in USUARIOS_DO_PILOTO:
                usuario, senha = self._garantir_usuario(
                    email=email,
                    nome=nome,
                    admin_global=admin_global,
                    redefinir=redefinir,
                )
                if papel is not None:
                    self._garantir_vinculo(usuario, condominio, papel)
                if senha:
                    credenciais.append((rotulo, usuario.email, senha))

        self._relatar(condominio, credenciais)

    @staticmethod
    def _resolver_condominio(valor: str) -> Condominio:
        """Encontra o condomínio piloto, sem escolher por conta própria.

        Com o identificador informado, ele manda. Sem ele, só há uma resposta
        quando existe um único condomínio cadastrado: havendo mais de um, o
        comando para e mostra os candidatos, em vez de adivinhar qual é o
        piloto.
        """
        if valor:
            try:
                chave = uuid.UUID(str(valor))
            except (ValueError, TypeError, AttributeError):
                raise CommandError(
                    "O identificador informado em --condominio não é válido."
                )
            condominio = Condominio.objects.filter(pk=chave).first()
            if condominio is None:
                raise CommandError(
                    "Não há condomínio cadastrado com este identificador."
                )
            return condominio

        candidatos = list(Condominio.objects.all())
        if not candidatos:
            raise CommandError(
                "Não há condomínio cadastrado. Cadastre o condomínio piloto antes "
                "de criar os usuários de teste."
            )
        if len(candidatos) > 1:
            listagem = "\n".join(
                f"  - {candidato.nome} ({candidato.pk})" for candidato in candidatos
            )
            raise CommandError(
                "Há mais de um condomínio cadastrado; informe qual é o piloto com "
                "`--condominio <identificador>`:\n" + listagem
            )
        return candidatos[0]

    @staticmethod
    def _garantir_usuario(*, email, nome, admin_global, redefinir):
        """Encontra ou cria a conta, deixando-a ativa e com o acesso esperado.

        Devolve a conta e a senha gerada — vazia quando a senha atual é
        preservada, que é o caso de uma conta já existente sem ``--redefinir``.
        """
        Usuario = get_user_model()
        usuario = Usuario.objects.filter(email__iexact=email).first()
        criado = usuario is None
        if criado:
            usuario = Usuario(email=email.lower())

        usuario.full_name = nome
        usuario.is_active = True
        # A administração global é do sistema, e não do condomínio: só o perfil
        # de administrador a recebe, e nenhum outro usuário de teste vira staff.
        usuario.is_staff = admin_global
        usuario.is_superuser = admin_global

        senha = ""
        if criado or redefinir:
            senha = gerar_senha()
            usuario.set_password(senha)

        usuario.save()
        return usuario, senha

    @staticmethod
    def _garantir_vinculo(usuario, condominio, papel):
        """O vínculo do perfil com o condomínio, sem nunca duplicá-lo.

        O par pessoa/condomínio é único, então reativar ou corrigir o papel é
        ajustar este vínculo — e nunca criar um segundo.
        """
        vinculo, _ = AcessoUsuarioCondominio.objects.update_or_create(
            usuario=usuario,
            condominio=condominio,
            defaults={"papel": papel, "ativo": True},
        )
        return vinculo

    def _relatar(self, condominio, credenciais) -> None:
        """Mostra o condomínio e as credenciais geradas nesta execução."""
        escrever = self.stdout.write
        escrever("Usuários locais de teste do piloto")
        escrever(f"Condomínio: {condominio.nome} ({condominio.pk})")

        if not credenciais:
            escrever(
                "Nenhuma senha nova foi gerada: as contas já existiam. Use "
                "--redefinir-senhas para gerar senhas novas."
            )
            return

        escrever("")
        escrever("Perfil | E-mail | Senha")
        for rotulo, email, senha in credenciais:
            escrever(f"{rotulo} | {email} | {senha}")
        escrever("")
        escrever(
            "Guarde estas senhas: elas não são recuperáveis e não ficam gravadas "
            "em nenhum lugar do sistema."
        )

"""Modelos do app de condomínios."""
import uuid

from django.conf import settings
from django.db import models

from apps.condominios.constants import Papel


class Condominio(models.Model):
    """Condomínio atendido pelo Eithra.

    É o limite de isolamento do sistema: toda informação de negócio pertencerá
    a um condomínio, e nada de um condomínio deve aparecer em outro.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField("nome", max_length=150)
    ativo = models.BooleanField("ativo", default=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "condomínio"
        verbose_name_plural = "condomínios"
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome


class AcessoUsuarioCondominio(models.Model):
    """Vínculo entre uma pessoa e um condomínio, com o papel exercido lá.

    Uma pessoa pode ter vínculo com vários condomínios; um condomínio pode ter
    várias pessoas. O papel fica aqui, no vínculo, e não na pessoa.

    A exclusão é protegida em ambas as pontas: apagar uma pessoa ou um
    condomínio não deve levar junto, em silêncio, o histórico de quem tinha
    acesso a quê. Primeiro se desativa o vínculo, ou se remove explicitamente.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="acessos_condominio",
        verbose_name="usuário",
    )
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="acessos",
        verbose_name="condomínio",
    )
    papel = models.CharField("papel", max_length=20, choices=Papel.choices)
    ativo = models.BooleanField("ativo", default=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "acesso a condomínio"
        verbose_name_plural = "acessos a condomínios"
        ordering = ["condominio__nome", "usuario__full_name"]
        constraints = [
            # Uma pessoa tem no máximo um vínculo por condomínio. Reativar
            # acesso é mudar este vínculo, e não criar um segundo — do
            # contrário haveria históricos paralelos para o mesmo par.
            models.UniqueConstraint(
                fields=["usuario", "condominio"],
                name="acesso_unico_por_usuario_e_condominio",
            )
        ]

    def __str__(self) -> str:
        return f"{self.usuario} · {self.condominio}"

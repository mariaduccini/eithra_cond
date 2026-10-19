"""Modelos do app de condomínios."""
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.condominios.constants import Papel


def somente_digitos(valor: str | None) -> str:
    """O valor sem máscara: só os dígitos, que é como ele fica guardado.

    CNPJ, CEP e telefone são guardados normalizados. Guardar o que a pessoa
    digitou parece mais simples, mas faz o mesmo dado aparecer de várias formas
    e inviabiliza comparar dois cadastros.
    """
    return re.sub(r"\D", "", valor or "")


class Condominio(models.Model):
    """Condomínio atendido pelo Eithra.

    É o limite de isolamento do sistema: toda informação de negócio pertencerá
    a um condomínio, e nada de um condomínio deve aparecer em outro.

    Os dados administrativos — CNPJ, endereço, contato e quantidade de unidades
    — são opcionais de propósito: um condomínio que já existe continua válido
    sem eles, e a gestão completa o cadastro aos poucos. Só o nome é obrigatório.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField("nome", max_length=150)
    cnpj = models.CharField(
        "CNPJ",
        max_length=20,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos. Aceita ser digitado com máscara.",
    )
    logradouro = models.CharField("logradouro", max_length=180, blank=True, default="")
    numero = models.CharField("número", max_length=20, blank=True, default="")
    complemento = models.CharField("complemento", max_length=120, blank=True, default="")
    bairro = models.CharField("bairro", max_length=120, blank=True, default="")
    cidade = models.CharField("cidade", max_length=120, blank=True, default="")
    uf = models.CharField("UF", max_length=2, blank=True, default="")
    cep = models.CharField(
        "CEP",
        max_length=10,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos. Aceita ser digitado com máscara.",
    )
    telefone_administrativo = models.CharField(
        "telefone administrativo",
        max_length=20,
        blank=True,
        default="",
        help_text="Guardado só com os dígitos. Aceita ser digitado com máscara.",
    )
    email_administrativo = models.EmailField(
        "e-mail administrativo", blank=True, default=""
    )
    quantidade_unidades = models.PositiveIntegerField(
        "quantidade de unidades",
        null=True,
        blank=True,
        help_text="Quantas unidades o condomínio tem, quando se souber.",
    )
    observacao_administrativa = models.TextField(
        "observação administrativa", blank=True, default=""
    )
    ativo = models.BooleanField("ativo", default=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "condomínio"
        verbose_name_plural = "condomínios"
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome

    def save(self, *args, **kwargs):
        """Guarda o cadastro já normalizado.

        A normalização vive aqui, e não só no formulário, para que o dado fique
        coerente também quando vier de outro caminho — carga, admin ou serviço.
        """
        self.nome = " ".join((self.nome or "").split())
        self.cnpj = somente_digitos(self.cnpj)
        self.cep = somente_digitos(self.cep)
        self.uf = (self.uf or "").strip().upper()
        self.telefone_administrativo = somente_digitos(self.telefone_administrativo)
        self.email_administrativo = (self.email_administrativo or "").strip().lower()
        for campo in ("logradouro", "numero", "complemento", "bairro", "cidade"):
            setattr(self, campo, " ".join((getattr(self, campo) or "").split()))
        self.observacao_administrativa = (self.observacao_administrativa or "").strip()
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um cadastro incoerente antes que ele chegue ao banco."""
        erros = {}

        if not (self.nome or "").strip():
            erros["nome"] = "Dê um nome ao condomínio."

        cnpj = somente_digitos(self.cnpj)
        if cnpj and len(cnpj) != 14:
            erros["cnpj"] = "O CNPJ deve ter 14 dígitos."

        cep = somente_digitos(self.cep)
        if cep and len(cep) != 8:
            erros["cep"] = "O CEP deve ter 8 dígitos."

        uf = (self.uf or "").strip().upper()
        if uf and (len(uf) != 2 or not uf.isalpha()):
            erros["uf"] = "A UF deve ter duas letras."

        if self.quantidade_unidades is not None and self.quantidade_unidades < 0:
            erros["quantidade_unidades"] = (
                "A quantidade de unidades não pode ser negativa."
            )

        if erros:
            raise ValidationError(erros)

    @property
    def cnpj_formatado(self) -> str:
        """O CNPJ como a gestão o lê. Sem os 14 dígitos, mostra o que houver."""
        digitos = self.cnpj or ""
        if len(digitos) != 14:
            return digitos
        return (
            f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/"
            f"{digitos[8:12]}-{digitos[12:]}"
        )

    @property
    def cep_formatado(self) -> str:
        """O CEP como a gestão o lê."""
        digitos = self.cep or ""
        if len(digitos) != 8:
            return digitos
        return f"{digitos[:5]}-{digitos[5:]}"

    @property
    def telefone_formatado(self) -> str:
        """O telefone como a gestão o lê, sem adivinhar o que não foi informado."""
        digitos = self.telefone_administrativo or ""
        if len(digitos) == 11:
            return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
        if len(digitos) == 10:
            return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
        return digitos

    @property
    def endereco_resumido(self) -> str:
        """O endereço em uma linha, para a listagem. Vazio quando não há nada."""
        partes = [
            " ".join(parte for parte in (self.logradouro, self.numero) if parte),
            self.complemento,
            self.bairro,
            " ".join(parte for parte in (self.cidade, self.uf) if parte),
        ]
        return " · ".join(parte for parte in partes if parte)


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

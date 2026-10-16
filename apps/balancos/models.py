"""Modelos do domínio de balanços mensais.

O balanço mensal é o agregado canônico do documento financeiro que a
administração envia todo mês. Ele não é conta bancária, lançamento, boleto,
cobrança, fornecedor, nota fiscal, manutenção ou orçamento: é a leitura
estruturada e validável de um documento mensal.

O Eithra não é um ERP contábil. Ele interpreta o que a administração já produz e
transforma isso em compreensão, acompanhamento e transparência.

O arquivo original não é guardado no sistema de arquivos local: ele fica em
storage externo privado, e aqui se guarda apenas a referência ao objeto.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.balancos.constants import (
    LayoutBalanco,
    ProcessamentoBalanco,
    StatusBalanco,
)
from apps.condominios.models import Condominio

# SHA-256 em hexadecimal minúsculo. Fixar a caixa evita que o mesmo arquivo
# apareça como dois hashes diferentes na detecção de duplicidade.
FORMATO_SHA256 = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Informe o hash SHA-256 em hexadecimal minúsculo (64 caracteres).",
)


class BalancoMensal(models.Model):
    """Balanço de um mês de um condomínio, em uma versão.

    O registro representa o balanço já confirmado pela gestão: o resultado da
    leitura é apresentado, a gestão confirma a importação e só então o mês
    passa a valer. Não existe etapa formal de revisão.

    O mesmo mês pode ter mais de uma versão. Quando a administradora envia um
    documento corrigido, cria-se uma nova versão que substitui a anterior — e a
    anterior permanece, marcada como substituída. Nada é sobrescrito em
    silêncio.

    A competência é sempre o primeiro dia do mês de referência: ela representa
    o mês, não um dia financeiro específico.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="balancos",
        verbose_name="condomínio",
    )
    competencia = models.DateField(
        "competência",
        help_text="Primeiro dia do mês de referência.",
    )
    versao = models.PositiveIntegerField("versão", default=1)
    substitui = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="versoes_corrigidas",
        verbose_name="substitui",
        help_text="Versão anterior deste mesmo mês, quando esta a corrige.",
    )
    status = models.CharField(
        "situação",
        max_length=20,
        choices=StatusBalanco.choices,
        default=StatusBalanco.VALIDADO,
    )
    status_processamento = models.CharField(
        "processamento",
        max_length=20,
        choices=ProcessamentoBalanco.choices,
        default=ProcessamentoBalanco.PENDENTE,
    )
    layout_detectado = models.CharField(
        "layout detectado",
        max_length=20,
        choices=LayoutBalanco.choices,
        default=LayoutBalanco.DESCONHECIDO,
    )
    schema_versao = models.PositiveIntegerField("versão do schema", default=1)

    # O que o sistema entendeu do documento, antes de qualquer confirmação
    # humana. Nunca é tratado como oficial.
    dados_extraidos = models.JSONField("dados extraídos", default=dict, blank=True)
    # O que a gestão confirmou ou corrigiu. É o estado oficial do mês.
    dados_validados = models.JSONField("dados validados", default=dict, blank=True)
    inconsistencias = models.JSONField("inconsistências", default=list, blank=True)
    # Apenas agregados úteis para listagem e comparação entre meses.
    resumo = models.JSONField("resumo", default=dict, blank=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="balancos_criados",
        verbose_name="criado por",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)
    validado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="balancos_validados",
        verbose_name="validado por",
    )
    validado_em = models.DateTimeField("validado em", null=True, blank=True)

    class Meta:
        verbose_name = "balanço mensal"
        verbose_name_plural = "balanços mensais"
        ordering = ["-competencia", "-versao"]
        constraints = [
            # A mesma versão não existe duas vezes para o mesmo mês.
            models.UniqueConstraint(
                fields=["condominio", "competencia", "versao"],
                name="balanco_unico_por_competencia_e_versao",
            ),
            # Uma competência tem uma única versão em vigor: validada ou
            # publicada. As versões anteriores continuam guardadas, marcadas
            # como substituídas — por isso a unicidade vale apenas fora desse
            # estado. É assim que se sabe qual versão é a atual, sem precisar
            # de campo redundante.
            models.UniqueConstraint(
                fields=["condominio", "competencia"],
                condition=~Q(status=StatusBalanco.SUBSTITUIDO),
                name="balanco_em_vigor_unico_por_competencia",
            ),
            models.CheckConstraint(
                condition=Q(versao__gte=1),
                name="balanco_versao_minima",
            ),
            # A competência representa o mês inteiro, nunca um dia avulso.
            models.CheckConstraint(
                condition=Q(competencia__day=1),
                name="balanco_competencia_no_primeiro_dia",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "status"],
                name="balanco_condominio_status_idx",
            ),
        ]

    def clean(self):
        super().clean()
        erros: dict[str, str] = {}

        if self.competencia and self.competencia.day != 1:
            erros["competencia"] = (
                "A competência representa o mês: use sempre o primeiro dia."
            )
        if self.versao is not None and self.versao < 1:
            erros["versao"] = "A versão começa em 1."

        if self.substitui_id:
            erros.update(self._erros_de_substituicao())

        if self.status in (StatusBalanco.VALIDADO, StatusBalanco.PUBLICADO):
            if not self.validado_em:
                erros["validado_em"] = (
                    "Um balanço validado registra quando foi validado."
                )
            if not self.validado_por_id:
                erros["validado_por"] = "Um balanço validado registra quem validou."

        if erros:
            raise ValidationError(erros)

    def _erros_de_substituicao(self) -> dict:
        """A substituição só vale dentro do mesmo mês do mesmo condomínio."""
        if self.pk and self.substitui_id == self.pk:
            return {"substitui": "Um balanço não substitui a si mesmo."}

        anterior = self.substitui
        erros: dict[str, str] = {}

        if self.condominio_id and anterior.condominio_id != self.condominio_id:
            erros["substitui"] = "A versão substituída pertence a outro condomínio."
        if self.competencia and anterior.competencia != self.competencia:
            erros["substitui"] = "A versão substituída é de outra competência."
        if (
            self.versao is not None
            and anterior.versao is not None
            and self.versao <= anterior.versao
        ):
            erros["versao"] = "A versão nova precisa ser maior que a substituída."

        return erros

    def __str__(self) -> str:
        if not self.competencia:
            return "Balanço sem competência"
        return f"Balanço {self.competencia:%m/%Y} v{self.versao} · {self.get_status_display()}"


class DocumentoBalanco(models.Model):
    """Metadados do arquivo original de um balanço.

    O arquivo em si vive em storage externo privado. Aqui ficam a referência do
    objeto e o que ajuda a reconhecê-lo: nome, tipo, tamanho e hash.

    Nunca se guarda URL pública, URL assinada, token ou senha. A URL temporária
    é gerada sob demanda, quando alguém autorizado precisa do arquivo — nunca
    fica persistida.

    O versionamento é do balanço, não do documento: uma versão corrigida é um
    novo balanço, com o seu próprio documento.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    balanco = models.ForeignKey(
        BalancoMensal,
        on_delete=models.PROTECT,
        related_name="documentos",
        verbose_name="balanço",
    )
    nome_original = models.CharField("nome original", max_length=255)
    storage_provider = models.CharField(
        "provedor", max_length=50, blank=True, default=""
    )
    storage_bucket = models.CharField(
        "local de armazenamento", max_length=150, blank=True, default=""
    )
    storage_key = models.CharField("referência do objeto", max_length=500)
    mime_type = models.CharField(
        "tipo do arquivo", max_length=120, blank=True, default=""
    )
    tamanho_bytes = models.PositiveBigIntegerField("tamanho", default=0)
    sha256 = models.CharField(
        "hash do arquivo",
        max_length=64,
        validators=[FORMATO_SHA256],
        help_text="Necessário para reconhecer o mesmo arquivo mais tarde.",
    )
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="documentos_balanco_enviados",
        verbose_name="enviado por",
    )
    enviado_em = models.DateTimeField("enviado em", default=timezone.now)
    ativo = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "documento de balanço"
        verbose_name_plural = "documentos de balanço"
        ordering = ["-enviado_em"]
        constraints = [
            # O mesmo arquivo não entra duas vezes no mesmo balanço. Não se
            # impõe unicidade global do hash: o mesmo PDF pode existir
            # legitimamente em condomínios distintos.
            models.UniqueConstraint(
                fields=["balanco", "sha256"],
                name="documento_unico_por_balanco_e_hash",
            ),
            models.CheckConstraint(
                condition=Q(tamanho_bytes__gte=0),
                name="documento_tamanho_nao_negativo",
            ),
        ]
        indexes = [
            # Apenas apoio à procura por arquivo repetido. A decisão de tratar
            # como duplicidade é da gestão, não do banco.
            models.Index(fields=["sha256"], name="documento_sha256_idx"),
        ]

    def clean(self):
        super().clean()
        erros: dict[str, str] = {}

        for campo in ("storage_provider", "storage_bucket", "storage_key"):
            valor = (getattr(self, campo) or "").strip().lower()
            if valor.startswith(("http://", "https://")):
                erros[campo] = (
                    "Guarde apenas a referência do objeto. URL pública ou "
                    "assinada é gerada sob demanda, nunca persistida."
                )

        if self.tamanho_bytes is not None and self.tamanho_bytes < 0:
            erros["tamanho_bytes"] = "O tamanho do arquivo não pode ser negativo."

        if erros:
            raise ValidationError(erros)

    def __str__(self) -> str:
        return self.nome_original or str(self.pk)

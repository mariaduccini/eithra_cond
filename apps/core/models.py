"""Modelos transversais do Eithra.

O que mora aqui não pertence a um domínio operacional só: é apoio comum para
importação, rastreabilidade e regras compartilhadas. Nesta etapa entra apenas o
registro da carga histórica, em três peças — o lote da rodada, o item da fonte e
o vínculo entre a fonte e o domínio —, para que a migração de dados seja
auditável, idempotente e independente do Gmail ou do Google Drive depois que o
dado já entrou no Eithra.

A separação entre item e vínculo é deliberada: a fonte externa tem identidade
própria (uma mensagem, um arquivo), e o que ela explica no Eithra pode ser mais
de uma coisa ao mesmo tempo. Misturar as duas coisas numa linha só obrigaria a
duplicar a identidade da fonte para cada registro de destino.
"""
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.condominios.models import Condominio

from .constants import (
    EstadoProcessamentoImportacao,
    EntidadeImportacaoHistorica,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from .texto import normalizar_endereco_email


def _texto_curto(valor: str | None) -> str:
    """Colapsa espaços repetidos, sem inventar conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva parágrafos, removendo só os espaços das pontas."""
    return (valor or "").strip()


class LoteImportacaoHistorica(models.Model):
    """Um agrupador explícito de itens de uma mesma rodada de carga histórica.

    O lote diz de onde a rodada veio e quem a registrou, sem obrigar o runtime
    do sistema a continuar conectado àquela fonte depois da carga.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="lotes_importacao_historica",
        verbose_name="Condomínio",
    )
    origem = models.CharField(
        "Origem",
        max_length=20,
        choices=OrigemImportacaoHistorica.choices,
    )
    conta_origem = models.CharField(
        "Conta ou origem declarada",
        max_length=255,
        blank=True,
        default="",
        help_text="Ex.: a conta do Gmail, a pasta do Drive ou a fonte declarada do lote.",
    )
    referencia_lote = models.CharField(
        "Referência externa do lote",
        max_length=255,
        blank=True,
        default="",
        help_text="Identificador externo do conjunto, quando existir.",
    )
    nome_original = models.CharField(
        "Nome original",
        max_length=255,
        blank=True,
        default="",
        help_text="Nome do arquivo ou do conjunto de origem, quando houver.",
    )
    hash_fonte = models.CharField(
        "Hash da fonte",
        max_length=64,
        blank=True,
        default="",
        help_text="Identidade da fonte, quando a origem fornecer hash confiável.",
    )
    observacao = models.TextField("Observação", blank=True, default="")
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lotes_importacao_historica_criados",
        verbose_name="Registrado por",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "lote de importação histórica"
        verbose_name_plural = "lotes de importação histórica"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(
                fields=["condominio", "origem"],
                name="loteimphist_cond_orig_idx",
            ),
        ]

    def __str__(self) -> str:
        base = self.get_origem_display()
        if self.nome_original:
            return f"{base} · {self.nome_original}"
        if self.referencia_lote:
            return f"{base} · {self.referencia_lote}"
        return base

    def save(self, *args, **kwargs):
        """Guarda o lote já normalizado."""
        for campo in ("conta_origem", "referencia_lote", "nome_original", "hash_fonte"):
            setattr(self, campo, _texto_curto(getattr(self, campo)))
        self.hash_fonte = self.hash_fonte.lower()
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um hash impossível antes que ele chegue ao banco."""
        erros = {}
        if self.hash_fonte and not re.fullmatch(r"[0-9a-f]{64}", self.hash_fonte.lower()):
            erros["hash_fonte"] = "O hash da fonte deve ter 64 caracteres hexadecimais."
        if erros:
            raise ValidationError(erros)


class ItemImportacaoHistoricaQuerySet(models.QuerySet):
    """Leituras e registros dos itens de fonte da carga histórica."""

    TRANSICOES_PERMITIDAS = {
        EstadoProcessamentoImportacao.PENDENTE: frozenset(
            {EstadoProcessamentoImportacao.PROCESSANDO}
        ),
        EstadoProcessamentoImportacao.PROCESSANDO: frozenset(
            {
                EstadoProcessamentoImportacao.PROCESSADO,
                EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
                EstadoProcessamentoImportacao.ERRO,
            }
        ),
        EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO: frozenset(
            {
                EstadoProcessamentoImportacao.PROCESSADO,
                EstadoProcessamentoImportacao.PROCESSANDO,
            }
        ),
        EstadoProcessamentoImportacao.PROCESSADO: frozenset(
            {EstadoProcessamentoImportacao.PROCESSANDO}
        ),
        EstadoProcessamentoImportacao.ERRO: frozenset(
            {EstadoProcessamentoImportacao.PROCESSANDO}
        ),
    }

    def do_condominio(self, condominio):
        """Itens deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    @transaction.atomic
    def registrar_item(
        self,
        *,
        lote: "LoteImportacaoHistorica",
        identificador_externo: str,
        conta_origem: str | None = None,
        message_id: str = "",
        thread_id: str = "",
        file_id: str = "",
        nome_original: str = "",
        hash_fonte: str = "",
        remetente: str = "",
        assunto: str = "",
        momento_da_mensagem=None,
        criado_por=None,
    ):
        """Cria ou reaproveita o item de fonte daquele registro externo.

        A identidade é `condominio + origem + conta declarada + identificador
        externo`. Reprocessar a mesma fonte atualiza a linha que já existe, em
        vez de criar uma segunda.

        O identificador externo é de quem chama, porque só quem lê a fonte sabe
        qual chave é estável ali: o `message_id` da mensagem inteira, o
        `message_id` junto do anexo, o `file_id` do arquivo, ou a chave da linha
        dentro dele. Este modelo não deduz essa chave — deduzir errado
        produziria duplicata em silêncio.

        A autoria é do nascimento do registro, não da última leitura: registrar
        de novo a mesma fonte não reescreve quem a registrou da primeira vez.
        Reingestão atualiza o que a fonte diz — metadados — e não quem a trouxe.
        """
        identificador_externo = _texto_curto(identificador_externo)
        if not identificador_externo:
            raise ValidationError(
                {"identificador_externo": "Diga o identificador externo do item."}
            )

        chave = {
            "condominio": lote.condominio,
            "origem": lote.origem,
            "conta_origem": _texto_curto(
                lote.conta_origem if conta_origem is None else conta_origem
            ),
            "identificador_externo": identificador_externo,
        }
        item = self.select_for_update().filter(**chave).first()
        criado = item is None
        if item is None:
            item = self.model(**chave)
            # Só aqui a autoria é gravada: reingestão não a sobrescreve nem a
            # apaga, mesmo quando a chamada vem sem autoria (como a do Gmail).
            item.criado_por = criado_por
        item.lote = lote
        item.message_id = message_id
        item.thread_id = thread_id
        item.file_id = file_id
        item.nome_original = nome_original
        item.hash_fonte = hash_fonte
        item.remetente = remetente
        item.assunto = assunto
        item.momento_da_mensagem = momento_da_mensagem
        item.full_clean()
        item.save()
        return item, criado

    @transaction.atomic
    def alterar_estado_processamento(
        self,
        *,
        item: "ItemImportacaoHistorica",
        novo_estado: str,
        reprocessamento_explicito: bool = False,
    ) -> "ItemImportacaoHistorica":
        """Move o item entre os estados válidos do ciclo de processamento.

        O manager centraliza as transições para que ingestão, retry e
        reprocessamento não espalhem atribuições soltas pelo código.
        """
        if item is None or not getattr(item, "pk", None):
            raise ValidationError(
                {"item": "Diga qual item da fonte deve mudar de estado."}
            )
        if novo_estado not in EstadoProcessamentoImportacao.values:
            raise ValidationError(
                {"estado_processamento": "Estado de processamento inválido."}
            )

        item = self.select_for_update().get(pk=item.pk)
        estado_atual = item.estado_processamento
        if novo_estado == estado_atual:
            return item

        permitidos = self.TRANSICOES_PERMITIDAS.get(estado_atual, frozenset())
        if novo_estado not in permitidos:
            raise ValidationError(
                {
                    "estado_processamento": (
                        "Transição de processamento inválida para este item."
                    )
                }
            )

        if (
            novo_estado == EstadoProcessamentoImportacao.PROCESSANDO
            and estado_atual
            in {
                EstadoProcessamentoImportacao.PROCESSADO,
                EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
            }
            and not reprocessamento_explicito
        ):
            raise ValidationError(
                {
                    "estado_processamento": (
                        "Reprocessamento exige ação explícita."
                    )
                }
            )

        item.estado_processamento = novo_estado
        if novo_estado == EstadoProcessamentoImportacao.PROCESSADO:
            item.processado_em = timezone.now()
        item.full_clean()
        item.save(update_fields=["estado_processamento", "processado_em", "atualizado_em"])
        return item

    def reprocessar_item(self, *, item: "ItemImportacaoHistorica") -> "ItemImportacaoHistorica":
        """Reabre explicitamente um item já analisado para nova leitura."""
        return self.alterar_estado_processamento(
            item=item,
            novo_estado=EstadoProcessamentoImportacao.PROCESSANDO,
            reprocessamento_explicito=True,
        )


class VinculoImportacaoHistoricaQuerySet(models.QuerySet):
    """Leituras e registros dos vínculos entre a fonte e o domínio."""

    def do_condominio(self, condominio):
        """Vínculos deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def pendentes_de_revisao(self):
        """Vínculos que ainda exigem olhar humano complementar."""
        return self.filter(revisao_manual=True)

    @transaction.atomic
    def registrar_vinculo(
        self,
        *,
        item: "ItemImportacaoHistorica",
        entidade_destino: str,
        identificador_destino: str = "",
        destino_id=None,
        resultado: str = ResultadoImportacaoHistorica.PENDENTE,
        revisao_manual: bool = False,
        observacao: str = "",
        criado_por=None,
    ):
        """Diz a que registro do Eithra este item de fonte corresponde.

        A identidade do vínculo é `item + tipo de entidade + identificação do
        destino`. O mesmo item pode ter vários vínculos ao mesmo tempo — uma
        mensagem que virou demanda e documento tem duas linhas, não uma —, e o
        mesmo arquivo pode apoiar um contrato e uma garantia.

        Reprocessar atualiza o vínculo existente com o novo desfecho. Dois
        vínculos do mesmo item, para a mesma entidade e a mesma identificação,
        são a mesma relação anotada duas vezes: o banco recusa a segunda.
        """
        chave = {
            "item": item,
            "entidade_destino": entidade_destino,
            "identificador_destino": _texto_curto(identificador_destino),
        }
        vinculo = self.select_for_update().filter(**chave).first()
        criado = vinculo is None
        if vinculo is None:
            vinculo = self.model(condominio=item.condominio, **chave)
        vinculo.destino_id = destino_id
        vinculo.resultado = resultado
        vinculo.revisao_manual = revisao_manual
        vinculo.observacao = observacao
        vinculo.criado_por = criado_por
        vinculo.processado_em = timezone.now()
        vinculo.full_clean()
        vinculo.save()
        return vinculo, criado


class ItemImportacaoHistorica(models.Model):
    """O item da fonte: a identidade de um registro externo da carga histórica.

    Ele diz o que veio, não o que virou. A mesma mensagem do Gmail ou o mesmo
    arquivo do Drive pode explicar vários registros do Eithra ao mesmo tempo e
    continua sendo um item só; para onde ele aponta mora em
    `VinculoImportacaoHistorica`.

    A identidade é estável e não depende do lote: recarregar a mesma fonte
    atualiza esta linha em vez de criar outra. O lote guardado é apenas o que
    viu o item por último.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lote = models.ForeignKey(
        LoteImportacaoHistorica,
        on_delete=models.PROTECT,
        related_name="itens",
        verbose_name="Lote",
        help_text="O lote que viu este item por último, não o seu dono.",
    )
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="itens_importacao_historica",
        verbose_name="Condomínio",
    )
    origem = models.CharField(
        "Origem",
        max_length=20,
        choices=OrigemImportacaoHistorica.choices,
    )
    conta_origem = models.CharField(
        "Conta ou origem declarada",
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Faz parte da identidade da fonte: declare-a igual ao reprocessar."
        ),
    )
    identificador_externo = models.CharField(
        "Identificador externo",
        max_length=255,
        help_text="A chave estável do registro na fonte: message_id, file_id, linha, etc.",
    )
    message_id = models.CharField(
        "Gmail message_id", max_length=255, blank=True, default=""
    )
    thread_id = models.CharField(
        "Gmail thread_id", max_length=255, blank=True, default=""
    )
    file_id = models.CharField(
        "Google Drive file_id", max_length=255, blank=True, default=""
    )
    nome_original = models.CharField(
        "Nome original", max_length=255, blank=True, default=""
    )
    hash_fonte = models.CharField(
        "Hash da fonte",
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Ajuda a reconhecer o mesmo binário, mas não é identidade: o mesmo "
            "arquivo pode ter chegado por duas fontes."
        ),
    )
    remetente = models.CharField("Remetente", max_length=320, blank=True, default="")
    assunto = models.CharField("Assunto", max_length=255, blank=True, default="")
    momento_da_mensagem = models.DateTimeField(
        "Momento da mensagem",
        null=True,
        blank=True,
        db_index=True,
    )
    estado_processamento = models.CharField(
        "Estado do processamento",
        max_length=30,
        choices=EstadoProcessamentoImportacao.choices,
        default=EstadoProcessamentoImportacao.PENDENTE,
        db_index=True,
    )
    processado_em = models.DateTimeField(
        "Processado em",
        null=True,
        blank=True,
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="itens_importacao_historica_criados",
        verbose_name="Registrado por",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = ItemImportacaoHistoricaQuerySet.as_manager()

    class Meta:
        verbose_name = "item de importação histórica"
        verbose_name_plural = "itens de importação histórica"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "condominio",
                    "origem",
                    "conta_origem",
                    "identificador_externo",
                ],
                name="itemimphist_unico_da_fonte",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "origem"],
                name="itemimphist_cond_orig_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_origem_display()} · {self.identificador_externo}"

    def save(self, *args, **kwargs):
        """Guarda o item já normalizado, venha ele do importador ou não."""
        for campo in (
            "conta_origem",
            "identificador_externo",
            "message_id",
            "thread_id",
            "file_id",
            "remetente",
            "assunto",
            "nome_original",
            "hash_fonte",
        ):
            setattr(self, campo, _texto_curto(getattr(self, campo)))
        self.hash_fonte = self.hash_fonte.lower()
        self.remetente = normalizar_endereco_email(self.remetente)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa inconsistências básicas entre lote, origem e identificadores."""
        erros = {}

        if self.lote_id:
            if self.condominio_id and self.lote.condominio_id != self.condominio_id:
                erros["lote"] = "O lote escolhido pertence a outro condomínio."
            if self.origem and self.lote.origem != self.origem:
                erros["origem"] = "A origem do item deve ser a mesma origem do lote."

        if self.origem == OrigemImportacaoHistorica.GMAIL and not self.message_id:
            erros["message_id"] = "Itens do Gmail precisam guardar o message_id."

        if self.origem == OrigemImportacaoHistorica.DRIVE and not self.file_id:
            erros["file_id"] = "Itens do Google Drive precisam guardar o file_id."

        if self.hash_fonte and not re.fullmatch(r"[0-9a-f]{64}", self.hash_fonte.lower()):
            erros["hash_fonte"] = "O hash da fonte deve ter 64 caracteres hexadecimais."

        if erros:
            raise ValidationError(erros)

    def reprocessar(self) -> "ItemImportacaoHistorica":
        """Atalho explícito para reabrir este item no ciclo de processamento."""
        return type(self).objects.reprocessar_item(item=self)


class VinculoImportacaoHistorica(models.Model):
    """O vínculo entre um item de fonte e um registro do domínio do Eithra.

    Cada linha diz "este registro externo corresponde a esta entidade", com o
    desfecho daquela tentativa. Um item de fonte pode ter vários vínculos ao
    mesmo tempo — uma mensagem que virou demanda e documento tem dois —, e
    nenhum deles duplica a identidade da fonte.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(
        ItemImportacaoHistorica,
        on_delete=models.PROTECT,
        related_name="vinculos",
        verbose_name="Item da fonte",
    )
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="vinculos_importacao_historica",
        verbose_name="Condomínio",
    )
    entidade_destino = models.CharField(
        "Entidade de destino",
        max_length=40,
        choices=EntidadeImportacaoHistorica.choices,
    )
    identificador_destino = models.CharField(
        "Identificação do destino",
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Chave natural do destino, quando a mesma entidade aparecer mais de "
            "uma vez no mesmo item."
        ),
    )
    destino_id = models.UUIDField(
        "Identificador do destino",
        null=True,
        blank=True,
        help_text="UUID do registro criado ou atualizado no Eithra, sem GenericForeignKey.",
    )
    resultado = models.CharField(
        "Resultado",
        max_length=20,
        choices=ResultadoImportacaoHistorica.choices,
        default=ResultadoImportacaoHistorica.PENDENTE,
    )
    revisao_manual = models.BooleanField(
        "Revisão manual necessária",
        default=False,
        help_text="Marcador complementar. Não é um status operacional próprio.",
    )
    observacao = models.TextField("Observação", blank=True, default="")
    processado_em = models.DateTimeField("Processado em", default=timezone.now)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vinculos_importacao_historica_criados",
        verbose_name="Registrado por",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = VinculoImportacaoHistoricaQuerySet.as_manager()

    class Meta:
        verbose_name = "vínculo de importação histórica"
        verbose_name_plural = "vínculos de importação histórica"
        ordering = ["-processado_em", "-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "entidade_destino", "identificador_destino"],
                name="vincimphist_unico_por_destino",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "entidade_destino"],
                name="vincimphist_cond_ent_idx",
            ),
            models.Index(
                fields=["condominio", "revisao_manual"],
                name="vincimphist_cond_rev_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_entidade_destino_display()} · {self.item.identificador_externo}"

    def save(self, *args, **kwargs):
        """Guarda o vínculo já normalizado."""
        self.identificador_destino = _texto_curto(self.identificador_destino)
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa vínculo entre condomínios diferentes."""
        erros = {}
        if self.item_id and self.condominio_id and self.item.condominio_id != self.condominio_id:
            erros["condominio"] = "O item da fonte pertence a outro condomínio."
        if erros:
            raise ValidationError(erros)

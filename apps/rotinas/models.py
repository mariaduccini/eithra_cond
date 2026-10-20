"""Modelos do domínio de rotinas recorrentes.

Uma rotina é a regra de algo que precisa voltar a acontecer: a limpeza semanal
da caixa d'água, o teste mensal da iluminação de emergência, a inspeção
trimestral da casa de máquinas, a revisão anual do gerador. Ela não é a
atividade em si — é o combinado de que aquela atividade não depende de alguém
lembrar.

A ocorrência concreta dessa regra é a ``ExecucaoRotina``: a data em que ela caiu,
quem respondeu por ela naquele momento e o que se encontrou ao fazer. É a
execução, e não a rotina, que guarda o que aconteceu. Por isso não existe um
histórico separado: a lista de execuções já é o histórico.

Duas escolhas valem a pena registrar aqui. ``proxima_execucao`` é persistida
para tornar a operação leve, mas não é um campo de digitação: quem escreve nela
é o domínio, sempre a partir da regra. E o que se repete — a periodicidade, o
dia da semana, o dia do mês — é descrito em palavras simples, não em expressão
de calendário: a conta vive em ``recorrencia.py``, e é a mesma para a tela, o
serviço e o teste.

``ModeloChecklist`` é o conjunto de tarefas que uma rotina pode carregar:
"limpar piso", "esvaziar lixeiras", "conferir anormalidades". Quando a
ocorrência nasce, o checklist do modelo é copiado para ela em
``ItemExecucaoChecklist`` — um retrato. A cópia existe por um motivo só: mudar o
modelo depois não pode reescrever o que já foi feito. O que a ocorrência guarda
é o que se esperava dela naquele dia, mais o que de fato aconteceu com cada
tarefa.
"""
import uuid

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.models import Condominio
from apps.core.constants import Prioridade
from apps.demandas.models import Demanda
from apps.manutencoes.models import Manutencao

from .constants import (
    SITUACOES_ENCERRADAS,
    SITUACOES_PENDENTES,
    STATUS_QUE_TRATAM_O_ITEM,
    DiaSemana,
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
    StatusItemChecklist,
    TipoRotina,
)


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


class ModeloChecklistQuerySet(models.QuerySet):
    """Leituras reutilizáveis do checklist modelo."""

    def do_condominio(self, condominio):
        """Checklists deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def ativos(self):
        """Checklists que ainda podem ser oferecidos a uma rotina."""
        return self.filter(ativo=True)


class ModeloChecklist(models.Model):
    """Um conjunto de tarefas reutilizável por rotinas do condomínio.

    O modelo descreve o que se espera fazer — a lista que a limpeza diária
    costuma seguir —, e não o que aconteceu em uma ocorrência. Copiar o modelo
    para a execução é o que preserva o histórico: mudar o modelo depois não
    reescreve o que já foi feito.

    Uma rotina sem checklist continua válida: nem toda atividade precisa de uma
    lista.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="checklists_modelo",
        verbose_name="Condomínio",
    )
    nome = models.CharField(
        "Nome",
        max_length=200,
        help_text="Descrição curta do conjunto de tarefas.",
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        help_text="Quando vale detalhar o que o checklist cobre.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Desativar para parar de oferecê-lo a novas rotinas, sem apagar o histórico.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="checklists_criados",
        verbose_name="Criado por",
        help_text="Quem registrou o checklist. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    objects = ModeloChecklistQuerySet.as_manager()

    class Meta:
        verbose_name = "Modelo de checklist"
        verbose_name_plural = "Modelos de checklist"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        """Guarda o checklist já normalizado, venha ele do formulário ou não."""
        self.nome = _texto(self.nome)
        self.descricao = _texto_longo(self.descricao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa um checklist sem nome antes que ele chegue ao banco."""
        if not _texto(self.nome):
            raise ValidationError({"nome": "Escreva o nome do checklist."})


class ItemModeloChecklist(models.Model):
    """Uma tarefa do checklist modelo, na ordem em que deve ser lida.

    A ordem é um número simples, não uma posição arrastável: quem monta o
    checklist diz em que sequência as tarefas costumam acontecer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checklist = models.ForeignKey(
        ModeloChecklist,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Checklist",
    )
    descricao = models.CharField(
        "Tarefa",
        max_length=200,
        help_text="O que precisa ser feito.",
    )
    ordem = models.PositiveIntegerField(
        "Ordem",
        default=0,
        help_text="A posição da tarefa na lista.",
    )
    obrigatorio = models.BooleanField(
        "Obrigatório",
        default=True,
        help_text="Uma tarefa obrigatória impede a conclusão enquanto estiver pendente.",
    )
    ativo = models.BooleanField(
        "Ativo",
        default=True,
        help_text="Só as tarefas ativas são copiadas para cada nova ocorrência.",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Item do checklist"
        verbose_name_plural = "Itens do checklist"
        ordering = ["checklist", "ordem", "criado_em"]
        indexes = [
            models.Index(
                fields=["checklist", "ativo"], name="item_checklist_ativo_idx"
            ),
        ]

    def __str__(self):
        return self.descricao

    def save(self, *args, **kwargs):
        """Guarda a tarefa já normalizada."""
        self.descricao = _texto(self.descricao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma tarefa sem descrição antes que ela chegue ao banco."""
        if not _texto(self.descricao):
            raise ValidationError({"descricao": "Escreva o que precisa ser feito."})


class RotinaRecorrenteQuerySet(models.QuerySet):
    """Leituras reutilizáveis da rotina.

    São as perguntas que a lista faz: o que está ativo, o que já venceu, o que
    vem por aí, o que é de um responsável, de um local, de um equipamento. A
    regra de atraso da rotina olha apenas ``proxima_execucao`` diante de hoje —
    e nunca o estado de uma execução, que é outra coisa.
    """

    def do_condominio(self, condominio):
        """Rotinas deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def ativas(self):
        """Rotinas que ainda geram ocorrência."""
        return self.filter(ativo=True)

    def vencidas(self, hoje=None):
        """Ativas cuja próxima execução já passou."""
        return self.ativas().filter(proxima_execucao__lt=hoje or timezone.localdate())

    def proximas(self, hoje=None, dias=7):
        """Ativas com a próxima execução nos próximos dias, a partir de hoje."""
        referencia = hoje or timezone.localdate()
        return self.ativas().filter(
            proxima_execucao__gte=referencia,
            proxima_execucao__lte=referencia + timedelta(days=dias),
        )

    def por_responsavel(self, usuario):
        """Rotinas sob responsabilidade desta pessoa."""
        if usuario is None or not getattr(usuario, "pk", None):
            return self.none()
        return self.filter(responsavel_id=usuario.pk)

    def sem_responsavel(self):
        """Ativas sem ninguém responsável — não é urgência, é falta de dono."""
        return self.ativas().filter(responsavel__isnull=True)

    def preventivas_proximas(self, hoje=None, dias=7):
        """Ativas que geram manutenção preventiva e vencem nos próximos dias."""
        return self.proximas(hoje=hoje, dias=dias).filter(gera_manutencao=True)

    def por_local(self, local):
        """Rotinas de um local."""
        if local is None or not getattr(local, "pk", None):
            return self.none()
        return self.filter(local_id=local.pk)

    def por_equipamento(self, equipamento):
        """Rotinas de um equipamento."""
        if equipamento is None or not getattr(equipamento, "pk", None):
            return self.none()
        return self.filter(equipamento_id=equipamento.pk)

    def buscar(self, termo):
        """Procura no título e na descrição, sem exigir acento exato."""
        texto = _texto(termo)
        if not texto:
            return self
        return self.filter(
            models.Q(titulo__icontains=texto) | models.Q(descricao__icontains=texto)
        )

    def na_ordem_de_atencao(self):
        """A ordem em que a lista deve ser lida: o que vence primeiro à frente.

        Quem não tem próxima execução fica no fim — não é urgência, é ausência
        de agenda, e não deve competir com o que já tem data.
        """
        return self.order_by(
            models.F("proxima_execucao").asc(nulls_last=True), "-criado_em"
        )


class RotinaRecorrente(models.Model):
    """A regra de uma atividade que se repete, com o ritmo e o contexto dela.

    Periodicidade e intervalo dizem de quanto em quanto tempo a atividade volta.
    Local, equipamento e fornecedor são cadastros do próprio condomínio — a
    rotina aponta para eles, não os recria. O que é próprio da rotina é apenas o
    responsável padrão: quem responde por ela hoje, e que pode mudar depois.

    Nada aqui dispara trabalho sozinho. A rotina lembra; quem decide fazer, fazer
    uma manutenção preventiva ou abrir uma demanda é a pessoa, na execução.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="rotinas",
        verbose_name="Condomínio",
    )
    titulo = models.CharField(
        "Título",
        max_length=200,
        help_text="Descrição curta e direta do que se repete.",
    )
    descricao = models.TextField(
        "Descrição",
        blank=True,
        help_text="O que a atividade envolve, quando vale detalhar.",
    )
    tipo = models.CharField(
        "Tipo",
        max_length=20,
        choices=TipoRotina.choices,
        default=TipoRotina.OPERACIONAL,
    )
    periodicidade_tipo = models.CharField(
        "Periodicidade",
        max_length=20,
        choices=Periodicidade.choices,
        default=Periodicidade.MENSAL,
    )
    intervalo = models.PositiveIntegerField(
        "Intervalo",
        default=1,
        help_text="Quantos períodos entre uma ocorrência e a seguinte. Sempre maior que zero.",
    )
    dia_semana = models.PositiveSmallIntegerField(
        "Dia da semana",
        null=True,
        blank=True,
        choices=DiaSemana.choices,
        help_text="Obrigatório quando a periodicidade é semanal.",
    )
    dia_mes = models.PositiveSmallIntegerField(
        "Dia do mês",
        null=True,
        blank=True,
        help_text=(
            "Dia do mês em que a ocorrência cai. Quando o mês não tiver esse dia, "
            "a atividade acontece no último dia do mês — o dia 31 em fevereiro "
            "vira o dia 28 ou 29."
        ),
    )
    mes_ano = models.PositiveSmallIntegerField(
        "Mês do ano",
        null=True,
        blank=True,
        help_text="Mês da ocorrência, quando a periodicidade é anual.",
    )
    data_inicio = models.DateField(
        "Início",
        help_text="A partir de quando a rotina existe. Nenhuma ocorrência nasce antes disso.",
    )
    data_fim = models.DateField(
        "Fim",
        null=True,
        blank=True,
        help_text="Até quando a rotina gera ocorrência. Sem fim, ela segue.",
    )
    proxima_execucao = models.DateField(
        "Próxima execução",
        null=True,
        blank=True,
        help_text="Calculada pela regra. Atualizada apenas pelo sistema.",
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rotinas_responsavel",
        verbose_name="Responsável",
        help_text="Quem responde por ela hoje. Vira o responsável de cada nova ocorrência.",
    )
    local = models.ForeignKey(
        LocalOperacional,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rotinas",
        verbose_name="Local",
        help_text="Onde a atividade acontece. Pode ficar sem local definido.",
    )
    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rotinas",
        verbose_name="Equipamento",
        help_text="O que a atividade acompanha, quando é um equipamento.",
    )
    fornecedor = models.ForeignKey(
        Fornecedor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rotinas",
        verbose_name="Fornecedor",
        help_text="Quem costuma executar, quando é serviço de terceiro.",
    )
    checklist_modelo = models.ForeignKey(
        ModeloChecklist,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rotinas",
        verbose_name="Checklist",
        help_text=(
            "O conjunto de tarefas que acompanha cada ocorrência. Opcional: "
            "nem toda atividade precisa de uma lista. Muda o modelo depois, "
            "não o que já foi feito."
        ),
    )
    prioridade = models.CharField(
        "Prioridade",
        max_length=20,
        choices=Prioridade.choices,
        default=Prioridade.NORMAL,
    )
    gera_manutencao = models.BooleanField(
        "Gera manutenção",
        default=False,
        help_text=(
            "Marque quando a rotina é, essencialmente, uma manutenção preventiva. "
            "Nada é criado sozinho: a sugestão aparece na execução, para confirmar."
        ),
    )
    ativo = models.BooleanField(
        "Ativa",
        default=True,
        help_text="Desativar para de gerar novas ocorrências sem apagar as que existem.",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="rotinas_criadas",
        verbose_name="Criada por",
        help_text="Quem registrou a rotina. Preservado para rastreabilidade.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)

    objects = RotinaRecorrenteQuerySet.as_manager()

    class Meta:
        verbose_name = "Rotina"
        verbose_name_plural = "Rotinas"
        ordering = ["-criado_em"]
        constraints = [
            # Um intervalo de zero ou negativo não descreve repetição nenhuma.
            models.CheckConstraint(
                condition=models.Q(intervalo__gt=0),
                name="rotina_intervalo_positivo",
            ),
            # O dia do mês, quando informado, existe no calendário.
            models.CheckConstraint(
                condition=models.Q(dia_mes__isnull=True)
                | models.Q(dia_mes__gte=1, dia_mes__lte=31),
                name="rotina_dia_mes_valido",
            ),
            # E o mês do ano também.
            models.CheckConstraint(
                condition=models.Q(mes_ano__isnull=True)
                | models.Q(mes_ano__gte=1, mes_ano__lte=12),
                name="rotina_mes_ano_valido",
            ),
        ]
        indexes = [
            models.Index(fields=["condominio", "ativo"], name="rotina_cond_ativo_idx"),
            models.Index(
                fields=["condominio", "proxima_execucao"],
                name="rotina_cond_proxima_idx",
            ),
            models.Index(
                fields=["responsavel", "ativo"], name="rotina_resp_ativo_idx"
            ),
            models.Index(
                fields=["equipamento", "ativo"], name="rotina_equip_ativo_idx"
            ),
        ]

    def __str__(self):
        return self.titulo

    def save(self, *args, **kwargs):
        """Guarda a rotina já normalizada, venha ela do formulário ou não."""
        self.titulo = _texto(self.titulo)
        self.descricao = _texto_longo(self.descricao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma rotina incoerente antes que ela chegue ao banco.

        Nada aqui pertence a outro condomínio, e o equipamento e o local
        precisam concordar: um equipamento que fica em um lugar não pode ser
        acompanhado em outro sem que alguém diga isso. A janela de vigência
        também é conferida — o fim não pode vir antes do início.
        """
        erros = {}
        if not _texto(self.titulo):
            erros["titulo"] = "Escreva o que se repete."

        if self.intervalo is not None and self.intervalo < 1:
            erros["intervalo"] = "O intervalo precisa ser de pelo menos um período."

        if self.dia_mes is not None and not 1 <= self.dia_mes <= 31:
            erros["dia_mes"] = "O dia do mês vai de 1 a 31."

        if self.mes_ano is not None and not 1 <= self.mes_ano <= 12:
            erros["mes_ano"] = "O mês vai de 1 a 12."

        if self.dia_semana is not None and not 0 <= int(self.dia_semana) <= 6:
            erros["dia_semana"] = "Escolha um dia da semana."

        if (
            self.data_inicio
            and self.data_fim
            and self.data_fim < self.data_inicio
        ):
            erros["data_fim"] = "O fim não pode ser anterior ao início da rotina."

        if (
            self.local_id
            and self.condominio_id
            and self.local.condominio_id != self.condominio_id
        ):
            erros["local"] = "O local escolhido não é deste condomínio."

        if (
            self.equipamento_id
            and self.condominio_id
            and self.equipamento.condominio_id != self.condominio_id
        ):
            erros["equipamento"] = "O equipamento escolhido não é deste condomínio."

        if (
            self.fornecedor_id
            and self.condominio_id
            and self.fornecedor.condominio_id != self.condominio_id
        ):
            erros["fornecedor"] = "O fornecedor escolhido não é deste condomínio."

        if (
            self.checklist_modelo_id
            and self.condominio_id
            and self.checklist_modelo.condominio_id != self.condominio_id
        ):
            erros["checklist_modelo"] = "O checklist escolhido não é deste condomínio."

        # O equipamento e o local precisam concordar, como já vale em Manutenções.
        if self.equipamento_id and self.local_id:
            local_do_equipamento = self.equipamento.local
            if (
                local_do_equipamento is not None
                and local_do_equipamento.pk != self.local_id
            ):
                erros["local"] = (
                    f"O equipamento escolhido fica em {local_do_equipamento.nome}. "
                    "Ajuste o local para combinar com o equipamento."
                )

        if erros:
            raise ValidationError(erros)


class ExecucaoRotinaQuerySet(models.QuerySet):
    """Leituras reutilizáveis da execução.

    Atrasada é o que já passou da data prevista e continua pendente — a conta é
    feita na hora, e não guardada. É este conjunto de perguntas que o painel de
    hoje vai usar mais adiante: atrasadas, de hoje e próximas, nesta ordem.
    """

    def do_condominio(self, condominio):
        """Execuções deste condomínio. Sem condomínio resolvido, conjunto vazio."""
        if condominio is None or not getattr(condominio, "pk", None):
            return self.none()
        return self.filter(condominio_id=condominio.pk)

    def pendentes(self):
        """Execuções que ainda dependem de alguém."""
        return self.filter(status__in=SITUACOES_PENDENTES)

    def atrasadas(self, hoje=None):
        """Pendentes cuja data prevista já passou."""
        return self.pendentes().filter(data_prevista__lt=hoje or timezone.localdate())

    def de_hoje(self, hoje=None):
        """Pendentes previstas para hoje."""
        return self.pendentes().filter(data_prevista=(hoje or timezone.localdate()))

    def proximas(self, hoje=None, dias=7):
        """Pendentes previstas para os próximos dias, sem contar hoje."""
        referencia = hoje or timezone.localdate()
        return self.pendentes().filter(
            data_prevista__gt=referencia,
            data_prevista__lte=referencia + timedelta(days=dias),
        )

    def minhas(self, usuario):
        """Pendentes sob responsabilidade desta pessoa."""
        if usuario is None or not getattr(usuario, "pk", None):
            return self.none()
        return self.pendentes().filter(responsavel_id=usuario.pk)

    def concluidas(self):
        """Execuções já concluídas."""
        return self.filter(status=StatusExecucao.CONCLUIDA)

    def por_rotina(self, rotina):
        """Todas as ocorrências de uma rotina, na ordem em que caíram."""
        if rotina is None or not getattr(rotina, "pk", None):
            return self.none()
        return self.filter(rotina_id=rotina.pk)

    def com_checklist_pendente(self):
        """Pendentes que têm checklist e ainda têm tarefa por tratar.

        Ocorrência sem checklist não entra: não há lista a completar. Basta um
        item — obrigatório ou opcional — ainda pendente para a ocorrência
        aparecer aqui. É o recorte que a gestão usa para cobrar o que ficou
        pela metade, e o mesmo que o Painel Hoje vai consumir adiante.
        """
        return (
            self.pendentes()
            .filter(itens__status=StatusItemChecklist.PENDENTE)
            .distinct()
        )

    def na_ordem_de_atencao(self):
        """Atrasadas, depois as de hoje, depois as próximas.

        A data prevista crescente já produz exatamente essa leitura; o nome
        existe para dizer isso em voz alta e não deixar a intenção só na view.
        """
        return self.order_by("data_prevista", "-criado_em")


class ExecucaoRotina(models.Model):
    """Uma ocorrência concreta de uma rotina, com o que aconteceu nela.

    A execução referencia a rotina para o contexto estrutural — local,
    equipamento, fornecedor —, sem duplicar o que a rotina já diz. O responsável
    é próprio: a ocorrência preserva quem respondia por ela naquele momento, e
    mudar o responsável da rotina depois não reescreve o passado.

    Os desfechos são poucos e nenhum apaga o que houve. Concluir registra quando,
    o que se encontrou e a próxima ocorrência da rotina. Ignorar diz que a
    atividade não se aplicava naquele período. Cancelar diz que ela não vai
    acontecer, com o motivo. Nos três casos, a rotina segue em frente.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rotina = models.ForeignKey(
        RotinaRecorrente,
        on_delete=models.CASCADE,
        related_name="execucoes",
        verbose_name="Rotina",
    )
    condominio = models.ForeignKey(
        Condominio,
        on_delete=models.PROTECT,
        related_name="execucoes_de_rotina",
        verbose_name="Condomínio",
    )
    data_prevista = models.DateField(
        "Data prevista",
        help_text="O dia em que esta ocorrência cai, conforme a regra da rotina.",
    )
    status = models.CharField(
        "Situação",
        max_length=20,
        choices=StatusExecucao.choices,
        default=StatusExecucao.PENDENTE,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="execucoes_de_rotina_responsavel",
        verbose_name="Responsável",
        help_text="Quem responde por esta ocorrência. Copiado da rotina quando ela é gerada.",
    )
    iniciado_em = models.DateTimeField("Iniciada em", null=True, blank=True)
    concluido_em = models.DateTimeField("Concluída em", null=True, blank=True)
    observacao_execucao = models.TextField(
        "Observação",
        blank=True,
        help_text="O que se registrou sobre a ocorrência. Obrigatório ao ignorar ou cancelar.",
    )
    resultado = models.CharField(
        "Resultado",
        max_length=20,
        choices=ResultadoExecucao.choices,
        blank=True,
        help_text="O que se encontrou ao fazer. Obrigatório para concluir.",
    )
    demanda_gerada = models.ForeignKey(
        Demanda,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="execucoes_de_rotina",
        verbose_name="Demanda gerada",
        help_text="A demanda aberta a partir desta ocorrência, quando houver.",
    )
    manutencao_gerada = models.ForeignKey(
        Manutencao,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="execucoes_de_rotina",
        verbose_name="Manutenção gerada",
        help_text="A manutenção preventiva criada a partir desta ocorrência, quando houver.",
    )
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)

    objects = ExecucaoRotinaQuerySet.as_manager()

    class Meta:
        verbose_name = "Execução de rotina"
        verbose_name_plural = "Execuções de rotina"
        ordering = ["-data_prevista"]
        constraints = [
            # Uma ocorrência por data, por rotina. É a última barreira contra
            # duas requisições simultâneas gerando a mesma execução.
            models.UniqueConstraint(
                fields=["rotina", "data_prevista"],
                name="rotina_execucao_unica",
            ),
            # A situação diz o que aconteceu; a data diz quando. Uma execução
            # concluída registra quando foi concluída — só ela.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=StatusExecucao.CONCLUIDA,
                        concluido_em__isnull=False,
                    )
                    | (
                        ~models.Q(status=StatusExecucao.CONCLUIDA)
                        & models.Q(concluido_em__isnull=True)
                    )
                ),
                name="execucao_concluida_registra_quando",
            ),
            # Começar uma execução deixa rastro de quando começou.
            models.CheckConstraint(
                condition=~models.Q(status=StatusExecucao.EM_ANDAMENTO)
                | models.Q(iniciado_em__isnull=False),
                name="execucao_em_andamento_tem_inicio",
            ),
        ]
        indexes = [
            models.Index(
                fields=["condominio", "data_prevista"],
                name="execucao_cond_data_idx",
            ),
            models.Index(
                fields=["rotina", "data_prevista"],
                name="execucao_rotina_data_idx",
            ),
            models.Index(
                fields=["responsavel", "status"],
                name="execucao_resp_status_idx",
            ),
            models.Index(
                fields=["condominio", "status"],
                name="execucao_cond_status_idx",
            ),
        ]

    def __str__(self):
        return f"{self.rotina} · {self.data_prevista:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        """Guarda a execução já normalizada, venha ela do serviço ou não."""
        self.observacao_execucao = _texto_longo(self.observacao_execucao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma execução incoerente antes que ela chegue ao banco.

        Nada aqui pertence a outro condomínio: a rotina, a demanda e a
        manutenção são do mesmo condomínio da execução. A situação e as datas
        também precisam combinar.
        """
        erros = {}

        if (
            self.rotina_id
            and self.condominio_id
            and self.rotina.condominio_id != self.condominio_id
        ):
            erros["rotina"] = "A rotina escolhida não é deste condomínio."

        if (
            self.demanda_gerada_id
            and self.condominio_id
            and self.demanda_gerada.condominio_id != self.condominio_id
        ):
            erros["demanda_gerada"] = "A demanda é de outro condomínio."

        if (
            self.manutencao_gerada_id
            and self.condominio_id
            and self.manutencao_gerada.condominio_id != self.condominio_id
        ):
            erros["manutencao_gerada"] = "A manutenção é de outro condomínio."

        if self.status == StatusExecucao.CONCLUIDA:
            if self.concluido_em is None:
                erros["concluido_em"] = (
                    "Uma execução concluída precisa registrar quando foi concluída."
                )
            if not _texto(self.resultado):
                erros["resultado"] = "Diga o que se encontrou antes de concluir."
        elif self.concluido_em is not None:
            erros["concluido_em"] = (
                "Só uma execução concluída registra a data de conclusão."
            )

        if self.status == StatusExecucao.EM_ANDAMENTO and self.iniciado_em is None:
            erros["iniciado_em"] = "Uma execução em andamento precisa registrar quando começou."

        if erros:
            raise ValidationError(erros)

    @property
    def esta_encerrada(self) -> bool:
        """Concluída, ignorada ou cancelada: não muda mais de situação."""
        return self.status in SITUACOES_ENCERRADAS

    @property
    def esta_atrasada(self) -> bool:
        """Pendente com a data prevista já passada. Derivado, nunca persistido."""
        return (
            self.status in SITUACOES_PENDENTES
            and self.data_prevista < timezone.localdate()
        )

    @property
    def tem_checklist(self) -> bool:
        """Se a ocorrência carrega um checklist — a cópia feita na geração."""
        return bool(self.itens.all())

    @property
    def tem_item_obrigatorio_pendente(self) -> bool:
        """Se sobrou tarefa obrigatória por tratar.

        É esta conta que segura a conclusão: enquanto houver tarefa obrigatória
        pendente, a ocorrência não se dá por concluída. Tarefa opcional pendente
        não trava — ela fica registrada como o que não foi feito.
        """
        return any(
            item.obrigatorio and item.status == StatusItemChecklist.PENDENTE
            for item in self.itens.all()
        )

    @property
    def progresso_do_checklist(self) -> dict:
        """O andamento do checklist, derivado na hora e nunca guardado.

        Devolve o total, quantas tarefas foram concluídas, quantas seguem
        pendentes, quantas não se aplicavam, e o percentual tratado —
        concluídas mais não aplicáveis sobre o total. Sem checklist, tudo
        zerado.
        """
        total = concluidos = pendentes = nao_aplicaveis = 0
        for item in self.itens.all():
            total += 1
            if item.status == StatusItemChecklist.CONCLUIDO:
                concluidos += 1
            elif item.status == StatusItemChecklist.NAO_APLICAVEL:
                nao_aplicaveis += 1
            else:
                pendentes += 1
        tratados = concluidos + nao_aplicaveis
        return {
            "total": total,
            "concluidos": concluidos,
            "pendentes": pendentes,
            "nao_aplicaveis": nao_aplicaveis,
            "percentual": round(tratados * 100 / total) if total else 0,
        }


class ItemExecucaoChecklist(models.Model):
    """Uma tarefa do checklist dentro de uma ocorrência — o retrato do dia.

    A tarefa nasce da cópia dos itens ativos do modelo no momento em que a
    ocorrência é gerada. Descrição, ordem e obrigatoriedade são uma fotografia:
    mudar o modelo depois não reescreve esta linha, porque o que aconteceu
    naquele dia não muda.

    ``item_modelo`` fica como vínculo frouxo — serve para dizer de onde a tarefa
    veio —, e não como fonte de verdade: se o item do modelo for apagado, a
    ocorrência continua contando a mesma história.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execucao = models.ForeignKey(
        ExecucaoRotina,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Ocorrência",
    )
    item_modelo = models.ForeignKey(
        ItemModeloChecklist,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="itens_de_execucao",
        verbose_name="Tarefa de origem",
        help_text="De onde a tarefa veio. Apenas rastro; não reescreve o histórico.",
    )
    descricao = models.CharField(
        "Tarefa",
        max_length=200,
        help_text="O que se esperava fazer, como estava no modelo naquele dia.",
    )
    ordem = models.PositiveIntegerField(
        "Ordem",
        default=0,
        help_text="A posição da tarefa na lista, copiada do modelo.",
    )
    obrigatorio = models.BooleanField(
        "Obrigatório",
        default=True,
        help_text="Uma tarefa obrigatória pendente impede a conclusão da ocorrência.",
    )
    status = models.CharField(
        "Situação",
        max_length=20,
        choices=StatusItemChecklist.choices,
        default=StatusItemChecklist.PENDENTE,
    )
    observacao = models.TextField(
        "Observação",
        blank=True,
        help_text="Quando vale registrar algo sobre esta tarefa.",
    )
    concluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="itens_de_checklist_concluidos",
        verbose_name="Tratado por",
        help_text="Quem concluiu ou marcou a tarefa como não aplicável.",
    )
    concluido_em = models.DateTimeField("Tratado em", null=True, blank=True)
    criado_em = models.DateTimeField("Criada em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizada em", auto_now=True)

    class Meta:
        verbose_name = "Item do checklist da ocorrência"
        verbose_name_plural = "Itens do checklist da ocorrência"
        ordering = ["execucao", "ordem", "criado_em"]
        constraints = [
            # Uma tarefa pendente não registra quando foi tratada; uma tarefa
            # tratada — concluída ou não aplicável — registra.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=StatusItemChecklist.PENDENTE,
                        concluido_em__isnull=True,
                    )
                    | (
                        ~models.Q(status=StatusItemChecklist.PENDENTE)
                        & models.Q(concluido_em__isnull=False)
                    )
                ),
                name="item_execucao_tratado_registra_quando",
            ),
        ]
        indexes = [
            models.Index(
                fields=["execucao", "status"], name="item_exec_status_idx"
            ),
        ]

    def __str__(self):
        return self.descricao

    def save(self, *args, **kwargs):
        """Guarda a tarefa já normalizada."""
        self.descricao = _texto(self.descricao)
        self.observacao = _texto_longo(self.observacao)
        super().save(*args, **kwargs)

    def clean(self):
        """Recusa uma tarefa incoerente antes que ela chegue ao banco."""
        erros = {}
        if not _texto(self.descricao):
            erros["descricao"] = "Escreva o que precisa ser feito."

        if self.status == StatusItemChecklist.PENDENTE:
            if self.concluido_em is not None:
                erros["concluido_em"] = (
                    "Uma tarefa pendente ainda não tem momento de conclusão."
                )
        elif self.concluido_em is None:
            erros["concluido_em"] = (
                "Uma tarefa tratada precisa registrar quando foi tratada."
            )

        if erros:
            raise ValidationError(erros)

    @property
    def esta_tratado(self) -> bool:
        """Concluída ou não aplicável: a tarefa não depende mais de ninguém."""
        return self.status in STATUS_QUE_TRATAM_O_ITEM


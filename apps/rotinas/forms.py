"""Formulários do domínio de rotinas recorrentes.

Pede-se apenas o necessário para a rotina existir — título e início —, e o resto
aparece em progressive disclosure: tipo, descrição, periodicidade, contexto e
prioridade. A periodicidade é descrita em palavras simples: "todo dia", "a cada
N semanas", "todo dia 5". Nada de expressão de calendário na tela.

As listas de escolha são fechadas e vêm do condomínio da rotina: local,
equipamento, fornecedor e responsável são os dele, e de mais nenhum. Como a
lista é a mesma que o domínio aceita, um registro de outro condomínio é recusado
pelo próprio formulário, e não por uma checagem solta na tela.
"""
from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.models import Condominio
from apps.core.constants import Prioridade

from .constants import (
    DiaSemana,
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
    StatusItemChecklist,
    TipoRotina,
)
from .models import ModeloChecklist
from .permissoes import usuarios_elegiveis_como_responsavel

# A ordem em que as situações aparecem para escolha, do começo ao fim do ciclo.
ORDEM_DOS_STATUS = (
    StatusExecucao.PENDENTE,
    StatusExecucao.EM_ANDAMENTO,
    StatusExecucao.CONCLUIDA,
    StatusExecucao.IGNORADA,
    StatusExecucao.CANCELADA,
)

# Recortes de situação que a lista oferece. Não é filtro de calendário: é a
# leitura que decide o que fazer primeiro.
SITUACOES_DA_LISTA = (
    ("", "Todas as rotinas"),
    ("ativas", "Ativas"),
    ("vencidas", "Vencidas"),
    ("proximas", "Vencendo em 7 dias"),
    ("sem_proxima", "Sem próxima execução"),
    ("inativas", "Inativas"),
)

# Recortes da lista de execuções, na mesma ideia: o que merece atenção primeiro.
RECORTES_DE_EXECUCOES = (
    ("pendentes", "Pendentes"),
    ("atrasadas", "Atrasadas"),
    ("hoje", "De hoje"),
    ("proximas", "Próximos 7 dias"),
    ("com_checklist_pendente", "Checklist pendente"),
    ("concluidas", "Concluídas"),
    ("", "Todas"),
)

# Estados que uma tarefa do checklist pode assumir por uma ação rápida. Reabrir
# uma tarefa não entra: o retrato anda para a frente, nunca de volta a pendente.
STATUS_DO_ITEM_PARA_TRATAR = (
    (StatusItemChecklist.CONCLUIDO, "Concluída"),
    (StatusItemChecklist.NAO_APLICAVEL, "Não aplicável"),
)


def _escolhas_do_condominio(modelo, condominio, atual=None):
    """Os registros do condomínio oferecidos numa escolha.

    Só os que estão em uso — mais aquele que a rotina já aponta, para que um
    cadastro desativado depois continue legível sem voltar a ser oferecido.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return modelo.objects.none()

    queryset = modelo.objects.do_condominio(condominio)
    if atual is not None and getattr(atual, "pk", None):
        return queryset.filter(Q(ativo=True) | Q(pk=atual.pk))
    return queryset.ativos()


class RotinaForm(forms.Form):
    """Cria ou edita a regra de uma rotina.

    O conjunto de condomínios possíveis vem do vínculo ativo da pessoa, e nunca
    do navegador. Ao editar, o condomínio é fixo: rotina não muda de casa.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    titulo = forms.CharField(
        label="Título",
        max_length=200,
        error_messages={"required": "Escreva o que se repete."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Limpeza da caixa d'água",
                "autocomplete": "off",
            }
        ),
    )
    descricao = forms.CharField(
        label="Descrição",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que a atividade envolve, quando vale detalhar",
            }
        ),
    )
    tipo = forms.ChoiceField(
        label="Tipo",
        choices=TipoRotina.choices,
        initial=TipoRotina.OPERACIONAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    periodicidade_tipo = forms.ChoiceField(
        label="Periodicidade",
        choices=Periodicidade.choices,
        initial=Periodicidade.MENSAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    intervalo = forms.IntegerField(
        label="A cada",
        min_value=1,
        initial=1,
        error_messages={"min_value": "O intervalo precisa ser de pelo menos um período."},
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )
    dia_semana = forms.TypedChoiceField(
        label="Dia da semana",
        choices=DiaSemana.choices,
        coerce=int,
        empty_value=None,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    dia_mes = forms.IntegerField(
        label="Dia do mês",
        min_value=1,
        max_value=31,
        required=False,
        error_messages={"max_value": "O dia do mês vai de 1 a 31."},
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "31"}),
    )
    mes_ano = forms.IntegerField(
        label="Mês",
        min_value=1,
        max_value=12,
        required=False,
        error_messages={"max_value": "O mês vai de 1 a 12."},
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "12"}),
    )
    data_inicio = forms.DateField(
        label="Início",
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        error_messages={"required": "Diga a partir de quando a rotina existe."},
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
        ),
    )
    data_fim = forms.DateField(
        label="Fim",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
        ),
    )
    prioridade = forms.ChoiceField(
        label="Prioridade",
        choices=Prioridade.choices,
        initial=Prioridade.NORMAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    local = forms.ModelChoiceField(
        label="Local",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Sem local",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    equipamento = forms.ModelChoiceField(
        label="Equipamento",
        queryset=Equipamento.objects.none(),
        required=False,
        empty_label="Sem equipamento",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    fornecedor = forms.ModelChoiceField(
        label="Fornecedor",
        queryset=Fornecedor.objects.none(),
        required=False,
        empty_label="Sem fornecedor",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=User.objects.none(),
        required=False,
        empty_label="Sem responsável",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    checklist_modelo = forms.ModelChoiceField(
        label="Checklist",
        queryset=ModeloChecklist.objects.none(),
        required=False,
        empty_label="Sem checklist",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    gera_manutencao = forms.BooleanField(
        label="É uma manutenção preventiva",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(
        self,
        *args,
        condominios=None,
        condominio=None,
        rotina=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo_condominio = self.fields["condominio"]

        if condominio is not None:
            # Edição ou criação dentro de um condomínio já resolvido: não muda de casa.
            campo_condominio.queryset = Condominio.objects.filter(pk=condominio.pk)
            campo_condominio.initial = condominio.pk
            campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = condominio
        else:
            campo_condominio.queryset = permitidos
            elegiveis = list(permitidos)
            if len(elegiveis) == 1:
                campo_condominio.initial = elegiveis[0].pk
                campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = self._condominio_escolhido(permitidos)

        atual = self.condominio_efetivo
        escolhido = getattr(rotina, "pk", None) and rotina or None

        self.fields["local"].queryset = _escolhas_do_condominio(
            LocalOperacional, atual, getattr(escolhido, "local", None)
        )
        self.fields["equipamento"].queryset = _escolhas_do_condominio(
            Equipamento, atual, getattr(escolhido, "equipamento", None)
        )
        self.fields["fornecedor"].queryset = _escolhas_do_condominio(
            Fornecedor, atual, getattr(escolhido, "fornecedor", None)
        )
        self.fields["checklist_modelo"].queryset = _escolhas_do_condominio(
            ModeloChecklist, atual, getattr(escolhido, "checklist_modelo", None)
        )
        self.fields["responsavel"].queryset = usuarios_elegiveis_como_responsavel(atual)

    def _condominio_escolhido(self, permitidos):
        """Qual condomínio vale para montar as listas de escolha.

        Vale o que a pessoa enviou, desde que esteja entre os permitidos; senão,
        o único elegível, quando houver só um. Serve apenas para montar as
        opções — a validação continua sendo a do campo, contra a mesma lista.
        """
        enviado = self.data.get("condominio") if self.is_bound else None
        if enviado:
            escolhido = permitidos.filter(pk=enviado).first()
            if escolhido is not None:
                return escolhido

        elegiveis = list(permitidos)
        return elegiveis[0] if len(elegiveis) == 1 else None

    def clean_titulo(self):
        titulo = " ".join(self.cleaned_data["titulo"].split())
        if not titulo:
            raise forms.ValidationError("Escreva o que se repete.")
        return titulo

    def clean_descricao(self):
        return (self.cleaned_data["descricao"] or "").strip()

    def clean(self):
        """Recusa combinações que só aparecem juntando dois campos.

        Cada periodicidade pede o que precisa para ser calculada — o dia da
        semana no semanal, o dia e o mês no anual. E o equipamento e o local
        precisam concordar: um equipamento que fica em um lugar não é acompanhado
        em outro sem que alguém diga isso. Divergência aqui é recusada com o nome
        do lugar certo — nunca corrigida em silêncio.
        """
        dados = super().clean()
        periodicidade = dados.get("periodicidade_tipo")
        dia_semana = dados.get("dia_semana")
        dia_mes = dados.get("dia_mes")
        mes_ano = dados.get("mes_ano")

        if periodicidade == Periodicidade.SEMANAL and dia_semana is None:
            self.add_error("dia_semana", "Escolha o dia da semana da rotina.")

        if periodicidade == Periodicidade.ANUAL:
            if dia_mes is None:
                self.add_error("dia_mes", "Escolha o dia do ano da rotina.")
            if mes_ano is None:
                self.add_error("mes_ano", "Escolha o mês da rotina.")

        inicio = dados.get("data_inicio")
        fim = dados.get("data_fim")
        if inicio and fim and fim < inicio:
            self.add_error("data_fim", "O fim não pode ser anterior ao início da rotina.")

        equipamento = dados.get("equipamento")
        local = dados.get("local")
        if equipamento is not None and local is not None:
            local_do_equipamento = equipamento.local
            if local_do_equipamento is not None and local_do_equipamento.pk != local.pk:
                self.add_error(
                    "local",
                    f"O equipamento escolhido fica em {local_do_equipamento.nome}. "
                    "Escolha o mesmo local ou troque o equipamento.",
                )
        return dados


class AtivacaoModeloForm(RotinaForm):
    """Revisão humana das escolhas antes de criar uma atividade do modelo."""

    execucao_unica = forms.BooleanField(
        label="Criar apenas uma ocorrência, sem repetição",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    confirmar_frequencia = forms.BooleanField(
        label="Revisei e confirmei a frequência ou ocorrência única para este condomínio",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    confirmar_condicao = forms.BooleanField(
        label="Confirmo que a estrutura indicada existe no condomínio",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    confirmar_fornecedor = forms.BooleanField(
        label="Vou confirmar o fornecedor especializado antes da execução",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    confirmar_validacao_tecnica = forms.BooleanField(
        label="Confirmei a frequência com profissional ou documentação técnica",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, modelo, **kwargs):
        self.modelo = modelo
        super().__init__(*args, **kwargs)
        if not modelo.periodicidade_tipo:
            campo = self.fields["periodicidade_tipo"]
            campo.required = False
            campo.choices = [("", "Escolha uma frequência"), *campo.choices]
        self.fields["confirmar_condicao"].required = modelo.condicional
        self.fields["confirmar_fornecedor"].required = (
            modelo.exige_fornecedor_especializado
        )
        self.fields["confirmar_validacao_tecnica"].required = (
            modelo.exige_validacao_tecnica
        )

    def clean(self):
        dados = super().clean()
        if not dados.get("execucao_unica") and not dados.get("periodicidade_tipo"):
            self.add_error(
                "periodicidade_tipo",
                "Escolha uma frequência ou crie apenas uma ocorrência.",
            )
        return dados


class ChecklistForm(forms.Form):
    """Cria ou edita um modelo de checklist do condomínio.

    O checklist é contexto da Rotina, não um módulo à parte: aqui se dá o nome
    ao conjunto de tarefas e, quando vale, diz-se o que ele cobre. As tarefas em
    si entram depois, uma a uma, para a lista poder crescer sem formulário
    pesado.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    nome = forms.CharField(
        label="Nome",
        max_length=200,
        error_messages={"required": "Dê um nome ao checklist."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Limpeza diária da piscina",
                "autocomplete": "off",
            }
        ),
    )
    descricao = forms.CharField(
        label="Descrição",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Quando vale detalhar o que o checklist cobre",
            }
        ),
    )

    def __init__(self, *args, condominios=None, condominio=None, **kwargs):
        """As escolhas de condomínio vêm do vínculo da pessoa, nunca do navegador.

        Ao editar, o condomínio é fixo: o checklist não muda de casa.
        """
        super().__init__(*args, **kwargs)

        permitidos = (
            condominios if condominios is not None else Condominio.objects.none()
        )
        campo = self.fields["condominio"]

        if condominio is not None:
            campo.queryset = Condominio.objects.filter(pk=condominio.pk)
            campo.initial = condominio.pk
            campo.widget = forms.HiddenInput()
        else:
            campo.queryset = permitidos
            elegiveis = list(permitidos)
            if len(elegiveis) == 1:
                campo.initial = elegiveis[0].pk
                campo.widget = forms.HiddenInput()

    def clean_nome(self):
        nome = " ".join(self.cleaned_data["nome"].split())
        if not nome:
            raise forms.ValidationError("Dê um nome ao checklist.")
        return nome

    def clean_descricao(self):
        return (self.cleaned_data["descricao"] or "").strip()


class ItemDoChecklistForm(forms.Form):
    """Acrescenta ou ajusta uma tarefa do modelo de checklist.

    A ordem é um número simples, não uma posição arrastável: quem monta a lista
    diz em que sequência as tarefas costumam acontecer. Sem ordem informada, a
    tarefa entra no fim.
    """

    descricao = forms.CharField(
        label="Tarefa",
        max_length=200,
        error_messages={"required": "Escreva o que precisa ser feito."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Limpar piso",
                "autocomplete": "off",
            }
        ),
    )
    ordem = forms.IntegerField(
        label="Ordem",
        min_value=0,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
    )
    obrigatorio = forms.BooleanField(
        label="Obrigatória",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_descricao(self):
        descricao = " ".join(self.cleaned_data["descricao"].split())
        if not descricao:
            raise forms.ValidationError("Escreva o que precisa ser feito.")
        return descricao


class InicioDeExecucaoForm(forms.Form):
    """Confirma o começo de uma ocorrência.

    Não há o que preencher: o início é um marco, e o momento é registrado pelo
    domínio. O formulário existe para a confirmação ser explícita.
    """


class ConclusaoDeExecucaoForm(forms.Form):
    """Conclui a ocorrência dizendo o que se encontrou e quando.

    Concluir não é só encerrar: exige o resultado. A observação é opcional — nem
    toda atividade rende o que contar.

    A data é a do dia em que a verificação aconteceu, não a do registro: uma
    rotina feita em setembro pode ser lançada agora. Ela vem preenchida com hoje
    e pode recuar; em branco, vale o momento do registro. O futuro é recusado
    porque ainda não aconteceu. A agenda da rotina não é tocada por ela.
    """

    resultado = forms.ChoiceField(
        label="O que se encontrou",
        choices=ResultadoExecucao.choices,
        error_messages={"required": "Diga o que se encontrou antes de concluir."},
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    data_execucao = forms.DateField(
        label="Data da execução",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        initial=timezone.localdate,
        error_messages={
            "invalid": "Informe uma data válida, no formato dd/mm/aaaa.",
        },
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
        ),
    )
    observacao = forms.CharField(
        label="Observação",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que vale registrar sobre esta ocorrência",
            }
        ),
    )

    def clean_data_execucao(self):
        data = self.cleaned_data["data_execucao"]
        if data is not None and data > timezone.localdate():
            raise forms.ValidationError(
                "A data da execução não pode estar no futuro."
            )
        return data

    def clean_observacao(self):
        return (self.cleaned_data["observacao"] or "").strip()


class TratamentoDeItemForm(forms.Form):
    """Registra o que aconteceu com uma tarefa do checklist.

    Duas coisas podem ser ditas sobre uma tarefa: como ela ficou — concluída ou
    não aplicável — e o que vale anotar. Uma ação rápida manda só o estado; a
    anotação manda só a observação. Pelo menos uma das duas, para não gravar
    nada em branco.
    """

    status = forms.ChoiceField(
        label="Situação",
        choices=STATUS_DO_ITEM_PARA_TRATAR,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    observacao = forms.CharField(
        label="Observação",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que vale registrar sobre esta tarefa",
            }
        ),
    )

    def clean_observacao(self):
        return (self.cleaned_data["observacao"] or "").strip()

    def clean(self):
        """Recusa um envio sem nada a registrar."""
        dados = super().clean()
        if not dados.get("status") and not dados.get("observacao"):
            raise forms.ValidationError("Diga o que aconteceu com a tarefa.")
        return dados


class JustificativaDaExecucaoForm(forms.Form):
    """Registra por que uma ocorrência foi ignorada ou cancelada.

    A justificativa é obrigatória: dizer "não se aplicava" ou "não vai acontecer"
    sem dizer por quê não ajuda quem lê o histórico depois.
    """

    justificativa = forms.CharField(
        label="Justificativa",
        error_messages={"required": "Diga por quê."},
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Por que esta ocorrência não acontece como previsto",
            }
        ),
    )

    def clean_justificativa(self):
        justificativa = (self.cleaned_data["justificativa"] or "").strip()
        if not justificativa:
            raise forms.ValidationError("Diga por quê.")
        return justificativa


class DemandaDaExecucaoForm(forms.Form):
    """Abre uma demanda a partir do que a ocorrência encontrou.

    Vem pré-preenchida com o essencial, para a gestão revisar e confirmar. Nada
    é criado sozinho.
    """

    titulo = forms.CharField(
        label="Título",
        max_length=200,
        error_messages={"required": "Escreva o que precisa ser tratado."},
        widget=forms.TextInput(
            attrs={"class": "form-control", "autocomplete": "off"}
        ),
    )
    descricao = forms.CharField(
        label="Descrição",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    prioridade = forms.ChoiceField(
        label="Prioridade",
        choices=Prioridade.choices,
        initial=Prioridade.NORMAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    prazo = forms.DateField(
        label="Prazo",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
        ),
    )

    def clean_titulo(self):
        titulo = " ".join(self.cleaned_data["titulo"].split())
        if not titulo:
            raise forms.ValidationError("Escreva o que precisa ser tratado.")
        return titulo

    def clean_descricao(self):
        return (self.cleaned_data["descricao"] or "").strip()


class ManutencaoDaExecucaoForm(forms.Form):
    """Cria uma manutenção preventiva a partir de uma ocorrência.

    Vem pré-preenchida com o essencial, para a gestão revisar e confirmar. Nada
    é criado sozinho.
    """

    titulo = forms.CharField(
        label="Título",
        max_length=200,
        error_messages={"required": "Escreva o que precisa ser feito."},
        widget=forms.TextInput(
            attrs={"class": "form-control", "autocomplete": "off"}
        ),
    )
    descricao_problema = forms.CharField(
        label="Problema",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    data_prevista = forms.DateField(
        label="Data prevista",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
        ),
    )

    def clean_titulo(self):
        titulo = " ".join(self.cleaned_data["titulo"].split())
        if not titulo:
            raise forms.ValidationError("Escreva o que precisa ser feito.")
        return titulo

    def clean_descricao_problema(self):
        return (self.cleaned_data["descricao_problema"] or "").strip()


class FiltroDeRotinasForm(forms.Form):
    """Filtros essenciais da lista de rotinas. Nunca obrigatórios."""

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        required=False,
        empty_label="Todos os condomínios",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    tipo = forms.ChoiceField(
        label="Tipo",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=User.objects.none(),
        required=False,
        empty_label="Todos os responsáveis",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    local = forms.ModelChoiceField(
        label="Local",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Todos os locais",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    equipamento = forms.ModelChoiceField(
        label="Equipamento",
        queryset=Equipamento.objects.none(),
        required=False,
        empty_label="Todos os equipamentos",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    situacao = forms.ChoiceField(
        label="Situação",
        choices=SITUACOES_DA_LISTA,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    busca = forms.CharField(
        label="Buscar",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Título ou descrição",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(
        self,
        *args,
        condominios=None,
        responsaveis=None,
        locais=None,
        equipamentos=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.fields["condominio"].queryset = (
            condominios if condominios is not None else Condominio.objects.none()
        )
        self.fields["tipo"].choices = [("", "Todos os tipos")] + list(TipoRotina.choices)
        self.fields["responsavel"].queryset = (
            responsaveis if responsaveis is not None else User.objects.none()
        )
        self.fields["local"].queryset = (
            locais if locais is not None else LocalOperacional.objects.none()
        )
        self.fields["equipamento"].queryset = (
            equipamentos if equipamentos is not None else Equipamento.objects.none()
        )


class FiltroDeExecucoesForm(forms.Form):
    """Filtros essenciais da lista de execuções. Nunca obrigatórios."""

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        required=False,
        empty_label="Todos os condomínios",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    recorte = forms.ChoiceField(
        label="Situação",
        choices=RECORTES_DE_EXECUCOES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=User.objects.none(),
        required=False,
        empty_label="Todos os responsáveis",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    busca = forms.CharField(
        label="Buscar",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Rotina",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, condominios=None, responsaveis=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["condominio"].queryset = (
            condominios if condominios is not None else Condominio.objects.none()
        )
        self.fields["responsavel"].queryset = (
            responsaveis if responsaveis is not None else User.objects.none()
        )

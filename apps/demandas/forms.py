"""Formulários do domínio de demandas.

Pede-se apenas o necessário: título e condomínio são o mínimo para a demanda
existir, e o resto aparece em progressive disclosure — descrição, prioridade,
responsável, prazo e local. Nada de campo técnico na tela.

O responsável sai de uma lista fechada: só quem tem vínculo ativo com o
condomínio e enxerga o módulo pode ser escolhido. Como a lista é a mesma que o
domínio aceita, um responsável de outro condomínio é recusado pelo próprio
formulário, e não por uma checagem solta na view.
"""
from django import forms
from django.db.models import Q

from apps.accounts.models import User
from apps.cadastros.models import LocalOperacional
from apps.condominios.models import Condominio

from .constants import PrioridadeDemanda, StatusDemanda
from .permissoes import usuarios_elegiveis_como_responsavel
from .services import LIMITE_DA_OBSERVACAO


def _locais_do_condominio(condominio, atual=None):
    """Os locais oferecidos numa escolha.

    Só os que estão em uso — mais aquele que a demanda já aponta, para que um
    cadastro desativado depois continue legível sem voltar a ser oferecido.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return LocalOperacional.objects.none()

    locais = LocalOperacional.objects.do_condominio(condominio)
    if atual is not None and getattr(atual, "pk", None):
        return locais.filter(Q(ativo=True) | Q(pk=atual.pk)).order_by("nome")
    return locais.ativos().order_by("nome")


# A ordem em que as situações aparecem para escolha, do começo ao fim do ciclo.
ORDEM_DOS_STATUS = (
    StatusDemanda.ABERTA,
    StatusDemanda.EM_ANDAMENTO,
    StatusDemanda.AGUARDANDO,
    StatusDemanda.CONCLUIDA,
    StatusDemanda.CANCELADA,
)

# Recortes de prazo que a lista oferece. Não é filtro de calendário: é a leitura
# que decide o que fazer primeiro.
FILTROS_DE_PRAZO = (
    ("", "Qualquer prazo"),
    ("vencidas", "Vencidas"),
    ("hoje", "Vencem hoje"),
    ("proximas", "Vencem em 7 dias"),
    ("sem_prazo", "Sem prazo"),
)


class DemandaForm(forms.Form):
    """Cria ou edita o conteúdo de uma demanda.

    O conjunto de condomínios possíveis vem do vínculo ativo da pessoa, e nunca
    do navegador. Quando há apenas um, o campo continua sendo validado, mas não
    é pedido de novo. Ao editar, o condomínio é fixo: demanda não muda de casa.
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
        error_messages={"required": "Escreva o que a demanda precisa resolver."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Vazamento na garagem",
                "autocomplete": "off",
            }
        ),
    )
    descricao = forms.CharField(
        label="Descrição",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    prioridade = forms.ChoiceField(
        label="Prioridade",
        choices=PrioridadeDemanda.choices,
        initial=PrioridadeDemanda.NORMAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=User.objects.none(),
        required=False,
        empty_label="Sem responsável",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    prazo = forms.DateField(
        label="Prazo",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
    )
    local = forms.ModelChoiceField(
        label="Local",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Sem local definido",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, condominios=None, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo_condominio = self.fields["condominio"]

        if condominio is not None:
            # Edição: a demanda pertence a este condomínio e não muda de casa.
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

        self.fields["responsavel"].queryset = usuarios_elegiveis_como_responsavel(
            self.condominio_efetivo
        )
        self.fields["local"].queryset = _locais_do_condominio(
            self.condominio_efetivo, self._local_atual()
        )

    def _local_atual(self):
        """O local que a demanda já aponta, quando o formulário é de edição.

        Serve só para manter o cadastro atual entre as opções, mesmo que tenha
        sido desativado depois. Não é validação.
        """
        local_id = (self.initial or {}).get("local")
        if not local_id:
            return None
        return LocalOperacional.objects.filter(pk=local_id).first()

    def _condominio_escolhido(self, permitidos):
        """Qual condomínio vale para montar a lista de responsáveis.

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
            raise forms.ValidationError("Escreva o que a demanda precisa resolver.")
        return titulo

    def clean_descricao(self):
        return (self.cleaned_data["descricao"] or "").strip()


class AlteracaoDeStatusForm(forms.Form):
    """Move a demanda para outra situação entre as que esta pessoa alcança."""

    status = forms.ChoiceField(
        label="Nova situação",
        choices=(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, permitidos=(), **kwargs):
        super().__init__(*args, **kwargs)
        alcancaveis = set(permitidos)
        self.fields["status"].choices = [
            (situacao, StatusDemanda(situacao).label)
            for situacao in ORDEM_DOS_STATUS
            if situacao in alcancaveis
        ]


class ObservacaoDaDemandaForm(forms.Form):
    """Registra o que se sabe sobre a demanda, sem mexer em nenhum campo dela."""

    texto = forms.CharField(
        label="Observação",
        max_length=LIMITE_DA_OBSERVACAO,
        error_messages={"required": "Escreva a observação antes de registrar."},
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que se sabe agora sobre esta demanda",
            }
        ),
    )

    def clean_texto(self):
        texto = " ".join(self.cleaned_data["texto"].split())
        if not texto:
            raise forms.ValidationError("Escreva a observação antes de registrar.")
        return texto


class FiltroDeDemandasForm(forms.Form):
    """Filtros essenciais da lista. Nunca obrigatórios: filtrar é opcional."""

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        required=False,
        empty_label="Todos os condomínios",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    status = forms.ChoiceField(
        label="Situação",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    prioridade = forms.ChoiceField(
        label="Prioridade",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=None,
        required=False,
        empty_label="Todos os responsáveis",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    prazo = forms.ChoiceField(
        label="Prazo",
        choices=FILTROS_DE_PRAZO,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    busca = forms.CharField(
        label="Buscar",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Título, descrição ou local",
                "autocomplete": "off",
            }
        ),
    )
    minhas = forms.BooleanField(
        label="Só as minhas",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, condominios=None, responsaveis=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["condominio"].queryset = (
            condominios if condominios is not None else Condominio.objects.none()
        )
        self.fields["status"].choices = [("", "Todas as situações")] + [
            (situacao, StatusDemanda(situacao).label) for situacao in ORDEM_DOS_STATUS
        ]
        self.fields["prioridade"].choices = [("", "Todas as prioridades")] + list(
            PrioridadeDemanda.choices
        )
        self.fields["responsavel"].queryset = (
            responsaveis if responsaveis is not None else User.objects.none()
        )



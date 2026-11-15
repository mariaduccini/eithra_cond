"""Formulários da capacidade transversal de comunicação.

Pede-se o que o card vai mostrar — o título, a mensagem, o tom, o público e a
data —, e nada mais. A origem (a demanda ou a manutenção que motivou o aviso)
não é campo: ela é decidida por quem abre a tela, e chega pela view, não pelo
navegador.

O condomínio segue a mesma régua das demandas: o conjunto possível vem do vínculo
ativo da pessoa, nunca do que o navegador diz. Ao revisar, o condomínio é fixo —
um comunicado não muda de casa.
"""
from django import forms

from apps.condominios.models import Condominio

from .constants import (
    LIMITE_DA_MENSAGEM,
    LIMITE_DO_LOCAL,
    LIMITE_DO_PUBLICO,
    LIMITE_DO_TITULO,
    ModeloComunicado,
    OrientacaoDoCard,
    PublicoComunicado,
    StatusComunicacao,
)


def _linha(valor: str | None) -> str:
    """Uma linha só: sem espaços repetidos e sem espaço nas pontas."""
    return " ".join((valor or "").split())


class ComunicacaoForm(forms.Form):
    """Cria ou revisa o conteúdo de um comunicado.

    O que a pessoa escreveu é o que o card mostra. O modelo e a orientação mudam
    a arte, não o texto; o público e a data são dado estruturado do comunicado,
    mesmo antes de existir um canal de envio.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    modelo = forms.ChoiceField(
        label="Modelo",
        choices=ModeloComunicado.choices,
        initial=ModeloComunicado.AVISO,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    orientacao = forms.ChoiceField(
        label="Orientação",
        choices=OrientacaoDoCard.choices,
        initial=OrientacaoDoCard.VERTICAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    titulo = forms.CharField(
        label="Título",
        max_length=LIMITE_DO_TITULO,
        error_messages={"required": "Escreva o título do comunicado."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "A frase curta que o morador lê primeiro",
                "autocomplete": "off",
            }
        ),
    )
    mensagem = forms.CharField(
        label="Mensagem",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": "O texto do comunicado, com as suas palavras.",
            }
        ),
    )
    local = forms.CharField(
        label="Local",
        max_length=LIMITE_DO_LOCAL,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Bloco, torre, área comum ou equipamento",
                "autocomplete": "off",
            }
        ),
    )
    periodo = forms.DateField(
        label="Data ou período",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
        help_text="Quando o comunicado vale. Deixe em branco quando não houver data.",
    )
    publico = forms.ChoiceField(
        label="Público",
        choices=PublicoComunicado.choices,
        initial=PublicoComunicado.TODOS,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    publico_descricao = forms.CharField(
        label="Detalhe do público",
        max_length=LIMITE_DO_PUBLICO,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Quais unidades ou qual bloco, quando não é todos",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, condominios=None, condominio=None, instancia=None, **kwargs):
        super().__init__(*args, **kwargs)

        self._instancia = instancia
        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo_condominio = self.fields["condominio"]

        if condominio is not None:
            # Revisão: o comunicado pertence a este condomínio e não muda de casa.
            # Como a casa já está decidida aqui, o campo não volta a ser
            # perguntado nem exigido do navegador.
            campo_condominio.queryset = Condominio.objects.filter(pk=condominio.pk)
            campo_condominio.initial = condominio.pk
            campo_condominio.required = False
            campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = condominio
        else:
            campo_condominio.queryset = permitidos
            elegiveis = list(permitidos)
            if len(elegiveis) == 1:
                campo_condominio.initial = elegiveis[0].pk
                campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = self._condominio_escolhido(permitidos)

    def _condominio_escolhido(self, permitidos):
        """Qual condomínio vale para montar as opções dependentes.

        Vale o que a pessoa enviou, desde que esteja entre os permitidos; senão,
        o que já se sabe do contexto e, na falta dele, o único elegível. A
        validação continua sendo a do campo, contra a mesma lista.
        """
        if self.is_bound:
            enviado = self.data.get("condominio")
        else:
            enviado = (self.initial or {}).get("condominio")
        if enviado:
            escolhido = permitidos.filter(pk=enviado).first()
            if escolhido is not None:
                return escolhido

        elegiveis = list(permitidos)
        return elegiveis[0] if len(elegiveis) == 1 else None

    def clean_titulo(self):
        return _linha(self.cleaned_data["titulo"])

    def clean_mensagem(self):
        return (self.cleaned_data["mensagem"] or "").strip()

    def clean_local(self):
        return _linha(self.cleaned_data["local"])

    def clean_publico_descricao(self):
        return _linha(self.cleaned_data["publico_descricao"])

    def clean(self):
        """Recusa um comunicado sem título antes que ele chegue ao domínio."""
        dados = super().clean()
        if not dados.get("titulo"):
            self.add_error("titulo", "Escreva o título do comunicado.")
        return dados


class FiltroDeComunicacoesForm(forms.Form):
    """Filtros essenciais da lista de comunicados. Nunca obrigatórios."""

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
    modelo = forms.ChoiceField(
        label="Modelo",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    publico = forms.ChoiceField(
        label="Público",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    busca = forms.CharField(
        label="Buscar",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Título, mensagem ou local",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, condominios=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["condominio"].queryset = (
            condominios if condominios is not None else Condominio.objects.none()
        )
        self.fields["status"].choices = [("", "Todas as situações")] + list(
            StatusComunicacao.choices
        )
        self.fields["modelo"].choices = [("", "Todos os modelos")] + list(
            ModeloComunicado.choices
        )
        self.fields["publico"].choices = [("", "Todos os públicos")] + list(
            PublicoComunicado.choices
        )

"""Formulário de entrada do balanço.

Pede apenas o que o sistema ainda não sabe: qual condomínio e qual arquivo.
Nada de mês, fornecedor, categoria, observação ou valores — a competência será
lida do próprio documento, mais adiante. Pedir agora seria trabalho para a
gestão sem retorno.
"""
from django import forms

from apps.balancos.services import ErroDeValidacaoDeArquivo, validar_arquivo
from apps.condominios.models import Condominio


class ImportacaoBalancoForm(forms.Form):
    """Recebe o PDF do balanço.

    O conjunto de condomínios possíveis vem do vínculo ativo da pessoa, e não
    do que o navegador enviar. Quando há mais de um, a escolha é dela; quando
    há apenas um, o campo continua sendo validado, mas não é pedido de novo.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    arquivo = forms.FileField(
        label="Arquivo do balanço (PDF)",
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": "application/pdf"}
        ),
    )

    def __init__(self, *args, condominios=None, **kwargs):
        super().__init__(*args, **kwargs)

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo = self.fields["condominio"]
        campo.queryset = permitidos

        elegiveis = list(permitidos)
        if len(elegiveis) == 1:
            campo.initial = elegiveis[0].pk
            campo.widget = forms.HiddenInput()

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        try:
            validar_arquivo(arquivo)
        except ErroDeValidacaoDeArquivo as erro:
            raise forms.ValidationError(str(erro)) from erro
        return arquivo

"""Formulários dos cadastros básicos operacionais.

Cada formulário pede apenas o que o cadastro precisa: nome e tipo no local,
nome no equipamento, nome no fornecedor. O resto é opcional e pode ser
completado aos poucos.

Os campos são os do próprio cadastro. Assim a validação é a mesma do domínio —
o modelo recusa o que não faz sentido — e não existe uma segunda lista de
regras no formulário para divergir com o tempo.
"""
from django import forms
from django.db.models import Q

from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

from .constants import TipoPessoa
from .models import (
    Equipamento,
    EspecialidadeFornecedor,
    Fornecedor,
    LocalOperacional,
    TipoEquipamento,
    TipoLocalOperacional,
)

# Recorte de entrada de texto, repetido em quase todo campo. Aparência é
# assunto do tema Sneat: aqui só se diz qual classe de campo usar.
TEXTO = {"class": "form-control"}
SELECAO = {"class": "form-select"}
MULTIPLA = {"class": "form-select", "size": 6}
DATA = {"class": "form-control", "type": "date"}


def _do_condominio(modelo, condominio):
    """Todos os registros deste condomínio. Sem condomínio, conjunto vazio."""
    if condominio is None or not getattr(condominio, "pk", None):
        return modelo.objects.none()
    return modelo.objects.filter(condominio=condominio)


def _escolhiveis(modelo, condominio, ids_escolhidos=()):
    """O que pode ser escolhido agora, mais o que já estava escolhido.

    Desativar um cadastro o retira das opções novas — mas não pode fazer
    desaparecer um vínculo que já existia no registro que a gestão está
    corrigindo.
    """
    escolhidos = [chave for chave in ids_escolhidos if chave]
    return _do_condominio(modelo, condominio).filter(Q(ativo=True) | Q(pk__in=escolhidos))


class CondominioForm(forms.ModelForm):
    """O cadastro do condomínio: dados gerais, endereço e administração.

    Todos os campos administrativos são opcionais de propósito. Um condomínio
    que já existe continua válido sem eles, e a gestão completa o cadastro aos
    poucos — só o nome é obrigatório.

    Em modo de leitura (``somente_leitura``) os campos aparecem desabilitados:
    a supervisão vê o mesmo cadastro que a gestão, sem poder alterá-lo.
    """

    class Meta:
        model = Condominio
        fields = (
            "nome",
            "cnpj",
            "logradouro",
            "numero",
            "complemento",
            "bairro",
            "cidade",
            "uf",
            "cep",
            "telefone_administrativo",
            "email_administrativo",
            "quantidade_unidades",
            "observacao_administrativa",
        )
        widgets = {
            "nome": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "cnpj": forms.TextInput(
                attrs={**TEXTO, "placeholder": "00.000.000/0000-00", "autocomplete": "off"}
            ),
            "logradouro": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "numero": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "complemento": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "bairro": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "cidade": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "uf": forms.TextInput(
                attrs={**TEXTO, "maxlength": 2, "placeholder": "UF", "autocomplete": "off"}
            ),
            "cep": forms.TextInput(
                attrs={**TEXTO, "placeholder": "00000-000", "autocomplete": "off"}
            ),
            "telefone_administrativo": forms.TextInput(
                attrs={**TEXTO, "placeholder": "(00) 00000-0000", "autocomplete": "off"}
            ),
            "email_administrativo": forms.EmailInput(attrs={**TEXTO, "autocomplete": "off"}),
            "quantidade_unidades": forms.NumberInput(
                attrs={**TEXTO, "min": 0, "step": 1, "inputmode": "numeric"}
            ),
            "observacao_administrativa": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que a gestão precisa lembrar deste condomínio"}
            ),
        }

    def __init__(self, *args, somente_leitura=False, **kwargs):
        super().__init__(*args, **kwargs)
        if somente_leitura:
            for campo in self.fields.values():
                campo.disabled = True


class TipoLocalOperacionalForm(forms.ModelForm):
    """A lista com que o condomínio classifica os seus lugares.

    O nome é único dentro do condomínio, sem distinguir maiúsculas de
    minúsculas — a mesma regra do domínio, conferida pelo próprio cadastro.
    """

    class Meta:
        model = TipoLocalOperacional
        fields = ("nome", "descricao")
        widgets = {
            "nome": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Área comum, Garagem, Torre",
                    "autocomplete": "off",
                }
            ),
            "descricao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "Detalhe opcional do que entra neste tipo"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        # O tipo pertence a um condomínio: a lista de um prédio não classifica
        # o lugar do prédio do lado.
        if condominio is not None:
            self.instance.condominio = condominio


class TipoEquipamentoForm(forms.ModelForm):
    """A lista com que o condomínio classifica o que mantém."""

    class Meta:
        model = TipoEquipamento
        fields = ("nome", "descricao")
        widgets = {
            "nome": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Bomba, Portão, Elevador",
                    "autocomplete": "off",
                }
            ),
            "descricao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "Detalhe opcional do que entra neste tipo"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        if condominio is not None:
            self.instance.condominio = condominio


class EspecialidadeFornecedorForm(forms.ModelForm):
    """A lista do que um fornecedor sabe fazer neste condomínio."""

    class Meta:
        model = EspecialidadeFornecedor
        fields = ("nome", "descricao")
        widgets = {
            "nome": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Elétrica, Hidráulica, Elevadores",
                    "autocomplete": "off",
                }
            ),
            "descricao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "Detalhe opcional do que entra nesta especialidade"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        if condominio is not None:
            self.instance.condominio = condominio


class LocalForm(forms.ModelForm):
    """Cadastro de um lugar do condomínio."""

    class Meta:
        model = LocalOperacional
        fields = ("nome", "tipo_local", "descricao")
        widgets = {
            "nome": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Bloco A, Garagem subsolo, Elevador social",
                    "autocomplete": "off",
                }
            ),
            "tipo_local": forms.Select(attrs=SELECAO),
            "descricao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "Detalhe opcional deste lugar"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        local = self.instance
        # A lista de tipos é a deste condomínio — e traz de volta o tipo já
        # escolhido, mesmo que ele tenha sido desativado depois.
        self.fields["tipo_local"].queryset = _escolhiveis(
            TipoLocalOperacional,
            condominio,
            [local.tipo_local_id] if local.pk else (),
        )
        # Não classificar o lugar é uma resposta legítima.
        self.fields["tipo_local"].required = False
        self.fields["tipo_local"].empty_label = "Sem tipo definido"


class EquipamentoForm(forms.ModelForm):
    """Cadastro de um equipamento do condomínio.

    O local e o tipo só oferecem opções do próprio condomínio: a lista vem do
    vínculo da pessoa com o condomínio em que ela está trabalhando, e nunca do
    navegador.
    """

    class Meta:
        model = Equipamento
        fields = (
            "nome",
            "tipo_equipamento",
            "local",
            "fabricante",
            "modelo",
            "numero_serie",
            "patrimonio",
            "data_aquisicao",
            "data_instalacao",
            "observacao",
        )
        widgets = {
            "nome": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Bomba de recalque, Portão da garagem",
                    "autocomplete": "off",
                }
            ),
            "tipo_equipamento": forms.Select(attrs=SELECAO),
            "local": forms.Select(attrs=SELECAO),
            "fabricante": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "modelo": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "numero_serie": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "patrimonio": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "data_aquisicao": forms.DateInput(attrs=DATA),
            "data_instalacao": forms.DateInput(attrs=DATA),
            "observacao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que se sabe sobre o equipamento"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        equipamento = self.instance
        self.fields["local"].queryset = _escolhiveis(
            LocalOperacional,
            condominio,
            [equipamento.local_id] if equipamento.pk else (),
        )
        self.fields["local"].empty_label = "Sem local definido"
        self.fields["tipo_equipamento"].queryset = _escolhiveis(
            TipoEquipamento,
            condominio,
            [equipamento.tipo_equipamento_id] if equipamento.pk else (),
        )
        # Não classificar é uma resposta legítima: o equipamento existe e serve
        # sem tipo.
        self.fields["tipo_equipamento"].required = False
        self.fields["tipo_equipamento"].empty_label = "Sem tipo definido"


class FornecedorForm(forms.ModelForm):
    """Cadastro de quem atende o condomínio.

    O cadastro do dia a dia e a disponibilização aos moradores são a mesma
    ficha: quem corrige o telefone do fornecedor é quem sabe se ele pode ser
    mostrado aos moradores. Os dados dos moradores ficam em campos próprios,
    recolhidos na tela, e nunca são lidos dos campos internos.
    """

    especialidades = forms.ModelMultipleChoiceField(
        label="Especialidades atendidas",
        queryset=EspecialidadeFornecedor.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs=MULTIPLA),
    )

    class Meta:
        model = Fornecedor
        fields = (
            "nome_razao_social",
            "nome_fantasia",
            "tipo_pessoa",
            "documento",
            "email",
            "telefone",
            "contato",
            "observacao",
            "indicado_para_moradores",
            "ativo_para_moradores",
            "descricao_publica",
            "telefone_publico",
            "whatsapp_publico",
            "site_publico",
            "observacao_publica",
        )
        widgets = {
            "nome_razao_social": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Elevadores Silva Ltda.",
                    "autocomplete": "off",
                }
            ),
            "nome_fantasia": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "tipo_pessoa": forms.Select(attrs=SELECAO, choices=TipoPessoa.choices),
            "documento": forms.TextInput(
                attrs={**TEXTO, "placeholder": "CPF ou CNPJ", "autocomplete": "off"}
            ),
            "email": forms.EmailInput(attrs={**TEXTO, "autocomplete": "off"}),
            "telefone": forms.TextInput(
                attrs={**TEXTO, "placeholder": "(00) 00000-0000", "autocomplete": "off"}
            ),
            "contato": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "observacao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que se sabe sobre o fornecedor"}
            ),
            "indicado_para_moradores": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "ativo_para_moradores": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "descricao_publica": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "Como o morador procura este fornecedor"}
            ),
            "telefone_publico": forms.TextInput(
                attrs={**TEXTO, "placeholder": "(00) 00000-0000", "autocomplete": "off"}
            ),
            "whatsapp_publico": forms.TextInput(
                attrs={**TEXTO, "placeholder": "(00) 00000-0000", "autocomplete": "off"}
            ),
            "site_publico": forms.URLInput(
                attrs={**TEXTO, "placeholder": "https://", "autocomplete": "off"}
            ),
            "observacao_publica": forms.Textarea(
                attrs={**TEXTO, "rows": 2, "placeholder": "Horário, como funciona o atendimento"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Não saber se é pessoa física ou jurídica é comum no primeiro contato:
        # quem não escolhe fica com "Não informado", o padrão do domínio. Sem
        # isto, o formulário obrigaria a escolher e já viria apontando para
        # "Pessoa física".
        self.fields["tipo_pessoa"].required = False
        self.fields["tipo_pessoa"].choices = [("", "Não informado")] + list(
            TipoPessoa.choices
        )
        # Marcar é escolher, não preencher: a caixa desmarcada é uma resposta
        # legítima — "não indico" —, e não um campo em falta.
        self.fields["indicado_para_moradores"].required = False
        self.fields["ativo_para_moradores"].required = False
        # O condomínio não é campo do formulário — quem decide é o vínculo da
        # pessoa, na view. Mas o cadastro precisa saber a que condomínio
        # pertence para o domínio recusar, na própria validação, um documento
        # que já existe ali. Sem isso, a regra só apareceria como erro do banco.
        if condominio is not None:
            self.instance.condominio = condominio

        ids_especialidades = (
            list(self.instance.especialidades.values_list("especialidade_id", flat=True))
            if self.instance.pk
            else []
        )
        self.fields["especialidades"].queryset = _escolhiveis(
            EspecialidadeFornecedor, condominio, ids_especialidades
        )
        if not self.is_bound and self.instance.pk:
            # O que já é atendido aparece marcado: corrigir o fornecedor não
            # pode começar com a lista em branco.
            self.initial["especialidades"] = ids_especialidades

    def clean_tipo_pessoa(self):
        return self.cleaned_data.get("tipo_pessoa") or TipoPessoa.NAO_INFORMADO


class AdicionarAcessoForm(forms.Form):
    """Dar acesso a alguém neste condomínio.

    O nome só é exigido quando o e-mail ainda não pertence a ninguém conhecido —
    e quem diz isso é o domínio, não o formulário. Sem isto, a tela recusaria
    uma pessoa que já existe por falta de um dado que já se sabe.
    """

    nome_completo = forms.CharField(
        label="Nome",
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={**TEXTO, "placeholder": "Nome de quem vai ter acesso", "autocomplete": "off"}
        ),
    )
    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={**TEXTO, "autocomplete": "off"}),
    )
    papel = forms.ChoiceField(
        label="Papel no condomínio",
        choices=Papel.choices,
        widget=forms.Select(attrs=SELECAO),
    )


class PapelForm(forms.Form):
    """O papel de uma pessoa neste condomínio."""

    papel = forms.ChoiceField(
        label="Papel no condomínio",
        choices=Papel.choices,
        widget=forms.Select(attrs=SELECAO),
    )

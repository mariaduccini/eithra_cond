"""Formulários operacionais da Portaria."""
from datetime import timedelta

from django import forms
from django.utils import timezone

from apps.cadastros.models import LocalOperacional
from apps.comunidade.constants import TipoVinculoPessoaUnidade
from apps.comunidade.models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
)

from .constants import (
    ESCOLHAS_DE_TIPO_EM_USO,
    InstrucaoEntrega,
    MeioAutorizacaoPortaria,
    NaturezaObjetoCorreios,
    StatusAutorizacaoPortaria,
    TipoAcessoPortaria,
)
from .models import AutorizacaoPortaria

TEXTO = {"class": "form-control"}
SELECAO = {"class": "form-select"}
DATA_HORA = {"class": "form-control", "type": "datetime-local"}

# A empresa do prestador é texto livre — não há cadastro prévio e a portaria não
# bloqueia a passagem de quem não está na base. O campo continua sendo o mesmo;
# o que muda é que ele lembra o que já foi registrado. A lista de sugestões é da
# tela, e o marcador diz de onde ela vem: sem JavaScript, isto segue um campo de
# texto comum, com entrada livre e sem bloquear nada.
EMPRESA = {
    **TEXTO,
    "placeholder": "Buscar ou digitar empresa",
    "autocomplete": "off",
    "data-eithra-empresa": "empresas-conhecidas",
}

# Quem responde por uma unidade pertence ao condomínio; não é recebido como
# visita. Estes papéis ficam de fora da lista de "pessoa conhecida" para que um
# morador não seja registrado como terceiro por engano.
TIPOS_DE_RESIDENTE = (
    TipoVinculoPessoaUnidade.PROPRIETARIO,
    TipoVinculoPessoaUnidade.MORADOR,
    TipoVinculoPessoaUnidade.LOCATARIO,
    TipoVinculoPessoaUnidade.FAMILIAR,
)


def _do_condominio(modelo, condominio):
    if condominio is None or not getattr(condominio, "pk", None):
        return modelo.objects.none()
    return modelo.objects.filter(condominio=condominio)


def _pessoas_ativas(condominio):
    """Pessoas que a portaria pode receber como terceiro.

    Moradores, proprietários, locatários e familiares saem da lista: quem mora
    já está dentro, e a portaria registra a passagem de quem vem de fora.
    """
    residentes = (
        VinculoPessoaUnidade.objects.do_condominio(condominio)
        .vigentes()
        .filter(tipo_vinculo__in=TIPOS_DE_RESIDENTE)
        .values_list("pessoa_id", flat=True)
    )
    return (
        _do_condominio(Pessoa, condominio)
        .filter(ativa=True)
        .exclude(pk__in=residentes)
        .order_by("nome")
    )


def _unidades_ativas(condominio):
    return _do_condominio(Unidade, condominio).filter(ativa=True).order_by(
        "bloco_torre", "identificador"
    )


def _locais_ativos(condominio):
    return (
        _do_condominio(LocalOperacional, condominio)
        .filter(ativo=True)
        .order_by("nome")
    )


def _veiculos_ativos(condominio):
    return _do_condominio(Veiculo, condominio).filter(ativo=True).order_by("placa")


class AutorizacaoPortariaForm(forms.ModelForm):
    class Meta:
        model = AutorizacaoPortaria
        fields = (
            "tipo_acesso",
            "status",
            "pessoa",
            "nome",
            "telefone",
            "documento",
            "empresa",
            "unidade",
            "local",
            "veiculo",
            "placa",
            "motivo",
            "natureza_objeto",
            "instrucao_entrega",
            "inicio_previsto",
            "fim_previsto",
            "observacao",
        )
        widgets = {
            "tipo_acesso": forms.Select(attrs=SELECAO),
            "status": forms.Select(attrs=SELECAO),
            "pessoa": forms.Select(attrs=SELECAO),
            "nome": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Nome de quem está esperado"}
            ),
            "telefone": forms.TextInput(attrs={**TEXTO, "placeholder": "Telefone"}),
            "documento": forms.TextInput(attrs={**TEXTO, "placeholder": "Documento"}),
            "empresa": forms.TextInput(attrs=EMPRESA),
            "unidade": forms.Select(attrs=SELECAO),
            "local": forms.Select(attrs=SELECAO),
            "veiculo": forms.Select(attrs=SELECAO),
            "placa": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Placa, quando houver"}
            ),
            "motivo": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Visita, serviço, entrega..."}
            ),
            "natureza_objeto": forms.Select(attrs=SELECAO),
            "instrucao_entrega": forms.Select(attrs=SELECAO),
            "inicio_previsto": forms.DateTimeInput(attrs=DATA_HORA),
            "fim_previsto": forms.DateTimeInput(attrs=DATA_HORA),
            "observacao": forms.Textarea(attrs={**TEXTO, "rows": 2}),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pessoa"].queryset = _pessoas_ativas(condominio)
        self.fields["unidade"].queryset = _unidades_ativas(condominio)
        self.fields["local"].queryset = _locais_ativos(condominio)
        self.fields["veiculo"].queryset = _veiculos_ativos(condominio)
        self.fields["pessoa"].required = False
        self.fields["unidade"].required = False
        self.fields["local"].required = False
        self.fields["veiculo"].required = False
        self.fields["tipo_acesso"].choices = ESCOLHAS_DE_TIPO_EM_USO
        # Quem registra um esperado é a própria portaria, e o que ela registra
        # já está liberado: a situação nasce autorizada e não vira pergunta. O
        # valor continua vindo do formulário quando informado; faltando, quem
        # responde é o domínio.
        self.fields["status"].required = False
        self.fields["status"].initial = StatusAutorizacaoPortaria.AUTORIZADO
        # A janela de validade também é do domínio: o esperado vale a partir de
        # agora, por duas horas. Quem tem um horário diferente informa; quem não
        # tem não precisa responder por isso.
        self.fields["inicio_previsto"].required = False
        self.fields["fim_previsto"].required = False
        agora = timezone.localtime().replace(second=0, microsecond=0)
        self.fields["inicio_previsto"].initial = agora.strftime("%Y-%m-%dT%H:%M")
        self.fields["fim_previsto"].initial = (
            agora + timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M")
        if condominio is not None:
            self.instance.condominio = condominio

    def clean(self):
        """Completa o que a portaria não precisa responder.

        Situação e janela de validade não são perguntas da portaria — o
        esperado nasce autorizado, a partir de agora, com duas horas de
        validade. Os campos continuam aceitando o valor informado; faltando,
        quem responde é o domínio.
        """
        dados = super().clean()
        if not dados.get("status"):
            dados["status"] = StatusAutorizacaoPortaria.AUTORIZADO
        agora = timezone.localtime().replace(second=0, microsecond=0)
        if not dados.get("inicio_previsto"):
            dados["inicio_previsto"] = agora
        if not dados.get("fim_previsto"):
            dados["fim_previsto"] = agora + timedelta(hours=2)
        return dados


class ChegadaNaoPrevistaForm(forms.Form):
    """Registro curto de chegada, com o resultado da autorização.

    A portaria pergunta o mínimo: quem chegou, para onde vai e qual foi o
    resultado do contato. Autorização não é entrada; por isso o resultado fica
    explícito no formulário, e só o resultado "Autorizado" abre presença.

    Visita comum nasce pelo contexto da unidade. Prestador, fornecedor e
    técnico podem apontar para a unidade ou para uma área comum, com empresa
    cadastrada ou digitada livremente. O formulário guarda a escolha, mas não
    obriga um cadastro prévio para deixar a operação andar.
    """

    unidade = forms.ModelChoiceField(
        label="Unidade",
        queryset=Unidade.objects.none(),
        required=False,
        widget=forms.HiddenInput,
        error_messages={"invalid_choice": "Escolha a unidade de destino."},
    )
    autorizador_pessoa = forms.ModelChoiceField(
        label="Autorizador",
        queryset=Pessoa.objects.none(),
        required=False,
        widget=forms.HiddenInput,
    )
    nome = forms.CharField(
        label="Quem chegou?",
        error_messages={"required": "Informe quem chegou."},
        widget=forms.TextInput(
            attrs={**TEXTO, "placeholder": "Nome do visitante ou prestador"}
        ),
    )
    tipo_acesso = forms.ChoiceField(
        label="Tipo",
        choices=ESCOLHAS_DE_TIPO_EM_USO,
        initial=ESCOLHAS_DE_TIPO_EM_USO[0][0],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    autorizador_nome = forms.CharField(
        label="Quem autorizou",
        required=False,
        widget=forms.TextInput(
            attrs={**TEXTO, "placeholder": "Nome de quem autorizou, quando houver"}
        ),
    )
    status = forms.ChoiceField(
        label="Resultado da autorização",
        choices=(
            (
                StatusAutorizacaoPortaria.AUTORIZADO,
                StatusAutorizacaoPortaria.AUTORIZADO.label,
            ),
            (
                StatusAutorizacaoPortaria.NAO_AUTORIZADO,
                StatusAutorizacaoPortaria.NAO_AUTORIZADO.label,
            ),
            (
                StatusAutorizacaoPortaria.SEM_RESPOSTA,
                StatusAutorizacaoPortaria.SEM_RESPOSTA.label,
            ),
            (
                StatusAutorizacaoPortaria.AGUARDANDO,
                StatusAutorizacaoPortaria.AGUARDANDO.label,
            ),
        ),
        initial=StatusAutorizacaoPortaria.AUTORIZADO,
        required=False,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    meio_autorizacao = forms.ChoiceField(
        label="Meio de autorização",
        required=False,
        choices=(("", "Escolha"), *MeioAutorizacaoPortaria.choices),
        widget=forms.Select(attrs=SELECAO),
    )
    empresa = forms.CharField(
        label="Empresa ou fornecedor",
        required=False,
        widget=forms.TextInput(attrs=EMPRESA),
    )
    local = forms.ModelChoiceField(
        label="Local",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Escolha o local",
        widget=forms.Select(attrs=SELECAO),
    )
    placa = forms.CharField(
        label="Placa",
        required=False,
        widget=forms.TextInput(attrs={**TEXTO, "placeholder": "Placa, quando houver"}),
    )
    telefone = forms.CharField(
        label="Telefone",
        required=False,
        widget=forms.TextInput(attrs={**TEXTO, "placeholder": "Telefone"}),
    )
    documento = forms.CharField(
        label="Documento",
        required=False,
        widget=forms.TextInput(attrs={**TEXTO, "placeholder": "Documento"}),
    )
    motivo = forms.CharField(
        label="Motivo",
        required=False,
        widget=forms.TextInput(
            attrs={**TEXTO, "placeholder": "Visita, serviço, entrega..."}
        ),
    )
    natureza_objeto = forms.ChoiceField(
        label="Natureza do objeto",
        required=False,
        choices=(("", "Escolha"), *NaturezaObjetoCorreios.choices),
        widget=forms.Select(attrs=SELECAO),
    )
    instrucao_entrega = forms.ChoiceField(
        label="Instrução de entrega",
        required=False,
        choices=(("", "Escolha"), *InstrucaoEntrega.choices),
        widget=forms.Select(attrs=SELECAO),
    )
    observacao = forms.CharField(
        label="Observação",
        required=False,
        widget=forms.Textarea(attrs={**TEXTO, "rows": 2}),
    )
    passo = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unidade"].queryset = _unidades_ativas(condominio)
        self.fields["autorizador_pessoa"].queryset = _do_condominio(Pessoa, condominio)
        self.fields["local"].queryset = _locais_ativos(condominio)

    def clean(self):
        dados = super().clean()
        tipo = dados.get("tipo_acesso")
        unidade = dados.get("unidade")
        local = dados.get("local")
        status = dados.get("status") or StatusAutorizacaoPortaria.AGUARDANDO
        meio = dados.get("meio_autorizacao") or ""
        dados["status"] = status

        if tipo == TipoAcessoPortaria.VISITANTE and not unidade:
            self.add_error("unidade", "Escolha a unidade que está recebendo a visita.")
        elif tipo != TipoAcessoPortaria.VISITANTE and not unidade and not local:
            self.add_error(
                None, "Escolha a unidade de destino ou a área comum desta chegada."
            )

        if status in (
            StatusAutorizacaoPortaria.AUTORIZADO,
            StatusAutorizacaoPortaria.NAO_AUTORIZADO,
        ) and not meio:
            self.add_error(
                "meio_autorizacao",
                "Informe por qual meio esse resultado foi registrado.",
            )

        if tipo == TipoAcessoPortaria.VISITANTE:
            dados["empresa"] = ""
            dados["motivo"] = ""
            dados["local"] = None

        # Natureza do objeto é assunto dos Correios e instrução de entrega é
        # assunto do Delivery. Fora do seu tipo, o valor não faz sentido e não
        # entra no registro — melhor vazio do que guardado errado.
        if tipo != TipoAcessoPortaria.CORREIOS:
            dados["natureza_objeto"] = ""
        if tipo != TipoAcessoPortaria.DELIVERY:
            dados["instrucao_entrega"] = ""

        if status in (
            StatusAutorizacaoPortaria.SEM_RESPOSTA,
            StatusAutorizacaoPortaria.AGUARDANDO,
        ) and not dados.get("autorizador_pessoa"):
            dados["autorizador_nome"] = ""

        return dados


class VerificarAutorizacaoForm(forms.Form):
    """Conclui uma chegada que ficou aguardando confirmação."""

    autorizador_pessoa = forms.ModelChoiceField(
        label="Autorizador",
        queryset=Pessoa.objects.none(),
        required=False,
        widget=forms.HiddenInput,
    )
    autorizador_nome = forms.CharField(
        label="Quem autorizou",
        required=False,
        widget=forms.TextInput(
            attrs={**TEXTO, "placeholder": "Nome de quem autorizou, quando houver"}
        ),
    )
    status = forms.ChoiceField(
        label="Resultado da autorização",
        choices=(
            (
                StatusAutorizacaoPortaria.AUTORIZADO,
                StatusAutorizacaoPortaria.AUTORIZADO.label,
            ),
            (
                StatusAutorizacaoPortaria.NAO_AUTORIZADO,
                StatusAutorizacaoPortaria.NAO_AUTORIZADO.label,
            ),
            (
                StatusAutorizacaoPortaria.SEM_RESPOSTA,
                StatusAutorizacaoPortaria.SEM_RESPOSTA.label,
            ),
            (
                StatusAutorizacaoPortaria.AGUARDANDO,
                StatusAutorizacaoPortaria.AGUARDANDO.label,
            ),
        ),
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    meio_autorizacao = forms.ChoiceField(
        label="Meio de autorização",
        required=False,
        choices=(("", "Escolha"), *MeioAutorizacaoPortaria.choices),
        widget=forms.Select(attrs=SELECAO),
    )
    observacao = forms.CharField(
        label="Observação",
        required=False,
        widget=forms.Textarea(attrs={**TEXTO, "rows": 2}),
    )
    passo = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["autorizador_pessoa"].queryset = _do_condominio(Pessoa, condominio)

    def clean(self):
        dados = super().clean()
        status = dados.get("status")
        meio = dados.get("meio_autorizacao") or ""
        if status in (
            StatusAutorizacaoPortaria.AUTORIZADO,
            StatusAutorizacaoPortaria.NAO_AUTORIZADO,
        ) and not meio:
            self.add_error(
                "meio_autorizacao",
                "Informe por qual meio esse resultado foi registrado.",
            )
        if status in (
            StatusAutorizacaoPortaria.SEM_RESPOSTA,
            StatusAutorizacaoPortaria.AGUARDANDO,
        ) and not dados.get("autorizador_pessoa"):
            dados["autorizador_nome"] = ""
        return dados

"""Formulários do domínio de demandas.

Pede-se apenas o necessário. A pergunta principal é uma só — o que aconteceu —,
e o que der para ler desse relato já vem preenchido: assunto, local,
equipamento, tipo de problema, impacto e risco. O que não der continua sendo
pergunta, com ajuste à mão.

A prioridade chega preenchida pelo motor: a regra estruturada lê o relato e
sustenta o peso, sem exigir que quem registra o escolha. O campo continua
disponível para a correção de quem tem permissão, e a escolha manual — quando
existe — prevalece e fica no histórico. O prazo não é criado automaticamente:
a regra de prazo segue configurável, mas fora do fluxo de registro.

Duas coisas que a leitura produz **não** viram campo, porque não são dado da
demanda: a área sai do tipo do local cadastrado, e a recorrência é uma leitura
do histórico. Guardar qualquer uma delas seria criar uma segunda fonte de
verdade que envelhece sozinha.

A classificação nasce no registro e não é reescrita pela edição estrutural: o
que a leitura propôs fica guardado com a origem e a confiança de cada campo,
separado do que a demanda passou a valer.

O responsável sai de uma lista fechada: só quem tem vínculo ativo com o
condomínio e enxerga o módulo pode ser escolhido. Como a lista é a mesma que o
domínio aceita, um responsável de outro condomínio é recusado pelo próprio
formulário, e não por uma checagem solta na view.
"""
from django import forms
from django.db.models import Q

from apps.accounts.models import User
from apps.cadastros.models import Equipamento, LocalOperacional
from apps.comunidade.models import Pessoa, VinculoPessoaUnidade
from apps.condominios.models import Condominio

from . import derivacao, interpretacao
from .constants import (
    ACAO_DE_REABERTURA,
    ACOES_DE_STATUS,
    AguardandoTipo,
    ImpactoDemanda,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
    TipoProblema,
)
from .derivacao import Leitura, local_do_relato
from .evidencias import ACEITE_DE_EVIDENCIA, limites_legiveis
from .permissoes import usuarios_elegiveis_como_responsavel
from .services import CAMPOS_DO_ACOMPANHAMENTO, LIMITE_DA_OBSERVACAO


def _linha(valor: str | None) -> str:
    """Uma linha só: sem espaços repetidos e sem espaço nas pontas."""
    return " ".join((valor or "").split())


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


def _locais_entre(condominios):
    """Os locais elegíveis entre os condomínios em vista.

    Serve para o filtro da listagem. A escolha continua sendo real: só os locais
    ativos dos condomínios que a pessoa pode consultar entram na lista.
    """
    if condominios is None:
        return LocalOperacional.objects.none()
    return LocalOperacional.objects.filter(
        condominio__in=condominios,
        ativo=True,
    ).order_by("nome")


def _equipamentos_do_condominio(condominio, atual=None):
    """Os equipamentos oferecidos numa escolha.

    Mesma regra dos locais: só o que está em uso — mais aquele que a demanda já
    aponta, para que um cadastro desativado depois continue legível sem voltar a
    ser oferecido.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return Equipamento.objects.none()

    equipamentos = Equipamento.objects.do_condominio(condominio)
    if atual is not None and getattr(atual, "pk", None):
        return equipamentos.filter(Q(ativo=True) | Q(pk=atual.pk)).order_by("nome")
    return equipamentos.ativos().order_by("nome")


def _equipamentos_entre(condominios):
    """Os equipamentos elegíveis entre os condomínios em vista."""
    if condominios is None:
        return Equipamento.objects.none()
    return Equipamento.objects.filter(
        condominio__in=condominios,
        ativo=True,
    ).order_by("nome")


class _SolicitanteDoCondominio(forms.ModelChoiceField):
    """A pessoa escolhida como solicitante, com a unidade à vista.

    Nome sozinho não distingue: num condomínio há homônimos. A unidade atual é
    como a operação reconhece quem pediu, e por isso acompanha o nome na
    escolha. Os rótulos já chegam prontos — montá-los aqui, pessoa a pessoa,
    custaria uma consulta a cada opção desenhada.
    """

    def __init__(self, *args, rotulos=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.rotulos = rotulos or {}

    def label_from_instance(self, pessoa):
        return self.rotulos.get(pessoa.pk) or str(pessoa)


def _solicitantes_do_condominio(condominio, atual=None):
    """As pessoas elegíveis como solicitante, e como cada uma deve ser lida.

    Só quem está em uso — mais aquela que a demanda já aponta, para que um
    cadastro desativado depois continue legível sem voltar a ser oferecido.
    Duas consultas bastam: as pessoas e os vínculos vigentes com as unidades.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return Pessoa.objects.none(), {}

    pessoas = Pessoa.objects.do_condominio(condominio)
    if atual is not None and getattr(atual, "pk", None):
        pessoas = pessoas.filter(Q(ativa=True) | Q(pk=atual.pk))
    else:
        pessoas = pessoas.ativas()
    pessoas = pessoas.order_by("nome")

    unidades_por_pessoa: dict = {}
    vinculos = (
        VinculoPessoaUnidade.objects.do_condominio(condominio)
        .vigentes()
        .select_related("unidade")
    )
    for vinculo in vinculos:
        unidade = vinculo.unidade
        nome_da_unidade = " ".join(
            parte for parte in (unidade.bloco_torre, unidade.identificador) if parte
        )
        if nome_da_unidade:
            unidades_por_pessoa.setdefault(vinculo.pessoa_id, []).append(nome_da_unidade)

    rotulos = {}
    for pessoa in pessoas:
        unidades = unidades_por_pessoa.get(pessoa.pk, [])
        rotulos[pessoa.pk] = (
            f"{pessoa} — {', '.join(unidades)}" if unidades else str(pessoa)
        )
    return pessoas, rotulos


# As escolhas de classificação na tela. O vazio não é "outro": é "ainda não
# identificado", e é isso que a lista oferece quando ninguém sabe responder.
ESCOLHAS_DE_TIPO_DE_PROBLEMA = (("", "Não identificado"),) + tuple(TipoProblema.choices)
ESCOLHAS_DE_IMPACTO = (("", "Não avaliado"),) + tuple(ImpactoDemanda.choices)
ESCOLHAS_DE_RISCO = (("", "Não avaliado"),) + tuple(RiscoDemanda.choices)

# Sugestões para a próxima ação recorrente. Não é catálogo: o campo continua
# aceitando texto livre — a lista só evita redigitar o que se repete. Quem
# escolhe pela sugestão pode editá-la depois.
SUGESTOES_DE_PROXIMA_ACAO = (
    "Aguardar fornecedor",
    "Cobrar orçamento",
    "Cobrar fornecedor",
    "Retornar ao morador",
    "Executar serviço",
    "Aguardar material",
    "Encaminhar para manutenção",
)


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

    Nada aqui é obrigatório além do relato. O que a pessoa não escolher é lido
    do que ela escreveu — e, quando a leitura não sustenta nada, o padrão do
    domínio responde, sem preencher campo por conta própria.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    relato = forms.CharField(
        label="O que aconteceu?",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Escreva com as palavras do dia a dia o que aconteceu.",
            }
        ),
    )
    titulo = forms.CharField(
        label="Assunto",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Aparece a partir do relato",
                "autocomplete": "off",
            }
        ),
    )
    descricao = forms.CharField(
        label="Descrição",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    # O peso chega calculado pela regra do domínio; o campo existe para a
    # correção de quem tem permissão. Em branco, o motor responde.
    prioridade = forms.ChoiceField(
        label="Prioridade",
        choices=PrioridadeDemanda.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=User.objects.none(),
        required=False,
        empty_label="Ninguém ainda",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    # Quem pediu: o morador por trás da demanda. É escolha, não texto livre —
    # aponta para o cadastro da pessoa, e é ele que permite mandar o
    # acompanhamento por link. Escolher alguém faz a origem virar "Morador".
    solicitante = _SolicitanteDoCondominio(
        label="Quem pediu (morador)",
        queryset=Pessoa.objects.none(),
        required=False,
        empty_label="Não informado",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="O morador que pediu. Ao escolher alguém, a origem vira "
        "\"Morador\".",
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
    # O lugar é uma busca, não uma lista longa: quem escreve o nome de um local
    # do condomínio encontra o cadastro; quem escreve outro nome continua sendo
    # entendido, porque o texto fica guardado como o que se sabe do lugar.
    local_texto = forms.CharField(
        label="Onde aconteceu?",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Buscar ou digitar o lugar",
                "autocomplete": "off",
            }
        ),
    )
    equipamento = forms.ModelChoiceField(
        label="Equipamento",
        queryset=Equipamento.objects.none(),
        required=False,
        empty_label="Sem equipamento definido",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    tipo_problema = forms.ChoiceField(
        label="Tipo de problema",
        choices=ESCOLHAS_DE_TIPO_DE_PROBLEMA,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    # Impacto e risco descrevem a consequência, e por isso não são perguntados
    # de saída: aparecem quando o relato sustentou algo, e continuam corrigíveis
    # na edição. "Não avaliado" é a resposta honesta de quem não sabe.
    impacto = forms.ChoiceField(
        label="Impacto",
        choices=ESCOLHAS_DE_IMPACTO,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    risco = forms.ChoiceField(
        label="Risco",
        choices=ESCOLHAS_DE_RISCO,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, condominios=None, condominio=None, instancia=None, **kwargs):
        super().__init__(*args, **kwargs)

        # O que a leitura do relato sustentar neste envio. Começa vazio: sem
        # relato não há o que ter lido.
        self._leitura = Leitura()

        # A demanda que está sendo revista, quando é uma edição. Serve para o
        # histórico não contar a própria demanda como ocorrência dela mesma.
        self._instancia = instancia

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo_condominio = self.fields["condominio"]

        if condominio is not None:
            # Edição: a demanda pertence a este condomínio e não muda de casa.
            # Como a casa já está decidida aqui, o campo não volta a ser
            # perguntado nem exigido do navegador — a tela de edição não o
            # desenha, e cobrar o que não é perguntado recusaria toda edição.
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

        self.fields["responsavel"].queryset = usuarios_elegiveis_como_responsavel(
            self.condominio_efetivo
        )
        self.fields["local"].queryset = _locais_do_condominio(
            self.condominio_efetivo, self._local_atual()
        )
        self.fields["equipamento"].queryset = _equipamentos_do_condominio(
            self.condominio_efetivo, self._equipamento_atual()
        )
        self.fields["local_texto"].widget.attrs["list"] = (
            f"{self['local_texto'].id_for_label}_lista"
        )

        solicitantes, rotulos = _solicitantes_do_condominio(
            self.condominio_efetivo, self._solicitante_atual()
        )
        campo_solicitante = self.fields["solicitante"]
        campo_solicitante.queryset = solicitantes
        campo_solicitante.rotulos = rotulos

    def _local_atual(self):
        """O local que a demanda já aponta, quando o formulário é de edição.

        Serve só para manter o cadastro atual entre as opções, mesmo que tenha
        sido desativado depois. Não é validação.
        """
        local_id = (self.initial or {}).get("local")
        if not local_id:
            return None
        return LocalOperacional.objects.filter(pk=local_id).first()

    def _equipamento_atual(self):
        """O equipamento que a demanda já aponta, pela mesma razão do local."""
        equipamento_id = (self.initial or {}).get("equipamento")
        if not equipamento_id:
            return None
        return Equipamento.objects.filter(pk=equipamento_id).first()

    def _solicitante_atual(self):
        """O morador que a demanda já aponta, pela mesma razão do local."""
        solicitante_id = (self.initial or {}).get("solicitante")
        if not solicitante_id:
            return None
        return Pessoa.objects.filter(pk=solicitante_id).first()

    def _condominio_escolhido(self, permitidos):
        """Qual condomínio vale para montar a lista de responsáveis e de locais.

        Vale o que a pessoa enviou, desde que esteja entre os permitidos; senão,
        o que já se sabe do contexto e, na falta dele, o único elegível. Serve
        apenas para montar as opções — a validação continua sendo a do campo,
        contra a mesma lista.
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

    @property
    def leitura(self) -> Leitura:
        """O que a leitura do relato sustentou neste envio.

        Montada no ``clean()``, sobre o mesmo texto que fecha o assunto e o
        peso da demanda — é ela que sustenta o que a revisão mostra antes de
        registrar, com o motivo de cada proposta.
        """
        return self._leitura

    def responsavel_sugerido(self, usuario):
        """Quem responde pela demanda, quando o próprio contexto já responde.

        Quem registra costuma ser quem acompanha o que registrou — desde que
        tenha vínculo que o permita. Fora disso não há sugestão: apontar
        alguém sem base seria decidir no lugar de quem registra.
        """
        if usuario is None or not getattr(usuario, "pk", None):
            return None
        if self.fields["responsavel"].queryset.filter(pk=usuario.pk).exists():
            return usuario
        return None

    def clean_titulo(self):
        return _linha(self.cleaned_data["titulo"])

    def clean_descricao(self):
        return (self.cleaned_data["descricao"] or "").strip()

    def clean(self):
        """Fecha o que o relato já disse, sem pedir a mesma coisa duas vezes.

        O que a pessoa escolheu vale. O que ela não escolheu é lido do relato e,
        se a leitura não sustentar nada, fica com o padrão do domínio.
        """
        dados = super().clean()

        relato = _linha(dados.get("relato"))
        descricao = dados.get("descricao") or ""

        # O relato é a descrição: pedir os dois textos seria pedir duas vezes o
        # mesmo, e o que foi escrito por extenso é o que vale.
        if relato and not descricao:
            descricao = relato
            dados["descricao"] = descricao

        # Uma leitura só do que foi escrito: o assunto, o peso e o local saem
        # dela, e é ela que sustenta o que a revisão mostra. A leitura semântica
        # só é consultada no registro — na edição o que ela proporia não é
        # gravado, e perguntar custaria espera sem mudar nada.
        leitor = (
            derivacao.interpretar if self._instancia is not None else interpretacao.interpretar
        )
        self._leitura = leitor(
            descricao or relato,
            condominio=self.condominio_efetivo,
            ignorar=self._instancia,
        )

        assunto = dados.get("titulo") or self._leitura.assunto
        dados["titulo"] = assunto
        if not assunto:
            self.add_error(
                "relato", "Escreva o que aconteceu para registrar a demanda."
            )
            return dados

        # O peso chega preenchido pelo motor: quando quem registra não escolhe,
        # vale a prioridade que a regra estruturada sustentou — nunca o peso
        # antigo de uma demanda já existente, que uma edição parcial não deve
        # reclassificar. A escolha explícita é uma correção e prevalece: é ela
        # que entra no histórico antes e depois.
        if self.cleaned_data.get("prioridade"):
            dados["prioridade"] = self.cleaned_data["prioridade"]
        elif self._instancia is not None:
            dados["prioridade"] = self._instancia.prioridade
        else:
            dados["prioridade"] = self._leitura.prioridade

        # O prazo não é criado automaticamente: a regra de prazo continua
        # configurável, mas fora do fluxo de registro. O que a pessoa informar
        # na tela vale; sem informação, a demanda segue sem prazo.
        self._resolver_local(dados)
        self._classificar(dados)
        self._conciliar_local_e_equipamento(dados)
        return dados

    def _classificar(self, dados):
        """Guarda o que a leitura propôs, sem transformar sugestão em fato.

        ``classificacao_sugerida`` preserva o valor sugerido, a origem e a
        confiança de cada campo — é a memória que permitirá comparar sugestão,
        confirmação e correção mais adiante. Os campos da demanda recebem só o
        que a leitura sustentou: o que ficou em aberto continua sendo "não
        identificado", que não é o mesmo que "outro".

        A leitura só responde pelo campo que a tela não perguntou. Quando o
        navegador manda o campo — inclusive em branco —, o que voltou é a
        resposta de quem registra, e "sem equipamento" é uma resposta.
        """
        if self._instancia is not None:
            # A classificação nasce com a demanda. A edição estrutural corrige
            # o relato e os campos combinados; reclassificar fica para quando
            # houver como revisar o que o EITHRA propôs.
            return

        leitura = self._leitura
        if "equipamento" not in self.data:
            dados["equipamento"] = leitura.equipamento
        for campo in ("tipo_problema", "impacto", "risco"):
            if campo in self.data:
                continue
            sugestao = getattr(leitura, campo)
            dados[campo] = sugestao.valor if sugestao.existe else None
        dados["classificacao_sugerida"] = leitura.registro() or None

    def _conciliar_local_e_equipamento(self, dados):
        """Faz o local e o equipamento contarem a mesma história.

        O equipamento sabe onde fica: quando o relato não disse o lugar, o local
        vem dele — mesmo movimento do cadastro, que já guarda o equipamento
        dentro de um local. Quando os dois divergem, ninguém escolhe por conta
        própria: a tela recusa e diz o que encontrou, como já faz a manutenção.
        """
        equipamento = dados.get("equipamento")
        if equipamento is None or not equipamento.local_id:
            return

        local = dados.get("local")
        if local is None:
            dados["local"] = equipamento.local
            dados["local_texto"] = equipamento.local.nome
            dados["local_contexto"] = ""
            return

        if getattr(local, "pk", None) != equipamento.local_id:
            self.add_error(
                "equipamento",
                f"O equipamento escolhido fica em {equipamento.local.nome}. "
                "Escolha o mesmo local ou troque o equipamento.",
            )

    def _resolver_local(self, dados):
        """Transforma o lugar digitado no cadastro do condomínio, ou em texto.

        Nome que existe no cadastro vira vínculo; nome que não existe continua
        legível como o que se sabe do lugar. Sem o campo na tela, o texto legado
        da demanda antiga fica intacto.
        """
        if "local_texto" not in self.data:
            dados["local_contexto"] = None
            return

        texto = _linha(dados.get("local_texto"))
        if not texto:
            dados["local"] = None
            dados["local_contexto"] = ""
            return

        local = local_do_relato(texto, self.condominio_efetivo)
        dados["local"] = local
        dados["local_contexto"] = "" if local else texto



def acoes_de_status(permitidos, demanda=None):
    """As ações que esta demanda alcança agora, com o verbo que a tela mostra.

    O valor de cada ação é a situação que ela produz — e é por isso que a tela
    oferece o verbo, e não o nome do estado. A demanda encerrada não anda por
    movimento comum: a única ação é devolvê-la à operação. Nas demais, não se
    oferece a ação que levaria a demanda aonde ela já está.
    """
    alcancaveis = set(permitidos)
    if demanda is not None and getattr(demanda, "esta_encerrada", False):
        return tuple(acao for acao in (ACAO_DE_REABERTURA,) if acao[0] in alcancaveis)
    return tuple(
        (situacao, verbo)
        for situacao, verbo in ACOES_DE_STATUS
        if situacao in alcancaveis and situacao != getattr(demanda, "status", None)
    )


class AcaoDeStatusForm(forms.Form):
    """Registra a ação que move a demanda — nunca a situação escolhida.

    Cada verbo leva a uma situação: "Iniciar" põe a demanda em andamento,
    "Aguardar" a deixa dependendo de terceiro, "Concluir" a encerra, "Cancelar"
    a interrompe e "Reabrir" a devolve à operação. Quem registra diz o que
    aconteceu; a situação é consequência. O backend só aceita uma ação que a
    pessoa alcança — qualquer outra é recusada, venha de onde vier.
    """

    acao = forms.ChoiceField(
        label="Situação agora",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, permitidos=(), demanda=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["acao"].choices = acoes_de_status(permitidos, demanda)


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


class AcompanhamentoDaDemandaForm(forms.Form):
    """Registra o acompanhamento operacional — sem tocar na situação.

    Pergunta o que a operação precisa saber para agir: por quem a demanda
    espera, o próximo passo, a data prometida, o que a trava e quem a segue de
    perto. Nada aqui é situação — os mesmos campos convivem com qualquer um dos
    estados do ciclo, e salvá-los não move a demanda.

    Tudo é opcional e o formulário devolve o conjunto inteiro: limpar um campo é
    uma decisão, e o serviço do domínio registra a diferença entre o que havia e
    o que passou a haver. O que ninguém mudou não vira evento.
    """

    aguardando_tipo = forms.ChoiceField(
        label="Aguardando",
        choices=AguardandoTipo.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    aguardando_descricao = forms.CharField(
        label="O que se espera",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Quem ou o que exatamente (ex.: Atlas Elevadores)",
                "autocomplete": "off",
            }
        ),
    )
    proxima_acao = forms.CharField(
        label="Próxima ação",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "O próximo passo combinado",
                "autocomplete": "off",
            }
        ),
    )
    prazo_proxima_acao = forms.DateField(
        label="Prazo da próxima ação",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
        help_text="Até quando a próxima ação precisa acontecer. É a urgência da "
        "ação, não o prazo de solução da demanda.",
    )
    previsao = forms.DateField(
        label="Previsão",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
        help_text="A data prometida por quem está sendo aguardado. Não é o prazo "
        "da demanda e vencê-la não muda a situação.",
    )
    impedimento = forms.CharField(
        label="Impedimento",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "O que está travando a demanda, se houver",
                "autocomplete": "off",
            }
        ),
    )
    responsavel_acompanhamento = forms.ModelChoiceField(
        label="Quem acompanha",
        queryset=User.objects.none(),
        required=False,
        empty_label="Ninguém definido",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Quem segue a demanda de perto. É diferente de quem responde "
        "por ela.",
    )

    def __init__(self, *args, demanda=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.demanda = demanda
        self.fields["responsavel_acompanhamento"].queryset = (
            usuarios_elegiveis_como_responsavel(getattr(demanda, "condominio", None))
        )
        # A sugestão chega como ``<datalist>``: quem clica escolhe, quem escreve
        # outra coisa continua sendo aceito. O campo não vira uma escolha
        # fechada — a lista só evita redigitar o que se repete.
        self.fields["proxima_acao"].widget.attrs["list"] = (
            f"{self['proxima_acao'].id_for_label}_lista"
        )
        if demanda is not None and not self.is_bound:
            self.initial = {**self.initial, **self._iniciais_da_demanda(demanda)}

    @staticmethod
    def _iniciais_da_demanda(demanda) -> dict:
        """O que a janela já deve trazer quando é aberta para corrigir."""
        return {
            "aguardando_tipo": demanda.aguardando_tipo or "",
            "aguardando_descricao": demanda.aguardando_descricao,
            "proxima_acao": demanda.proxima_acao,
            "prazo_proxima_acao": demanda.prazo_proxima_acao,
            "previsao": demanda.previsao,
            "impedimento": demanda.impedimento,
            "responsavel_acompanhamento": demanda.responsavel_acompanhamento_id,
        }

    def mudancas(self) -> dict:
        """Os campos do acompanhamento prontos para o serviço oficial do domínio.

        Devolve o conjunto inteiro, e não só o que mudou: quem decidiu limpar um
        campo precisa que a limpeza chegue ao domínio, que é quem sabe comparar
        com o que havia e registrar apenas a diferença.
        """
        return {campo: self.cleaned_data.get(campo) for campo in CAMPOS_DO_ACOMPANHAMENTO}


class _VariosArquivosInput(forms.FileInput):
    """O campo de arquivo que aceita mais de um, como o navegador já oferece."""

    allow_multiple_selected = True


class _VariosArquivos(forms.FileField):
    """Aceita um ou vários arquivos sob a mesma chave, na ordem em que chegaram.

    O campo de arquivo do Django guarda um valor só. Quem escolhe vários manda a
    mesma chave repetida, e é isso que esta régua lê: o formulário passa a
    devolver a lista, em vez do último arquivo escolhido.
    """

    widget = _VariosArquivosInput

    def clean(self, data, initial=None):
        limpar_um = super().clean
        if isinstance(data, (list, tuple)):
            return [limpar_um(arquivo, initial) for arquivo in data]
        return limpar_um(data, initial)


class EvidenciaDaDemandaForm(forms.Form):
    """Recebe as fotos e os vídeos que sustentam o que se sabe da demanda.

    Uma atualização pode trazer mais de um arquivo, e a pergunta é a mesma para
    todos: o que prova o que foi relatado. Nada aqui decide o tipo real — ele é
    conferido pelo conteúdo, no momento de guardar; o ``accept`` do navegador é
    conveniência, não autorização.
    """

    evidencias = _VariosArquivos(
        label="Fotos ou vídeos",
        error_messages={"required": "Escolha ao menos uma foto ou um vídeo."},
        widget=_VariosArquivosInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        limites = limites_legiveis()
        self.fields["evidencias"].widget.attrs["accept"] = ACEITE_DE_EVIDENCIA
        self.fields["evidencias"].help_text = (
            f"{limites['formatos']}. Até {limites['foto']} por foto e "
            f"{limites['video']} por vídeo."
        )

    def arquivos_enviados(self) -> list:
        """Os arquivos escolhidos, na ordem em que chegaram."""
        arquivos = self.cleaned_data.get("evidencias") or []
        return [arquivo for arquivo in arquivos if arquivo]


class ConclusaoDaDemandaForm(forms.Form):
    """A conclusão em uma etapa: o que foi feito e, se houver, a prova.

    Concluir precisa ser simples: a demanda termina e o histórico registra. O
    que a tela pergunta é opcional — o relato do que foi feito e a prova que o
    sustenta —, e nada disso é obrigatório para encerrar. O mesmo formulário
    serve à conclusão pelo ícone e à conclusão em lote, onde só o texto é usado.
    """

    texto = forms.CharField(
        label="O que foi feito?",
        max_length=LIMITE_DA_OBSERVACAO,
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Opcional. Descreva o que foi feito para concluir esta demanda.",
            }
        ),
    )
    evidencias = _VariosArquivos(
        label="Evidência (opcional)",
        required=False,
        widget=_VariosArquivosInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        limites = limites_legiveis()
        self.fields["evidencias"].widget.attrs["accept"] = ACEITE_DE_EVIDENCIA
        self.fields["evidencias"].help_text = (
            f"{limites['formatos']}. Até {limites['foto']} por foto e "
            f"{limites['video']} por vídeo."
        )

    def clean_texto(self):
        """Normaliza os espaços como o resto do domínio; vazio continua vazio."""
        return " ".join((self.cleaned_data.get("texto") or "").split())

    def arquivos_enviados(self) -> list:
        """As provas escolhidas, na ordem em que chegaram."""
        arquivos = self.cleaned_data.get("evidencias") or []
        return [arquivo for arquivo in arquivos if arquivo]


class FiltroDeDemandasForm(forms.Form):
    """Filtros essenciais da lista. Nunca obrigatórios: filtrar é opcional.

    Os primeiros recortam o que a operação acompanha — situação, peso, lugar,
    quem responde. Os de classificação vêm depois porque são a lente fina: tipo
    de problema, consequência e histórico só interessam quando já se sabe o que
    se procura.
    """

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
    tipo_problema = forms.ChoiceField(
        label="Tipo de problema",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    impacto = forms.ChoiceField(
        label="Impacto",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    risco = forms.ChoiceField(
        label="Risco",
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
        label="Pesquisar demandas",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Pesquisar demandas...",
                "autocomplete": "off",
            }
        ),
    )
    minhas = forms.BooleanField(
        label="Só as minhas",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    # O que chegou sem lugar: é por onde o prédio aponta as demandas que ainda
    # não têm nível, e onde a gestão as distribui. Mora aqui, e não num filtro
    # próprio, porque é o mesmo recorte — a lista já sabe mostrá-lo.
    sem_local = forms.BooleanField(
        label="Sem local",
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
        self.fields["local"].queryset = _locais_entre(condominios)
        self.fields["equipamento"].queryset = _equipamentos_entre(condominios)
        self.fields["tipo_problema"].choices = [
            ("", "Todos os tipos de problema")
        ] + list(TipoProblema.choices)
        self.fields["impacto"].choices = [("", "Qualquer impacto")] + list(
            ImpactoDemanda.choices
        )
        self.fields["risco"].choices = [("", "Qualquer risco")] + list(
            RiscoDemanda.choices
        )
        self.fields["responsavel"].queryset = (
            responsaveis if responsaveis is not None else User.objects.none()
        )


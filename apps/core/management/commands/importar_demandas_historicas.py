"""Importa as demandas que aconteceram antes do Eithra, a partir do Gmail.

A caixa da administração tem anos de conversa com fornecedores e entre a
gestão. Esta é a rodada que relê esse acervo uma vez e devolve ao sistema as
demandas que já existiram — conservadora no que aceita, auditável no que grava
e repetível sem duplicar.

O que a rodada faz, na ordem:

1. lê a caixa pelo mesmo caminho da sincronização, respeitando filtro e limite;
2. registra cada mensagem como item da carga histórica — mensagem já registrada
   não vira item novo, e o item preserva a autoria de quem o criou;
3. entrega as mensagens à carga histórica, que agrupa, decide e registra;
4. relata o que foi feito, sem corpo de e-mail e sem detalhe técnico.

Nada é gravado sem quem responda pela carga: a execução definitiva exige
``--responsavel``, e a simulação (``--dry-run``) lê e decide sem escrever nada —
nem lote, nem item, nem demanda.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.condominios.models import Condominio
from apps.core import demandas_historicas, gmail
from apps.core.constants import OrigemImportacaoHistorica
from apps.core.demandas_historicas import (
    CargaSemResponsavel,
    EntradaDaCarga,
    ErroDaCargaHistorica,
    ResumoDaCarga,
)
from apps.core.gmail import (
    GmailCredencialInvalida,
    GmailIndisponivel,
    GmailMensagemInexistente,
    GmailNaoConfigurado,
    GmailRespostaInvalida,
    obter_provedor,
)
from apps.core.management.commands import sincronizar_gmail as leitura
from apps.core.models import ItemImportacaoHistorica, LoteImportacaoHistorica
from apps.core.texto import normalizar_endereco_email

LIMITE_PADRAO = leitura.LIMITE_PADRAO

# Esta carga pertence a uma caixa de negócio, e a uma só: a caixa de Demandas
# da administração. A caixa da Portaria é outra coisa — e não entra por aqui.
CONTA_DEMANDAS = "fernandaduccini.rve@gmail.com"


@dataclass
class ResumoDaImportacao:
    """O que a rodada fez, para ser relatado no fim."""

    condominio: Condominio
    conta: str = ""
    consulta: str = ""
    limite: int = LIMITE_PADRAO
    lote: LoteImportacaoHistorica | None = None
    simulado: bool = False
    mensagens: int = 0
    itens: int = 0
    novas: int = 0
    existentes: int = 0
    carga: ResumoDaCarga | None = None
    falhas: list[tuple[str, str]] = field(default_factory=list)
    truncado: bool = False

    @property
    def grupos(self) -> int:
        return len(self.carga.grupos) if self.carga is not None else 0

    @property
    def criadas(self) -> int:
        return len(self.carga.criadas) if self.carga is not None else 0

    @property
    def relacionadas(self) -> int:
        return len(self.carga.relacionadas) if self.carga is not None else 0

    @property
    def ignoradas(self) -> int:
        return len(self.carga.ignoradas) if self.carga is not None else 0

    @property
    def decisoes(self) -> tuple:
        """Os grupos com destino registrado ou relacionado, para o relato."""
        if self.carga is None:
            return ()
        return self.carga.criadas + self.carga.relacionadas


class Command(BaseCommand):
    """Lê o Gmail e devolve as demandas que as mensagens contam."""

    help = "Importa demandas históricas a partir das mensagens do Gmail."

    def add_arguments(self, parser):
        parser.add_argument(
            "--condominio",
            required=True,
            help="Identificador do condomínio ao qual a execução pertence.",
        )
        parser.add_argument(
            "--responsavel",
            default="",
            help=(
                "E-mail de quem responde pela carga, e assina o que for criado. "
                "Obrigatório para gravar; na simulação não é exigido."
            ),
        )
        parser.add_argument(
            "--consulta",
            default="",
            help="Busca do Gmail (ex.: 'in:inbox newer_than:7d'). Opcional.",
        )
        parser.add_argument(
            "--desde",
            default="",
            help="Só mensagens a partir desta data, no formato AAAA-MM-DD. Opcional.",
        )
        parser.add_argument(
            "--limite",
            type=int,
            default=None,
            help=(
                "Quantas mensagens ler nesta execução. "
                "Sem consulta e sem --desde, o limite passa a ser obrigatório. "
                f"Quando houver filtro, o padrão é {LIMITE_PADRAO}."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Só lê e decide: não cria lote, não registra item, não grava "
                "demanda e não exige responsável."
            ),
        )

    def handle(self, *args, **opcoes):
        if not gmail.configurado():
            raise CommandError(NAO_CONFIGURADO)
        self._conferir_conta()

        simulado = opcoes["dry_run"]
        condominio = leitura.Command._resolver_condominio(opcoes["condominio"])
        consulta = leitura._consulta_efetiva(opcoes["consulta"], opcoes["desde"])
        limite = leitura.Command._resolver_limite(opcoes["limite"], consulta=consulta)
        usuario = None
        if not simulado:
            usuario = self._resolver_responsavel(opcoes["responsavel"], condominio)

        resumo = self._importar(
            condominio=condominio,
            consulta=consulta,
            limite=limite,
            usuario=usuario,
            simulado=simulado,
        )
        self._relatar(resumo)

    @staticmethod
    def _importar(*, condominio, consulta, limite, usuario, simulado) -> ResumoDaImportacao:
        """Roda a importação traduzindo falha do provider em falha de comando.

        Gmail não configurado, credencial recusada e indisponibilidade são
        falhas do conjunto, não de uma mensagem: interrompem a execução com uma
        mensagem curta, em vez de subir um erro cru da camada de rede.
        """
        try:
            return importar_do_gmail(
                condominio=condominio,
                provedor=obter_provedor(),
                usuario=usuario,
                consulta=consulta,
                limite=limite,
                simulado=simulado,
            )
        except GmailNaoConfigurado:
            raise CommandError(NAO_CONFIGURADO)
        except GmailCredencialInvalida:
            raise CommandError(
                "A credencial do Gmail foi recusada. Revise o refresh token antes "
                "de importar."
            )
        except GmailIndisponivel:
            raise CommandError(
                "O Gmail não respondeu. Nada foi gravado; tente de novo mais tarde."
            )
        except GmailRespostaInvalida:
            raise CommandError("O Gmail respondeu de um jeito que não foi possível ler.")
        except (CargaSemResponsavel, ErroDaCargaHistorica) as erro:
            raise CommandError(f"{erro} Nada foi gravado.")

    @staticmethod
    def _conferir_conta() -> None:
        """Recusa a rodada quando a conta declarada não é a caixa de Demandas.

        A trava é de negócio, não de formato: compara os endereços já
        normalizados, sem depender de maiúsculas, espaços ou nome de exibição.
        A caixa da Portaria nunca alimenta esta carga, e a recusa é anterior a
        qualquer leitura — nenhum lote, item, demanda ou vínculo é criado.
        """
        declarada = normalizar_endereco_email(leitura._conta_declarada())
        if declarada != normalizar_endereco_email(CONTA_DEMANDAS):
            raise CommandError(EXCLUSIVO_DA_CAIXA_DE_DEMANDAS)

    @staticmethod
    def _resolver_responsavel(valor, condominio):
        """Resolve quem responde pela carga — por e-mail, como no resto do sistema."""
        endereco = normalizar_endereco_email(valor)
        if not endereco:
            raise CommandError(
                "Informe quem responde por esta carga: --responsavel <e-mail>. "
                "Sem isso nada é gravado."
            )

        usuario = get_user_model().objects.filter(email__iexact=endereco).first()
        if usuario is None:
            raise CommandError(
                "Não há pessoa cadastrada com este e-mail para responder pela carga."
            )

        try:
            demandas_historicas.exigir_responsavel(usuario, condominio)
        except CargaSemResponsavel as erro:
            raise CommandError(str(erro))
        return usuario

    def _relatar(self, resumo: ResumoDaImportacao) -> None:
        """Mostra o resultado da rodada, só com o que é operacional.

        Nada de corpo, token, segredo ou conteúdo de anexo aparece aqui.
        """
        escrever = self.stdout.write
        titulo = "Importação de demandas históricas"
        if resumo.simulado:
            titulo += " (simulação — nada foi gravado)"
        escrever(titulo)
        escrever(f"Condomínio: {resumo.condominio.nome} ({resumo.condominio.pk})")
        escrever(f"Conta: {resumo.conta or 'não declarada'}")
        escrever(f"Filtro: {resumo.consulta or 'nenhum'}")
        escrever(f"Limite: {resumo.limite}")
        if resumo.simulado:
            escrever("Lote: não criado (simulação)")
        else:
            escrever(f"Lote: {resumo.lote.pk if resumo.lote else 'não criado'}")
        escrever(f"Mensagens na listagem: {resumo.mensagens}")
        escrever(f"Lidas e registradas: {resumo.itens}")
        escrever(f"Itens novos: {resumo.novas}")
        escrever(f"Itens já existentes: {resumo.existentes}")
        escrever(f"Conversas reunidas: {resumo.grupos}")
        escrever(f"Demandas registradas: {resumo.criadas}")
        escrever(f"Demandas relacionadas: {resumo.relacionadas}")
        escrever(f"Sem registro: {resumo.ignoradas}")
        for resultado in resumo.decisoes:
            escrever(f"  - {resultado.titulo or resultado.chave}")
        escrever(f"Falhas: {len(resumo.falhas)}")
        for identificador, motivo in resumo.falhas:
            escrever(f"  - {identificador}: {motivo}")
        if resumo.truncado:
            escrever(
                "Havia mais mensagens do que o limite desta execução; "
                "rode de novo com um filtro mais específico ou um limite maior."
            )


NAO_CONFIGURADO = (
    "O acesso ao Gmail não está configurado neste ambiente. "
    "Defina as variáveis EITHRA_GMAIL_* antes de importar."
)

EXCLUSIVO_DA_CAIXA_DE_DEMANDAS = (
    "Esta carga é exclusiva da caixa de Demandas "
    f"({CONTA_DEMANDAS}), e nada foi lido nem gravado. "
    "A caixa da Portaria não alimenta este comando."
)


def importar_do_gmail(
    *,
    condominio: Condominio,
    provedor,
    usuario=None,
    consulta: str = "",
    limite: int = LIMITE_PADRAO,
    simulado: bool = False,
) -> ResumoDaImportacao:
    """Lê a caixa, registra as mensagens e importa as demandas que elas contam.

    A leitura da caixa é a mesma da sincronização — listagem, lote e identidade
    do item vêm de lá, para não existirem duas versões da mesma regra. O que
    muda é o que se faz depois: a mensagem é lida e a carga decide o destino
    dela.

    O provedor é recebido de fora para que a rodada seja testável sem rede.
    """
    conta = leitura._conta_declarada()
    resumo = ResumoDaImportacao(
        condominio=condominio,
        conta=conta,
        consulta=consulta,
        limite=limite,
        simulado=simulado,
    )

    # A listagem vem antes do lote: se a credencial estiver inválida ou o Gmail
    # não responder, a execução termina aqui e nenhum lote é deixado para trás.
    referencias, resumo.truncado = leitura._listar(
        provedor, consulta=consulta, limite=limite
    )
    resumo.mensagens = len(referencias)

    if not simulado:
        resumo.lote = LoteImportacaoHistorica.objects.create(
            condominio=condominio,
            origem=OrigemImportacaoHistorica.GMAIL,
            conta_origem=conta,
            observacao=leitura._contexto_da_execucao(consulta=consulta, limite=limite),
        )

    entradas = []
    for referencia in referencias:
        try:
            mensagem = provedor.obter_mensagem(referencia.message_id)
        except (GmailMensagemInexistente, GmailRespostaInvalida) as erro:
            # A mensagem específica não pôde ser lida: registra e segue. As
            # demais não têm por que parar por causa dela.
            resumo.falhas.append((referencia.message_id, type(erro).__name__))
            continue

        if simulado:
            # Em simulação nada é gravado: a mensagem entra na decisão sem
            # virar item da fonte.
            entradas.append(EntradaDaCarga(mensagem=mensagem))
            continue

        try:
            item, criado = ItemImportacaoHistorica.objects.registrar_item(
                lote=resumo.lote,
                identificador_externo=mensagem.message_id,
                message_id=mensagem.message_id,
                thread_id=mensagem.thread_id,
                remetente=mensagem.remetente,
                assunto=mensagem.assunto,
                momento_da_mensagem=mensagem.momento,
                hash_fonte=leitura._identidade_da_mensagem(mensagem),
            )
        except ValidationError:
            # A mensagem chegou, mas não serve como item da fonte.
            resumo.falhas.append((referencia.message_id, "ValidationError"))
            continue

        resumo.itens += 1
        if criado:
            resumo.novas += 1
        else:
            resumo.existentes += 1
        entradas.append(EntradaDaCarga(mensagem=mensagem, item=item))

    resumo.carga = demandas_historicas.importar(
        entradas,
        condominio=condominio,
        usuario=usuario,
        simulado=simulado,
    )
    resumo.falhas.extend(resumo.carga.falhas)
    return resumo

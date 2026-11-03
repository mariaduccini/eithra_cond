"""Gera as manutenções preventivas que já venceram.

Este comando é a casca fina do gerador periódico: ele lê os parâmetros, confia no
serviço de domínio e relata o que aconteceu. Nenhuma regra de recorrência,
idempotência ou autoria mora aqui — tudo isso pertence ao serviço, para que a
mesma geração valha quando o agendamento automático chegar.

Ele existe para ser rodado à mão, com data declarada, enquanto o agendador real
não faz parte do piloto. Rodar duas vezes para a mesma data não duplica nada:
quem garante isso é o serviço oficial, e não uma checagem feita aqui.

Quem assina a geração também pode ser declarado. Numa execução manual, quem
opera informa a pessoa com ``--usuario``; numa execução automática, ninguém
informa nada e quem assina é o ator técnico configurado no ambiente. Sem ator
declarado — ou com um que não existe — a rodada não acontece: a autoria não é
atribuída a qualquer pessoa por adivinhação.
"""
from datetime import datetime
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.condominios.models import Condominio
from apps.core.automacao import ErroDeAutomacao
from apps.manutencoes.services import gerar_preventivas_pendentes


class Command(BaseCommand):
    help = "Gera as manutenções preventivas pendentes dos planos ativos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--data",
            default="",
            help=(
                "Data de referência, no formato AAAA-MM-DD. Serve para recuperar "
                "uma ausência longa. Sem ela, vale o dia de hoje."
            ),
        )
        parser.add_argument(
            "--condominio",
            default="",
            help="Gera só os planos deste condomínio (identificador). Opcional.",
        )
        parser.add_argument(
            "--usuario",
            default="",
            help=(
                "E-mail de quem responde pela geração, quando a execução é "
                "manual. Fica registrado como autor no histórico de cada "
                "ocorrência criada. Sem ele, a geração é assinada pelo ator "
                "automático configurado."
            ),
        )

    def handle(self, *args, **opcoes):
        usuario = self._resolver_usuario(opcoes["usuario"])
        data_referencia = self._resolver_data(opcoes["data"])
        condominio = self._resolver_condominio(opcoes["condominio"])

        try:
            resumo = gerar_preventivas_pendentes(
                usuario=usuario,
                data_referencia=data_referencia,
                condominio=condominio,
            )
        except ErroDeAutomacao as erro:
            # Não há quem assine: nada é gerado, e o operador precisa saber por
            # quê antes de tentar de novo.
            raise CommandError(str(erro))

        self._relatar(resumo, condominio=condominio)

    @staticmethod
    def _resolver_usuario(valor):
        """Quem assina a geração, quando quem opera declara a pessoa.

        Sem declaração, devolve ``None``: quem resolve o ator automático é o
        serviço, para que a mesma regra valha quando a execução vier do
        agendador, e não de uma pessoa digitando um comando.
        """
        email = (valor or "").strip()
        if not email:
            return None

        modelo = get_user_model()
        usuario = modelo.objects.filter(**{modelo.USERNAME_FIELD: email}).first()
        if usuario is None:
            raise CommandError("Não encontrei uma pessoa com esse e-mail.")
        return usuario

    @staticmethod
    def _resolver_data(valor):
        """A data de referência declarada, ou o dia de hoje quando não houver."""
        if not (valor or "").strip():
            return None
        try:
            return datetime.strptime(valor.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise CommandError("--data deve estar no formato AAAA-MM-DD.")

    @staticmethod
    def _resolver_condominio(valor):
        """O condomínio do escopo, quando informado — nunca inferido."""
        if not (valor or "").strip():
            return None
        try:
            chave = UUID(valor.strip())
        except (ValueError, AttributeError, TypeError):
            raise CommandError(
                "Informe o condomínio pelo identificador dele (--condominio)."
            )
        condominio = Condominio.objects.filter(pk=chave).first()
        if condominio is None:
            raise CommandError("O condomínio informado não foi encontrado.")
        return condominio

    def _relatar(self, resumo, *, condominio=None) -> None:
        """Mostra o resultado da rodada, só com o que é operacional."""
        escrever = self.stdout.write
        escrever("Geração de preventivas")
        escrever(f"Data de referência: {resumo.data_referencia.strftime('%d/%m/%Y')}")
        escrever(
            "Escopo: "
            + (
                f"condomínio {condominio.nome}"
                if condominio is not None
                else "todos os condomínios"
            )
        )
        escrever(f"Planos verificados: {resumo.planos_verificados}")
        escrever(f"Planos com ocorrência gerada: {resumo.planos_processados}")
        escrever(f"Planos sem necessidade: {resumo.planos_sem_necessidade}")
        escrever(f"Ocorrências geradas: {resumo.ocorrencias_geradas}")
        escrever(
            "Competências já existentes reconhecidas: "
            f"{resumo.competencias_reconciliadas}"
        )
        escrever(f"Planos que pararam no limite: {len(resumo.planos_limitados)}")
        for identificador in resumo.planos_limitados:
            escrever(f"  - {identificador}")
        escrever(f"Falhas: {len(resumo.falhas)}")
        for identificador, motivo in resumo.falhas:
            escrever(f"  - {identificador}: {motivo}")

"""Deixa a biblioteca de modelos de conferência do condomínio piloto pronta.

O piloto precisa dos roteiros reutilizáveis dos serviços que já acontecem no
condomínio, e cadastrá-los à mão, um a um, é lento e fácil de errar. Este comando
registra a lista inicial de modelos e de seus critérios como **dados**, pela
mesma estrutura que a tela da Administradora usa — sem código novo, sem migration
de dados e sem fixture.

Nenhum critério aqui afirma norma técnica, tolerância, medição, parâmetro de
fabricante ou obrigação contratual. São itens observáveis, operacionais e
seguros: o que a conferência consegue responder olhando a entrega.

Rodar duas vezes não duplica nem sobrescreve. O modelo que já existe é deixado
como está, com os critérios que tiver — inclusive os que a Administradora tenha
ajustado depois. Atualizar um roteiro é outro ato, feito de propósito pela tela.

A autoria é da Administradora ativa do condomínio, resolvida pelo mecanismo que
já existe. Sem exatamente uma Administradora válida — nenhuma ou mais de uma —, o
comando para: ninguém é escolhido por adivinhação, e administração do sistema não
substitui a função condominial.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.condominios.gestao import ErroDeGestao, administradora_do_condominio
from apps.condominios.models import Condominio
from apps.manutencoes.models import ModeloConferenciaPreventiva
from apps.manutencoes.services import ErroDeManutencao, criar_modelo_conferencia

# O condomínio piloto, pelo nome com que está cadastrado. É a identidade estável
# que o banco oferece; nenhum identificador técnico é fixado aqui.
CONDOMINIO_DO_PILOTO = "Rio Vermelho Exclusive"

# Os nove serviços iniciais da biblioteca, cada um com os itens que a conferência
# observa. Todos os critérios nascem obrigatórios, e a ordem da lista é a ordem
# do roteiro na tela.
MODELOS_DO_PILOTO = (
    (
        "Elevador",
        (
            "Serviço previsto foi executado",
            "Equipamento ficou disponível para uso após o atendimento",
            "Não há anomalia visível ou sinal evidente de falha após o serviço",
            "Área de trabalho foi deixada limpa e organizada",
            "Evidências previstas foram anexadas",
            "Pendências ou recomendações do prestador foram registradas",
        ),
    ),
    (
        "Gerador",
        (
            "Serviço previsto foi executado",
            "Equipamento foi deixado em condição operacional após o atendimento",
            "Não há vazamento ou anomalia visível após o serviço",
            "Área de trabalho foi deixada limpa e organizada",
            "Evidências previstas foram anexadas",
            "Pendências ou recomendações do prestador foram registradas",
        ),
    ),
    (
        "Bombas",
        (
            "Serviço previsto foi executado",
            "Equipamento foi deixado em condição operacional após o atendimento",
            "Não há vazamento ou anomalia visível após o serviço",
            "Área de trabalho foi deixada limpa e organizada",
            "Evidências previstas foram anexadas",
            "Pendências ou recomendações do prestador foram registradas",
        ),
    ),
    (
        "Piscina",
        (
            "Serviço previsto foi executado",
            "Área foi deixada limpa e organizada",
            "Não há anomalia visível nos equipamentos ou na área atendida",
            "Materiais e resíduos do serviço foram removidos",
            "Evidências previstas foram anexadas",
            "Pendências ou recomendações foram registradas",
        ),
    ),
    (
        "Portão",
        (
            "Serviço previsto foi executado",
            "Portão ficou funcional após o atendimento",
            "Não há anomalia visível após o serviço",
            "Área de trabalho foi deixada segura e organizada",
            "Evidências previstas foram anexadas",
            "Pendências ou recomendações do prestador foram registradas",
        ),
    ),
    (
        "Reservatórios",
        (
            "Serviço previsto foi executado",
            "Área foi deixada limpa e organizada",
            "Acesso ao reservatório foi deixado em condição adequada após o serviço",
            "Não há anomalia visível na área atendida",
            "Evidências previstas foram anexadas",
            "Pendências ou recomendações foram registradas",
        ),
    ),
    (
        "Dedetização",
        (
            "Serviço previsto foi executado",
            "Áreas previstas foram atendidas",
            "Área foi deixada organizada após o serviço",
            "Orientações deixadas pelo prestador foram registradas",
            "Evidências previstas foram anexadas",
            "Pendências foram registradas",
        ),
    ),
    (
        "Sistema de incêndio",
        (
            "Serviço previsto foi executado",
            "Itens previstos no atendimento foram verificados pelo prestador",
            "Não há anomalia visível deixada sem registro",
            "Área de trabalho foi deixada organizada",
            "Evidências previstas foram anexadas",
            "Pendências ou recomendações do prestador foram registradas",
        ),
    ),
    (
        "Elétrica",
        (
            "Serviço previsto foi executado",
            "Área atendida foi deixada em condição segura e organizada",
            "Não há anomalia visível deixada sem registro",
            "Equipamento ou ponto atendido ficou disponível conforme previsto",
            "Evidências previstas foram anexadas",
            "Pendências ou recomendações do prestador foram registradas",
        ),
    ),
)


class Command(BaseCommand):
    """Cadastra os modelos de conferência iniciais do condomínio piloto."""

    help = (
        "Cadastra os modelos de conferência do condomínio piloto. Rodar de novo "
        "não duplica nem sobrescreve o que já existe."
    )

    def handle(self, *args, **opcoes):
        condominio = self._resolver_condominio()
        administradora = self._resolver_administradora(condominio)

        criados = []
        existentes = []
        criterios_criados = 0

        with transaction.atomic():
            for nome, criterios in MODELOS_DO_PILOTO:
                modelo, criados_agora = self._garantir_modelo(
                    condominio=condominio,
                    administradora=administradora,
                    nome=nome,
                    criterios=criterios,
                )
                if criados_agora is None:
                    existentes.append(modelo.nome)
                else:
                    criados.append(modelo.nome)
                    criterios_criados += criados_agora

        self._relatar(
            condominio=condominio,
            administradora=administradora,
            criados=criados,
            existentes=existentes,
            criterios_criados=criterios_criados,
        )

    @staticmethod
    def _resolver_condominio():
        """O condomínio piloto, sem escolher por conta própria.

        Havendo mais de um cadastro com este nome, o comando para e mostra os
        candidatos, em vez de adivinhar qual é o piloto.
        """
        candidatos = list(
            Condominio.objects.filter(nome__iexact=CONDOMINIO_DO_PILOTO).order_by(
                "criado_em"
            )
        )

        if not candidatos:
            raise CommandError(
                f"Não há condomínio cadastrado com o nome «{CONDOMINIO_DO_PILOTO}». "
                "Cadastre o condomínio piloto antes de popular a biblioteca."
            )

        if len(candidatos) > 1:
            raise CommandError(
                f"Há mais de um condomínio cadastrado com o nome "
                f"«{CONDOMINIO_DO_PILOTO}». Regularize o cadastro antes de "
                "continuar: a biblioteca precisa de um condomínio só."
            )

        return candidatos[0]

    @staticmethod
    def _resolver_administradora(condominio):
        """Quem assina os modelos: a Administradora ativa do condomínio.

        A resolução é a que já existe no domínio, e ela recusa tanto a ausência
        quanto a ambiguidade. Não há substituto: administração do sistema,
        síndica e supervisão não assinam esta carga.
        """
        try:
            return administradora_do_condominio(condominio)
        except ErroDeGestao as erro:
            raise CommandError(str(erro))

    @staticmethod
    def _garantir_modelo(*, condominio, administradora, nome, criterios):
        """Devolve o modelo, criando-o quando ainda não existe.

        O que já existe é devolvido sem toque nenhum — nem critérios, nem
        situação —, e a segunda resposta diz se ele nasceu nesta execução
        (quantos critérios foram criados) ou se já estava lá (``None``).
        """
        existente = ModeloConferenciaPreventiva.objects.filter(
            condominio=condominio, nome__iexact=nome
        ).first()
        if existente is not None:
            return existente, None

        try:
            modelo = criar_modelo_conferencia(
                condominio=condominio,
                criado_por=administradora,
                nome=nome,
                criterios=[
                    {"descricao": descricao, "ordem": ordem, "obrigatorio": True}
                    for ordem, descricao in enumerate(criterios)
                ],
            )
        except ErroDeManutencao as erro:
            raise CommandError(
                f"Não foi possível registrar o modelo «{nome}»: {erro}"
            )

        return modelo, len(criterios)

    def _relatar(
        self, *, condominio, administradora, criados, existentes, criterios_criados
    ) -> None:
        """Mostra o resultado da carga, só com o que é operacional."""
        escrever = self.stdout.write
        escrever("Modelos de conferência do piloto")
        escrever(f"Condomínio: {condominio.nome}")
        escrever(f"Administradora: {self._nome(administradora)}")
        escrever(f"Modelos criados: {len(criados)}")
        for nome in criados:
            escrever(f"  - {nome}")
        escrever(f"Modelos já existentes: {len(existentes)}")
        for nome in existentes:
            escrever(f"  - {nome} (já existente)")
        escrever(f"Critérios criados: {criterios_criados}")

    @staticmethod
    def _nome(usuario) -> str:
        """O nome pelo qual a pessoa é reconhecida, sem identificador técnico."""
        return (
            getattr(usuario, "full_name", "")
            or getattr(usuario, "short_name", "")
            or getattr(usuario, "email", "")
            or "sem nome"
        )

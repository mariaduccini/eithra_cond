"""A carga inicial da biblioteca de conferência do condomínio piloto.

O comando deixa o piloto pronto para uso: nove roteiros reutilizáveis, cada um
com os itens que a conferência observa. Aqui se prova que eles nascem certos —
do condomínio piloto, ativos, com os critérios do próprio modelo e todos
obrigatórios —, que rodar de novo não duplica nem sobrescreve o que a
Administradora ajustou depois, e que a carga se recusa a acontecer quando não há
um condomínio só e uma Administradora válida para assinar.

Nada aqui é critério técnico: os itens são dados, e nenhum deles afirma norma,
medição ou obrigação contratual.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio
from apps.manutencoes.management.commands.popular_modelos_conferencia_piloto import (
    CONDOMINIO_DO_PILOTO,
    MODELOS_DO_PILOTO,
)
from apps.manutencoes.models import (
    CriterioModeloConferenciaPreventiva,
    ModeloConferenciaPreventiva,
)
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)

COMANDO = "popular_modelos_conferencia_piloto"

# Os nove serviços iniciais, como a biblioteca do piloto deve ficar.
NOMES_ESPERADOS = (
    "Elevador",
    "Gerador",
    "Bombas",
    "Piscina",
    "Portão",
    "Reservatórios",
    "Dedetização",
    "Sistema de incêndio",
    "Elétrica",
)

CRITERIOS_POR_MODELO = 6


def _rodar(*args):
    """Roda o comando e devolve o que ele relatou."""
    saida = StringIO()
    call_command(COMANDO, *args, stdout=saida)
    return saida.getvalue()


class BaseDaCarga(TestCase):
    """O condomínio piloto cadastrado, com quem responde por ele."""

    def setUp(self):
        self.condominio = criar_condominio(CONDOMINIO_DO_PILOTO)
        self.administradora = criar_pessoa("adm@exemplo.test", "Administradora")
        criar_vinculo(
            self.administradora, self.condominio, papel=Papel.ADMINISTRADORA
        )

    def _modelos(self, condominio=None):
        return ModeloConferenciaPreventiva.objects.filter(
            condominio=condominio or self.condominio
        )

    def _criterios(self, modelo):
        return list(modelo.criterios.order_by("ordem"))


class CargaInicialTests(BaseDaCarga):
    """A primeira execução cria os nove modelos, ativos e completos."""

    def test_cria_os_nove_modelos(self):
        _rodar()

        nomes = set(self._modelos().values_list("nome", flat=True))
        self.assertEqual(nomes, set(NOMES_ESPERADOS))
        self.assertEqual(self._modelos().count(), 9)

    def test_modelos_pertencem_ao_condominio_piloto(self):
        _rodar()

        for modelo in self._modelos():
            self.assertEqual(modelo.condominio, self.condominio)

    def test_todos_ficam_ativos(self):
        _rodar()

        inativos = self._modelos().filter(ativo=False)
        self.assertEqual(inativos.count(), 0)

    def test_criterios_correspondem_ao_modelo_correto(self):
        _rodar()

        esperado = dict(MODELOS_DO_PILOTO)
        for modelo in self._modelos():
            descricoes = [criterio.descricao for criterio in self._criterios(modelo)]
            self.assertEqual(descricoes, list(esperado[modelo.nome]))
            self.assertEqual(len(descricoes), CRITERIOS_POR_MODELO)

    def test_um_item_proprio_nao_vaza_para_outro_modelo(self):
        """Itens parecidos entre modelos não se confundem na carga."""
        _rodar()

        portao = self._modelos().get(nome="Portão")
        descricoes_do_portao = [
            criterio.descricao for criterio in self._criterios(portao)
        ]
        self.assertIn("Portão ficou funcional após o atendimento", descricoes_do_portao)

        piscina = self._modelos().get(nome="Piscina")
        descricoes_da_piscina = [
            criterio.descricao for criterio in self._criterios(piscina)
        ]
        self.assertNotIn(
            "Portão ficou funcional após o atendimento", descricoes_da_piscina
        )
        # A piscina encerra as pendências sem citar o prestador, diferente de
        # outros modelos — o roteiro é o que foi cadastrado, item por item.
        self.assertIn(
            "Pendências ou recomendações foram registradas", descricoes_da_piscina
        )
        self.assertNotIn(
            "Pendências ou recomendações do prestador foram registradas",
            descricoes_da_piscina,
        )

    def test_todos_os_criterios_sao_obrigatorios(self):
        _rodar()

        criterios = CriterioModeloConferenciaPreventiva.objects.filter(
            modelo__condominio=self.condominio
        )
        self.assertEqual(criterios.count(), 9 * CRITERIOS_POR_MODELO)
        self.assertEqual(criterios.filter(obrigatorio=False).count(), 0)

    def test_administradora_assina_os_modelos(self):
        _rodar()

        autores = set(self._modelos().values_list("criado_por", flat=True))
        self.assertEqual(autores, {self.administradora.pk})

    def test_a_carga_relata_o_que_fez(self):
        saida = _rodar()

        self.assertIn(CONDOMINIO_DO_PILOTO, saida)
        self.assertIn("Modelos criados: 9", saida)
        self.assertIn("Modelos já existentes: 0", saida)
        self.assertIn("Critérios criados: 54", saida)


class RepeticaoTests(BaseDaCarga):
    """Rodar de novo não duplica e não sobrescreve o que foi ajustado."""

    def test_segunda_execucao_nao_duplica(self):
        _rodar()
        _rodar()

        self.assertEqual(self._modelos().count(), 9)
        self.assertEqual(
            CriterioModeloConferenciaPreventiva.objects.filter(
                modelo__condominio=self.condominio
            ).count(),
            9 * CRITERIOS_POR_MODELO,
        )

    def test_segunda_execucao_relata_o_que_ja_existia(self):
        _rodar()

        saida = _rodar()

        self.assertIn("Modelos criados: 0", saida)
        self.assertIn("Modelos já existentes: 9", saida)
        self.assertIn("Critérios criados: 0", saida)

    def test_segunda_execucao_preserva_edicao_manual_dos_criterios(self):
        _rodar()
        elevador = self._modelos().get(nome="Elevador")
        criterios = self._criterios(elevador)

        # A Administradora ajusta o roteiro depois: reescreve um item e retira
        # outro. Nada disso pode ser desfeito por uma nova rodada da carga.
        criterios[0].descricao = "Serviço previsto foi executado e registrado"
        criterios[0].save()
        criterios[-1].delete()
        restantes = [
            criterio.descricao for criterio in self._criterios(elevador)
        ]

        _rodar()

        elevador.refresh_from_db()
        self.assertEqual(
            [criterio.descricao for criterio in self._criterios(elevador)],
            restantes,
        )
        self.assertEqual(len(restantes), CRITERIOS_POR_MODELO - 1)
        self.assertIn("Serviço previsto foi executado e registrado", restantes)

    def test_segunda_execucao_nao_reativa_modelo_desativado(self):
        """Desativar é decisão da Administradora, e a carga não a desfaz."""
        _rodar()
        gerador = self._modelos().get(nome="Gerador")
        gerador.ativo = False
        gerador.save(update_fields=["ativo"])

        _rodar()

        gerador.refresh_from_db()
        self.assertFalse(gerador.ativo)


class RecusasTests(BaseDaCarga):
    """Sem um condomínio só e uma Administradora válida, a carga não acontece."""

    def _sem_administradora(self):
        """Retira o vínculo e a conta de quem assinava, deixando o condomínio só."""
        AcessoUsuarioCondominio.objects.filter(usuario=self.administradora).delete()
        self.administradora.delete()

    def _sem_condominio_piloto(self):
        """Desfaz os vínculos e o cadastro do piloto, como se ele não existisse."""
        AcessoUsuarioCondominio.objects.filter(condominio=self.condominio).delete()
        self.condominio.delete()

    def test_ausencia_de_condominio_falha(self):
        self._sem_condominio_piloto()

        with self.assertRaises(CommandError):
            _rodar()

        self.assertEqual(ModeloConferenciaPreventiva.objects.count(), 0)

    def test_ambiguidade_de_condominio_falha(self):
        criar_condominio(CONDOMINIO_DO_PILOTO)

        with self.assertRaises(CommandError):
            _rodar()

        self.assertEqual(ModeloConferenciaPreventiva.objects.count(), 0)

    def test_ausencia_de_administradora_falha(self):
        self._sem_administradora()

        with self.assertRaises(CommandError):
            _rodar()

        self.assertEqual(ModeloConferenciaPreventiva.objects.count(), 0)

    def test_ambiguidade_de_administradora_falha(self):
        outra = criar_pessoa("adm2@exemplo.test", "Outra administradora")
        criar_vinculo(outra, self.condominio, papel=Papel.ADMINISTRADORA)

        with self.assertRaises(CommandError):
            _rodar()

        self.assertEqual(ModeloConferenciaPreventiva.objects.count(), 0)

    def test_sindica_e_supervisao_nao_assinam_a_carga(self):
        """Quem responde pela operação não substitui a Administradora."""
        self._sem_administradora()
        sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(sindica, self.condominio, papel=Papel.SINDICA)
        supervisora = criar_pessoa("supervisao@exemplo.test", "Supervisão")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)

        with self.assertRaises(CommandError):
            _rodar()

        self.assertEqual(ModeloConferenciaPreventiva.objects.count(), 0)

    def test_superuser_nao_substitui_a_administradora(self):
        """Administração do sistema não é função de condomínio."""
        self._sem_administradora()
        get_user_model().objects.create_superuser(
            email="root@exemplo.test", password="senha-forte-123", full_name="Root"
        )

        with self.assertRaises(CommandError):
            _rodar()

        self.assertEqual(ModeloConferenciaPreventiva.objects.count(), 0)

    def test_superuser_nao_e_a_autora_quando_ha_administradora(self):
        get_user_model().objects.create_superuser(
            email="root@exemplo.test", password="senha-forte-123", full_name="Root"
        )

        _rodar()

        autores = set(self._modelos().values_list("criado_por", flat=True))
        self.assertEqual(autores, {self.administradora.pk})


class IsolamentoTests(BaseDaCarga):
    """A carga alcança o condomínio piloto, e só ele."""

    def test_nenhum_outro_condominio_e_alterado(self):
        vizinho = criar_condominio("Vizinho")
        administradora_do_vizinho = criar_pessoa("adm@vizinho.test", "Administradora")
        criar_vinculo(
            administradora_do_vizinho, vizinho, papel=Papel.ADMINISTRADORA
        )

        _rodar()

        self.assertEqual(ModeloConferenciaPreventiva.objects.count(), 9)
        self.assertEqual(self._modelos(condominio=vizinho).count(), 0)
        self.assertEqual(
            CriterioModeloConferenciaPreventiva.objects.filter(
                modelo__condominio=vizinho
            ).count(),
            0,
        )

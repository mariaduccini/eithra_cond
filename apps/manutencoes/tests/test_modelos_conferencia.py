"""Modelos reutilizáveis de conferência preventiva: fundação e autorização.

Um modelo é o roteiro reutilizável de um tipo de serviço — a limpeza da piscina,
a revisão do elevador. Ele não é o plano nem a ocorrência: aplicar o modelo é
COPIAR os critérios para o plano, por valor, e nunca deixar os dois ligados.

A biblioteca é administrada exclusivamente pela Administradora — quem responde
pela configuração estrutural do condomínio. A síndica conduz a operação, mas não
administra a biblioteca nem a aplica ao plano; a supervisão também não. O vínculo
condominial é quem decide, e nunca ``is_staff``.

Esta suíte prova o que a fundação promete: o modelo pertence a um condomínio e
só à Administradora dele; os critérios são copiados na ordem, com a
obrigatoriedade preservada, como registros independentes; editar ou desativar o
modelo depois não alcança plano nenhum; e nenhuma ocorrência já gerada é
reescrita — o snapshot do passado permanece. Também confere a política de
substituição, a autorização por papel e que a cópia é transacional: uma falha no
meio não deixa metade dos critérios copiados.
"""
from datetime import date
from unittest import mock

from django.test import TestCase

from apps.condominios.constants import Papel
from apps.manutencoes.constants import FrequenciaUnidade
from apps.manutencoes.models import (
    CriterioConferenciaPreventiva,
    ModeloConferenciaPreventiva,
    PlanoPreventivo,
)
from apps.manutencoes.permissoes import (
    condominios_para_administrar_modelos,
    pode_administrar_modelos_de_conferencia,
)
from apps.manutencoes.services import (
    ErroDeManutencao,
    aplicar_modelo_de_conferencia,
    criar_modelo_conferencia,
    definir_ativo_do_modelo,
    editar_modelo_conferencia,
    gerar_ocorrencia_preventiva,
)
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)


class BaseDeModelos(TestCase):
    """Um condomínio com quem responde por ele, um segundo condomínio e iscas."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")

        # A Administradora é quem administra a biblioteca de modelos.
        self.administradora = criar_pessoa("adm@exemplo.test", "Administradora")
        criar_vinculo(
            self.administradora, self.condominio, papel=Papel.ADMINISTRADORA
        )

        # A síndica responde pela operação do condomínio, mas não administra a
        # biblioteca de modelos nem a aplica ao plano.
        self.sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.sindica, self.condominio, papel=Papel.SINDICA)

        # A supervisão opera a manutenção, mas não administra configuração do
        # condomínio — é uma isca de quem tentaria administrar modelos sem poder.
        self.supervisora = criar_pessoa("supervisao@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, papel=Papel.SUPERVISORA)

        # Quem responde pelo outro condomínio, de fora de Alpha.
        self.de_fora = criar_pessoa("fora@exemplo.test", "De fora")
        criar_vinculo(self.de_fora, self.outro, papel=Papel.ADMINISTRADORA)

    def _modelo(self, *, condominio=None, criado_por=None, nome="Limpeza de piscina",
                criterios=()):
        """Registra um modelo pelo serviço oficial."""
        return criar_modelo_conferencia(
            condominio=condominio or self.condominio,
            criado_por=criado_por or self.administradora,
            nome=nome,
            criterios=criterios,
        )

    def _plano(self, *, condominio=None, **campos):
        """Um plano válido do condomínio, com a conferência exigida.

        Nasce pelo modelo, e não por serviço: o plano aqui é só o cenário onde o
        modelo será aplicado.
        """
        base = {
            "titulo": "Limpeza do reservatório",
            "frequencia_unidade": FrequenciaUnidade.MESES,
            "frequencia_intervalo": 1,
            "proxima_data_prevista": date(2026, 12, 1),
            "exige_conferencia": True,
            "criado_por": self.sindica,
        }
        base.update(campos)
        plano = PlanoPreventivo(condominio=condominio or self.condominio, **base)
        plano.full_clean()
        plano.save()
        return plano

    def _descricoes(self, colecao):
        """As descrições dos critérios de um modelo ou de um plano, na ordem."""
        return [criterio.descricao for criterio in colecao]


class ModeloTests(BaseDeModelos):
    """O modelo pertence ao condomínio e nasce com critérios ordenados."""

    def test_cria_modelo_no_condominio(self):
        modelo = self._modelo(nome="Revisão de elevador")

        self.assertEqual(modelo.condominio_id, self.condominio.pk)
        self.assertTrue(modelo.ativo)
        self.assertEqual(modelo.criado_por_id, self.administradora.pk)

    def test_cria_criterios_na_ordem_informada(self):
        modelo = self._modelo(
            criterios=[
                {"descricao": "Conferir o nível", "ordem": 2},
                {"descricao": "Testar o funcionamento", "ordem": 0},
                {"descricao": "Verificar vazamentos", "ordem": 1, "obrigatorio": False},
            ]
        )

        self.assertEqual(
            [
                (criterio.descricao, criterio.ordem, criterio.obrigatorio)
                for criterio in modelo.criterios.all()
            ],
            [
                ("Testar o funcionamento", 0, True),
                ("Verificar vazamentos", 1, False),
                ("Conferir o nível", 2, True),
            ],
        )

    def test_modelo_sem_nome_e_recusado(self):
        with self.assertRaises(ErroDeManutencao):
            self._modelo(nome="   ")

    def test_dois_modelos_com_o_mesmo_nome_no_condominio_sao_recusados(self):
        self._modelo(nome="Dedetização")

        # "Dedetização" e "dedetização" são o mesmo roteiro: a unicidade é do
        # condomínio, e a validação do domínio a recusa antes de chegar ao banco.
        with self.assertRaises(ErroDeManutencao):
            self._modelo(nome="dedetização")

    def test_leitura_por_condominio(self):
        modelo = self._modelo(nome="Gerador")
        self._modelo(
            condominio=self.outro, criado_por=self.de_fora, nome="Bomba"
        )

        self.assertEqual(
            list(
                ModeloConferenciaPreventiva.objects.do_condominio(
                    self.condominio
                ).values_list("pk", flat=True)
            ),
            [modelo.pk],
        )
        self.assertEqual(
            ModeloConferenciaPreventiva.objects.ativos().count(), 2
        )


class AutorizacaoTests(BaseDeModelos):
    """A biblioteca é da Administradora: só ela cria, edita, desativa e aplica."""

    def test_administradora_cria_edita_desativa_e_aplica(self):
        modelo = self._modelo(
            criado_por=self.administradora,
            criterios=[{"descricao": "Água limpa", "ordem": 0}],
        )

        editar_modelo_conferencia(
            modelo=modelo,
            usuario=self.administradora,
            nome="Limpeza de piscina",
            criterios=[{"descricao": "pH dentro da faixa", "ordem": 0}],
        )
        plano = self._plano()
        aplicar_modelo_de_conferencia(
            plano=plano, modelo=modelo, usuario=self.administradora
        )
        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["pH dentro da faixa"],
        )

        definir_ativo_do_modelo(
            modelo=modelo, usuario=self.administradora, ativo=False
        )
        self.assertFalse(modelo.ativo)

    def test_sindica_nao_cria_modelo(self):
        with self.assertRaises(ErroDeManutencao):
            self._modelo(criado_por=self.sindica)

        self.assertFalse(
            ModeloConferenciaPreventiva.objects.do_condominio(
                self.condominio
            ).exists()
        )

    def test_sindica_nao_edita_modelo(self):
        modelo = self._modelo(criterios=[{"descricao": "Água limpa", "ordem": 0}])

        with self.assertRaises(ErroDeManutencao):
            editar_modelo_conferencia(
                modelo=modelo,
                usuario=self.sindica,
                nome="Outro nome",
                criterios=[{"descricao": "Outra coisa", "ordem": 0}],
            )

        self.assertEqual(self._descricoes(modelo.criterios.all()), ["Água limpa"])

    def test_sindica_nao_desativa_modelo(self):
        modelo = self._modelo()

        with self.assertRaises(ErroDeManutencao):
            definir_ativo_do_modelo(modelo=modelo, usuario=self.sindica, ativo=False)

        self.assertTrue(modelo.ativo)

    def test_sindica_nao_aplica_nem_substitui_no_plano(self):
        plano = self._plano()
        CriterioConferenciaPreventiva.objects.create(
            plano=plano, descricao="Critério antigo", ordem=0
        )
        modelo = self._modelo(criterios=[{"descricao": "Água limpa", "ordem": 0}])

        with self.assertRaises(ErroDeManutencao):
            aplicar_modelo_de_conferencia(
                plano=plano, modelo=modelo, usuario=self.sindica, substituir=True
            )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Critério antigo"],
        )

    def test_sindica_nao_aplica_em_plano_sem_criterios(self):
        plano = self._plano()
        modelo = self._modelo(criterios=[{"descricao": "Água limpa", "ordem": 0}])

        with self.assertRaises(ErroDeManutencao):
            aplicar_modelo_de_conferencia(
                plano=plano, modelo=modelo, usuario=self.sindica
            )

        self.assertFalse(plano.criterios_conferencia.exists())

    def test_supervisora_nao_administra_modelos(self):
        with self.assertRaises(ErroDeManutencao):
            self._modelo(criado_por=self.supervisora)


class AplicacaoTests(BaseDeModelos):
    """Aplicar o modelo copia os critérios para o plano, por valor."""

    def _modelo_com_criterios(self):
        return self._modelo(
            criterios=[
                {"descricao": "Água limpa", "ordem": 0},
                {"descricao": "Sem vazamento aparente", "ordem": 1},
                {"descricao": "Relatório anexado", "ordem": 2, "obrigatorio": False},
            ]
        )

    def test_criterios_sao_copiados_para_o_plano(self):
        plano = self._plano()
        modelo = self._modelo_com_criterios()

        aplicar_modelo_de_conferencia(
            plano=plano, modelo=modelo, usuario=self.administradora
        )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Água limpa", "Sem vazamento aparente", "Relatório anexado"],
        )

    def test_ordem_e_obrigatoriedade_sao_preservadas(self):
        plano = self._plano()
        modelo = self._modelo_com_criterios()

        aplicar_modelo_de_conferencia(
            plano=plano, modelo=modelo, usuario=self.administradora
        )

        self.assertEqual(
            [
                (criterio.descricao, criterio.ordem, criterio.obrigatorio)
                for criterio in plano.criterios_conferencia.all()
            ],
            [
                ("Água limpa", 0, True),
                ("Sem vazamento aparente", 1, True),
                ("Relatório anexado", 2, False),
            ],
        )

    def test_criterios_copiados_sao_registros_independentes(self):
        plano = self._plano()
        modelo = self._modelo_com_criterios()

        aplicar_modelo_de_conferencia(
            plano=plano, modelo=modelo, usuario=self.administradora
        )
        do_modelo = {criterio.pk for criterio in modelo.criterios.all()}
        do_plano = {criterio.pk for criterio in plano.criterios_conferencia.all()}

        self.assertTrue(do_plano.isdisjoint(do_modelo))

    def test_editar_o_modelo_depois_nao_altera_o_plano(self):
        plano = self._plano()
        modelo = self._modelo_com_criterios()

        aplicar_modelo_de_conferencia(
            plano=plano, modelo=modelo, usuario=self.administradora
        )
        editar_modelo_conferencia(
            modelo=modelo,
            usuario=self.administradora,
            nome="Limpeza de piscina",
            criterios=[{"descricao": "Água com pH adequado", "ordem": 0}],
        )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Água limpa", "Sem vazamento aparente", "Relatório anexado"],
        )

    def test_desativar_o_modelo_depois_nao_altera_o_plano(self):
        plano = self._plano()
        modelo = self._modelo_com_criterios()

        aplicar_modelo_de_conferencia(
            plano=plano, modelo=modelo, usuario=self.administradora
        )
        definir_ativo_do_modelo(
            modelo=modelo, usuario=self.administradora, ativo=False
        )

        self.assertFalse(modelo.ativo)
        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Água limpa", "Sem vazamento aparente", "Relatório anexado"],
        )

    def test_modelo_inativo_nao_pode_ser_aplicado(self):
        plano = self._plano()
        modelo = self._modelo_com_criterios()
        definir_ativo_do_modelo(
            modelo=modelo, usuario=self.administradora, ativo=False
        )

        with self.assertRaises(ErroDeManutencao):
            aplicar_modelo_de_conferencia(
                plano=plano, modelo=modelo, usuario=self.administradora
            )

        self.assertFalse(plano.criterios_conferencia.exists())

    def test_modelo_sem_criterios_nao_apaga_o_plano(self):
        plano = self._plano()
        CriterioConferenciaPreventiva.objects.create(
            plano=plano, descricao="Critério já combinado", ordem=0
        )
        modelo = self._modelo(nome="Vazio")

        with self.assertRaises(ErroDeManutencao):
            aplicar_modelo_de_conferencia(
                plano=plano,
                modelo=modelo,
                usuario=self.administradora,
                substituir=True,
            )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Critério já combinado"],
        )


class SubstituicaoTests(BaseDeModelos):
    """A política de substituição: nada é trocado em silêncio; o passado fica."""

    def test_plano_ja_tem_criterios_exige_substituicao_explicita(self):
        plano = self._plano()
        CriterioConferenciaPreventiva.objects.create(
            plano=plano, descricao="Critério já combinado", ordem=0
        )
        modelo = self._modelo(
            criterios=[{"descricao": "Água limpa", "ordem": 0}]
        )

        with self.assertRaises(ErroDeManutencao):
            aplicar_modelo_de_conferencia(
                plano=plano, modelo=modelo, usuario=self.administradora
            )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Critério já combinado"],
        )

    def test_substituicao_troca_a_lista_por_inteiro(self):
        plano = self._plano()
        CriterioConferenciaPreventiva.objects.create(
            plano=plano, descricao="Critério antigo", ordem=0
        )
        modelo = self._modelo(
            criterios=[
                {"descricao": "Água limpa", "ordem": 0},
                {"descricao": "Sem vazamento", "ordem": 1},
            ]
        )

        aplicar_modelo_de_conferencia(
            plano=plano, modelo=modelo, usuario=self.administradora, substituir=True
        )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Água limpa", "Sem vazamento"],
        )

    def test_ocorrencia_anterior_nunca_muda_quando_o_plano_e_substituido(self):
        plano = self._plano()
        primeiro = self._modelo(
            nome="Modelo inicial",
            criterios=[
                {"descricao": "Água limpa", "ordem": 0},
                {"descricao": "Sem vazamento", "ordem": 1},
            ],
        )
        aplicar_modelo_de_conferencia(
            plano=plano, modelo=primeiro, usuario=self.administradora
        )
        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.sindica)

        segundo = self._modelo(
            nome="Modelo novo",
            criterios=[{"descricao": "pH dentro da faixa", "ordem": 0}],
        )
        aplicar_modelo_de_conferencia(
            plano=plano, modelo=segundo, usuario=self.administradora, substituir=True
        )

        # O plano mudou para as próximas ocorrências; a que já nasceu guardou o
        # retrato do dia e continua com os critérios de então.
        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["pH dentro da faixa"],
        )
        self.assertEqual(
            self._descricoes(ocorrencia.criterios_conferencia.all()),
            ["Água limpa", "Sem vazamento"],
        )


class IsolamentoTests(BaseDeModelos):
    """Modelo de outro condomínio não atravessa; sem vínculo, ninguém administra."""

    def test_modelo_de_outro_condominio_nao_pode_ser_aplicado(self):
        plano = self._plano()
        modelo_alheio = self._modelo(
            condominio=self.outro,
            criado_por=self.de_fora,
            nome="Bomba do Beta",
            criterios=[{"descricao": "Conferir pressão", "ordem": 0}],
        )

        # Mesmo quem administra Alpha não aplica um modelo que é de Beta.
        with self.assertRaises(ErroDeManutencao):
            aplicar_modelo_de_conferencia(
                plano=plano, modelo=modelo_alheio, usuario=self.administradora
            )

        self.assertFalse(plano.criterios_conferencia.exists())

    def test_usuario_sem_acesso_nao_cria_modelo_no_condominio(self):
        with self.assertRaises(ErroDeManutencao):
            self._modelo(condominio=self.condominio, criado_por=self.de_fora)

        self.assertFalse(
            ModeloConferenciaPreventiva.objects.do_condominio(
                self.condominio
            ).exists()
        )

    def test_permissoes_e_leitura_respeitam_o_vinculo(self):
        self.assertTrue(
            pode_administrar_modelos_de_conferencia(
                self.administradora, self.condominio
            )
        )
        self.assertFalse(
            pode_administrar_modelos_de_conferencia(self.sindica, self.condominio)
        )
        self.assertFalse(
            pode_administrar_modelos_de_conferencia(self.supervisora, self.condominio)
        )
        self.assertFalse(
            pode_administrar_modelos_de_conferencia(self.de_fora, self.condominio)
        )

        self.assertIn(
            self.condominio,
            condominios_para_administrar_modelos(self.administradora),
        )
        self.assertNotIn(
            self.condominio,
            condominios_para_administrar_modelos(self.sindica),
        )
        self.assertNotIn(
            self.condominio,
            condominios_para_administrar_modelos(self.de_fora),
        )


class IntegridadeTests(BaseDeModelos):
    """A cópia é uma transação: falha no meio não deixa metade dos critérios."""

    def test_falha_na_copia_nao_deixa_metade_dos_criterios(self):
        plano = self._plano()
        CriterioConferenciaPreventiva.objects.create(
            plano=plano, descricao="Critério antigo", ordem=0
        )
        modelo = self._modelo(
            criterios=[
                {"descricao": "Água limpa", "ordem": 0},
                {"descricao": "Sem vazamento", "ordem": 1},
            ]
        )

        with mock.patch.object(
            CriterioConferenciaPreventiva.objects,
            "bulk_create",
            side_effect=RuntimeError("falha simulada"),
        ):
            with self.assertRaises(RuntimeError):
                aplicar_modelo_de_conferencia(
                    plano=plano,
                    modelo=modelo,
                    usuario=self.administradora,
                    substituir=True,
                )

        # A transação desfez tudo: a lista antiga continua inteira, sem itens
        # novos pela metade.
        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Critério antigo"],
        )

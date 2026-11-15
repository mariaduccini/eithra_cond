"""As operações do domínio: criar, revisar e gerar o card.

O que se verifica aqui é que o serviço é o portal único da escrita — ele grava o
comunicado e a memória dele na mesma transação, guarda o arquivo antes de a
referência existir, e deixa na origem apenas a lembrança de que um comunicado
saiu. Nada de rede: o armazenamento é o de mentira.
"""
from unittest.mock import patch

from django.test import TestCase

from apps.condominios.constants import Papel
from apps.core.storage import ErroDeStorage
from apps.demandas.models import HistoricoDemanda
from apps.manutencoes.models import HistoricoManutencao

from .apoio import (
    StorageDeMentira,
    criar_condominio,
    criar_demanda,
    criar_manutencao,
    criar_pessoa,
    criar_vinculo,
)
from .. import services
from ..constants import (
    MIMES_DE_COMUNICADO,
    OrigemComunicado,
    OrientacaoDoCard,
    StatusComunicacao,
    TipoEventoComunicacao,
)
from ..models import Comunicacao, HistoricoComunicacao


class CriarComunicacaoTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.autor = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.autor, self.condominio, papel=Papel.SINDICA)

    def test_criar_registra_o_primeiro_evento(self):
        comunicacao = services.criar_comunicacao(
            condominio=self.condominio,
            criado_por=self.autor,
            titulo="  Falta de água  ",
            mensagem="  O abastecimento volta à tarde.  ",
        )

        self.assertEqual(comunicacao.status, StatusComunicacao.RASCUNHO)
        self.assertEqual(comunicacao.titulo, "Falta de água")
        self.assertEqual(comunicacao.mensagem, "O abastecimento volta à tarde.")
        eventos = comunicacao.historico.all()
        self.assertEqual(eventos.count(), 1)
        self.assertEqual(eventos.first().tipo_evento, TipoEventoComunicacao.CRIADA)

    def test_criar_a_partir_de_uma_demanda_guarda_a_referencia(self):
        demanda = criar_demanda(self.condominio, self.autor, titulo="Falta de água")

        comunicacao = services.criar_comunicacao(
            condominio=self.condominio,
            criado_por=self.autor,
            titulo="Falta de água",
            origem_tipo=OrigemComunicado.DEMANDA,
            origem_demanda=demanda,
        )

        self.assertEqual(comunicacao.origem_tipo, OrigemComunicado.DEMANDA)
        self.assertEqual(comunicacao.origem_demanda, demanda)
        self.assertIsNone(comunicacao.origem_manutencao)


class EditarComunicacaoTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.autor = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.autor, self.condominio, papel=Papel.SINDICA)
        self.comunicacao = services.criar_comunicacao(
            condominio=self.condominio, criado_por=self.autor, titulo="Aviso"
        )

    def test_revisao_registra_apenas_o_campo_alterado(self):
        services.editar_comunicacao(
            self.comunicacao, usuario=self.autor, titulo="Aviso revisado"
        )

        evento = self.comunicacao.historico.first()
        self.assertEqual(evento.tipo_evento, TipoEventoComunicacao.EDITADA)
        self.assertEqual(evento.dados_anteriores, {"titulo": "Aviso"})
        self.assertEqual(evento.dados_novos, {"titulo": "Aviso revisado"})
        self.assertIn("título", evento.descricao)

    def test_sem_mudanca_nao_cria_evento(self):
        services.editar_comunicacao(
            self.comunicacao, usuario=self.autor, titulo="Aviso"
        )
        self.assertEqual(
            HistoricoComunicacao.objects.filter(
                comunicacao=self.comunicacao
            ).count(),
            1,
        )

    def test_campo_nao_informado_nao_e_tocado(self):
        self.comunicacao.mensagem = "Mensagem original"
        self.comunicacao.save()

        services.editar_comunicacao(
            self.comunicacao, usuario=self.autor, titulo="Outro título"
        )

        self.comunicacao.refresh_from_db()
        self.assertEqual(self.comunicacao.mensagem, "Mensagem original")


class GerarCardTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.autor = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.autor, self.condominio, papel=Papel.SINDICA)
        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.comunicacao.arquivos.obter_provider", return_value=self.provider
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _comunicacao(self, **campos):
        dados = {"titulo": "Aviso aos moradores", "mensagem": "O elevador para na sexta."}
        dados.update(campos)
        return services.criar_comunicacao(
            condominio=self.condominio, criado_por=self.autor, **dados
        )

    def test_gerar_guarda_o_card_e_marca_como_pronta(self):
        comunicacao = self._comunicacao()

        services.gerar_card(comunicacao, usuario=self.autor)

        comunicacao.refresh_from_db()
        self.assertEqual(comunicacao.status, StatusComunicacao.PRONTA)
        self.assertTrue(comunicacao.esta_pronta)
        self.assertIn(comunicacao.mime_type, MIMES_DE_COMUNICADO)
        self.assertGreater(comunicacao.tamanho_bytes, 0)
        self.assertEqual(len(comunicacao.sha256), 64)
        self.assertIsNotNone(comunicacao.gerado_em)
        self.assertIn(comunicacao.storage_key, self.provider.objetos)

        # O PNG começa com a assinatura do formato.
        self.assertTrue(self.provider.objetos[comunicacao.storage_key].startswith(b"\x89PNG"))

    def test_gerar_registra_o_evento_da_imagem(self):
        comunicacao = self._comunicacao()
        services.gerar_card(comunicacao, usuario=self.autor)

        evento = comunicacao.historico.first()
        self.assertEqual(evento.tipo_evento, TipoEventoComunicacao.IMAGEM_GERADA)

    def test_card_respeita_a_orientacao(self):
        from PIL import Image
        from io import BytesIO

        vertical = self._comunicacao(orientacao=OrientacaoDoCard.VERTICAL)
        services.gerar_card(vertical, usuario=self.autor)
        imagem = Image.open(BytesIO(self.provider.objetos[vertical.storage_key]))
        self.assertGreater(imagem.height, imagem.width)

        horizontal = self._comunicacao(orientacao=OrientacaoDoCard.HORIZONTAL)
        services.gerar_card(horizontal, usuario=self.autor)
        imagem = Image.open(BytesIO(self.provider.objetos[horizontal.storage_key]))
        self.assertGreater(imagem.width, imagem.height)

    def test_lembranca_na_demanda_de_origem(self):
        demanda = criar_demanda(self.condominio, self.autor)
        comunicacao = self._comunicacao(
            origem_tipo=OrigemComunicado.DEMANDA, origem_demanda=demanda
        )

        services.gerar_card(comunicacao, usuario=self.autor)

        evento = HistoricoDemanda.objects.filter(
            demanda=demanda, tipo_evento="comunicado"
        ).first()
        self.assertIsNotNone(evento)
        self.assertIn(comunicacao.titulo, evento.descricao)

    def test_lembranca_na_manutencao_de_origem(self):
        manutencao = criar_manutencao(self.condominio, self.autor)
        comunicacao = self._comunicacao(
            origem_tipo=OrigemComunicado.MANUTENCAO, origem_manutencao=manutencao
        )

        services.gerar_card(comunicacao, usuario=self.autor)

        evento = HistoricoManutencao.objects.filter(
            manutencao=manutencao, tipo_evento="comunicado"
        ).first()
        self.assertIsNotNone(evento)

    def test_falha_de_storage_nao_deixa_comunicacao_pronta(self):
        comunicacao = self._comunicacao()
        self.provider.falhar_ao_enviar = True

        with self.assertRaises(ErroDeStorage):
            services.gerar_card(comunicacao, usuario=self.autor)

        comunicacao.refresh_from_db()
        self.assertEqual(comunicacao.status, StatusComunicacao.RASCUNHO)
        self.assertEqual(comunicacao.storage_key, "")
        self.assertIsNone(comunicacao.gerado_em)

    def test_acesso_temporario_pede_endereco_ao_provedor(self):
        comunicacao = self._comunicacao()
        services.gerar_card(comunicacao, usuario=self.autor)

        endereco = services.acesso_temporario(comunicacao)

        self.assertIn(comunicacao.storage_key, endereco)
        self.assertEqual(self.provider.assinaturas[-1][0], comunicacao.storage_key)

    def test_acesso_temporario_sem_card_e_recusado(self):
        comunicacao = self._comunicacao()
        with self.assertRaises(ErroDeStorage):
            services.acesso_temporario(comunicacao)


class IsolamentoDoComunicadoTests(TestCase):
    """A comunicação é isolada por condomínio, e o QuerySet diz isso."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.autor = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.autor, self.alpha, papel=Papel.SINDICA)
        criar_vinculo(self.autor, self.beta, papel=Papel.SINDICA)
        self.do_alpha = services.criar_comunicacao(
            condominio=self.alpha, criado_por=self.autor, titulo="Aviso Alpha"
        )
        self.do_beta = services.criar_comunicacao(
            condominio=self.beta, criado_por=self.autor, titulo="Aviso Beta"
        )

    def test_do_condominio_nao_mistura_condominios(self):
        self.assertEqual(
            list(Comunicacao.objects.do_condominio(self.alpha)), [self.do_alpha]
        )
        self.assertEqual(
            list(Comunicacao.objects.do_condominio(self.beta)), [self.do_beta]
        )

    def test_do_condominio_sem_condominio_devolve_nada(self):
        self.assertEqual(Comunicacao.objects.do_condominio(None).count(), 0)

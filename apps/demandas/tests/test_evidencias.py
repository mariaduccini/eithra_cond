"""Foto e vídeo como prova do que se sabe sobre uma demanda.

A evidência não é um registro à parte: ela se prende à demanda e à movimentação
que a trouxe. É isso que estes testes prendem — anexar não cria demanda, não
conclui, não cancela; a prova acompanha o evento certo; e o arquivo só chega a
quem pode acompanhar aquela demanda.

Nenhum teste fala com armazenamento de verdade: o provider é substituído por um
storage em memória, como nos balanços. Assim o que se verifica é o que as
Demandas fazem com a prova, não o que o bucket faria com ela.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda, TipoEventoDemanda, TipoMidiaEvidencia
from apps.demandas.models import EvidenciaDemanda
from apps.demandas.services import alterar_status, criar_demanda
from apps.demandas.tests.apoio import (
    StorageDeMentira,
    arquivo_qualquer,
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
    jpg,
    mp4,
    png,
    webm,
)
from apps.demandas.tests.test_audio import (
    CONFIGURADO,
    RespostaFalsa,
    corpo_json,
    recado,
)

RELATO = "O portão da garagem voltou a travar depois da chuva."


class BaseDeEvidencias(TestCase):
    """Uma demanda de teste e um storage em memória no lugar do provedor.

    O mesmo provedor serve às duas portas de anexo, porque as duas passam pela
    mesma função: ``provedor_das_evidencias``. Programá-lo para falhar é como se
    verifica que uma falha de armazenamento não vira sucesso.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.demandas.evidencias.obter_provider", return_value=self.provider
        )
        self.patch_do_storage = patcher.start()
        self.addCleanup(patcher.stop)

    def _demanda(self, **campos):
        # A demanda nasce em "Aberta"; quando o cenário pede outra situação, ela
        # é movida pelo mesmo caminho que a gestão usaria — nada é escrito à mão.
        status = campos.pop("status", None)
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Demanda de teste",
        }
        base.update(campos)
        demanda = criar_demanda(**base)
        if status and status != StatusDemanda.ABERTA:
            alterar_status(demanda=demanda, usuario=self.gestao, novo_status=status)
        return demanda

    def _anexar(self, *arquivos, demanda=None):
        """Anexa as provas pela porta própria — sem mexer na situação."""
        demanda = demanda or self._demanda()
        return self.client.post(
            reverse("demandas:adicionar_evidencia", args=[demanda.pk]),
            {"evidencias": list(arquivos)},
        )

    def _revisar(self, demanda, texto=RELATO, arquivos=()):
        """A primeira etapa da atualização: só mostra o que vai acontecer."""
        return self.client.post(
            reverse("demandas:atualizar", args=[demanda.pk]),
            {
                "texto": texto,
                "etapa_atualizacao": "informacoes",
                "evidencias": list(arquivos),
            },
        )

    def _confirmar(self, demanda, etapa, texto=RELATO, arquivos=()):
        """A etapa de confirmação, com a marca que a tela devolveu."""
        return self.client.post(
            reverse("demandas:atualizar", args=[demanda.pk]),
            {
                "texto": texto,
                "etapa_atualizacao": "conclusao",
                "confirmacao": etapa.context["confirmacao"],
                "evidencias": list(arquivos),
            },
        )


class AnexarEvidenciaTests(BaseDeEvidencias):
    """A porta de anexo: guardar a prova sem mexer no que a demanda é."""

    def test_anexa_uma_foto(self):
        demanda = self._demanda()

        resposta = self._anexar(png(), demanda=demanda)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(EvidenciaDemanda.objects.count(), 1)
        evidencia = EvidenciaDemanda.objects.get()
        self.assertEqual(evidencia.tipo_midia, TipoMidiaEvidencia.FOTO)
        self.assertEqual(evidencia.mime_type, "image/png")
        self.assertEqual(evidencia.demanda_id, demanda.pk)
        self.assertEqual(evidencia.criado_por_id, self.gestao.pk)
        self.assertEqual(evidencia.storage_provider, self.provider.nome)
        self.assertEqual(evidencia.storage_bucket, self.provider.bucket)
        self.assertEqual(len(self.provider.objetos), 1)

    def test_anexa_multiplas_fotos(self):
        demanda = self._demanda()

        resposta = self._anexar(png(marca="a"), jpg(marca="b"), demanda=demanda)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(EvidenciaDemanda.objects.filter(demanda=demanda).count(), 2)
        self.assertEqual(len(self.provider.objetos), 2)

    def test_anexa_video_valido(self):
        demanda = self._demanda()

        resposta = self._anexar(mp4(), demanda=demanda)

        self.assertEqual(resposta.status_code, 302)
        evidencia = EvidenciaDemanda.objects.get()
        self.assertEqual(evidencia.tipo_midia, TipoMidiaEvidencia.VIDEO)
        self.assertEqual(evidencia.mime_type, "video/mp4")

    def test_anexa_video_webm(self):
        demanda = self._demanda()

        resposta = self._anexar(webm(), demanda=demanda)

        self.assertEqual(resposta.status_code, 302)
        evidencia = EvidenciaDemanda.objects.get()
        self.assertEqual(evidencia.mime_type, "video/webm")

    def test_a_chave_do_objeto_nao_depende_do_nome_enviado(self):
        """O nome é rótulo: quem decide onde o objeto fica são os identificadores."""
        demanda = self._demanda()

        self._anexar(png(nome="foto da garagem.png"), demanda=demanda)

        evidencia = EvidenciaDemanda.objects.get()
        self.assertEqual(evidencia.nome_original, "foto da garagem.png")
        self.assertEqual(
            evidencia.storage_key,
            f"demandas/{self.condominio.pk}/{demanda.pk}/evidencias/{evidencia.pk}.png",
        )
        self.assertNotIn("foto da garagem", evidencia.storage_key)

    def test_anexar_evidencia_nao_altera_a_situacao(self):
        demanda = self._demanda()

        self._anexar(png(), demanda=demanda)

        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)
        self.assertEqual(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.EVIDENCIA).count(), 1
        )

    def test_anexar_evidencia_nao_cria_demanda(self):
        demanda = self._demanda()

        self._anexar(png(), demanda=demanda)

        self.assertEqual(demanda.__class__.objects.count(), 1)

    def test_arquivo_invalido_e_recusado(self):
        demanda = self._demanda()

        resposta = self._anexar(arquivo_qualquer(), demanda=demanda)

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(EvidenciaDemanda.objects.count(), 0)
        self.assertEqual(demanda.historico.count(), 1)

    @override_settings(DEMANDAS_MAX_FOTO_BYTES=10)
    def test_arquivo_acima_do_limite_e_recusado(self):
        demanda = self._demanda()

        resposta = self._anexar(png(), demanda=demanda)

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(EvidenciaDemanda.objects.count(), 0)
        self.assertEqual(demanda.historico.count(), 1)

    def test_falha_no_upload_nao_deixa_evento_orfa(self):
        """Sem o arquivo, o evento que o anunciava também não fica."""
        self.provider.falhar_ao_enviar = True
        demanda = self._demanda()

        resposta = self._anexar(png(), demanda=demanda)

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(EvidenciaDemanda.objects.count(), 0)
        self.assertEqual(demanda.historico.count(), 1)

    def test_o_mesmo_arquivo_nao_entra_duas_vezes(self):
        demanda = self._demanda()
        self._anexar(png(), demanda=demanda)

        resposta = self._anexar(png(), demanda=demanda)

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(EvidenciaDemanda.objects.count(), 1)


class EvidenciaNaAtualizacaoTests(BaseDeEvidencias):
    """A prova que acompanha um relato, no mesmo envio que o confirma."""

    def test_atualizacao_com_texto_e_foto(self):
        demanda = self._demanda()

        etapa = self._revisar(demanda, arquivos=[png()])
        self.assertEqual(etapa.status_code, 200)
        # A revisão não toca em nada: só mostra o que vai acontecer.
        self.assertEqual(EvidenciaDemanda.objects.count(), 0)
        self.assertEqual(demanda.historico.count(), 1)

        resposta = self._confirmar(demanda, etapa, arquivos=[png()])

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(EvidenciaDemanda.objects.count(), 1)
        self.assertEqual(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.OBSERVACAO).count(),
            1,
        )

    def test_evidencia_acompanha_o_evento_do_relato(self):
        demanda = self._demanda()
        etapa = self._revisar(demanda, arquivos=[png()])

        self._confirmar(demanda, etapa, arquivos=[png()])

        evidencia = EvidenciaDemanda.objects.get()
        evento = evidencia.evento
        self.assertEqual(evento.tipo_evento, TipoEventoDemanda.OBSERVACAO)
        self.assertEqual(evento.demanda_id, demanda.pk)
        self.assertIn(RELATO.split()[0], evento.descricao)

    def test_atualizacao_so_com_arquivo_ganha_evento_proprio(self):
        """Sem relato, a prova precisa de um lugar na linha do tempo."""
        demanda = self._demanda()

        etapa = self._revisar(demanda, texto="", arquivos=[png()])
        self._confirmar(demanda, etapa, texto="", arquivos=[png()])

        evidencia = EvidenciaDemanda.objects.get()
        self.assertEqual(evidencia.evento.tipo_evento, TipoEventoDemanda.EVIDENCIA)

    def test_conclusao_explicita_continua_exigindo_confirmacao(self):
        """A leitura propõe; quem conclui é a confirmação de quem registrou."""
        demanda = self._demanda()

        etapa = self._revisar(
            demanda, texto="Serviço concluído hoje. Elevador testado e funcionando."
        )

        self.assertEqual(etapa.status_code, 200)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)

        resposta = self._confirmar(
            demanda, etapa, texto="Serviço concluído hoje. Elevador testado e funcionando."
        )

        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)

    def test_falha_no_upload_nao_muda_a_situacao_nem_registra_o_relato(self):
        """Relato, situação e arquivo entram juntos — ou não entram."""
        self.provider.falhar_ao_enviar = True
        demanda = self._demanda(status=StatusDemanda.EM_ANDAMENTO)

        etapa = self._revisar(
            demanda,
            texto="Serviço concluído hoje. Elevador testado e funcionando.",
            arquivos=[png()],
        )
        resposta = self._confirmar(
            demanda,
            etapa,
            texto="Serviço concluído hoje. Elevador testado e funcionando.",
            arquivos=[png()],
        )

        self.assertEqual(resposta.status_code, 400)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.EM_ANDAMENTO)
        self.assertEqual(EvidenciaDemanda.objects.count(), 0)
        self.assertEqual(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.OBSERVACAO).count(),
            0,
        )

    def test_confirmacao_repetida_nao_duplica_movimentacao(self):
        demanda = self._demanda()
        etapa = self._revisar(demanda, arquivos=[png()])
        self._confirmar(demanda, etapa, arquivos=[png()])

        repetida = self._confirmar(demanda, etapa, arquivos=[png()])

        self.assertEqual(repetida.status_code, 302)
        self.assertEqual(EvidenciaDemanda.objects.count(), 1)
        self.assertEqual(demanda.historico.count(), 2)


class EvidenciaPorAudioTests(BaseDeEvidencias):
    """Ditar é escrever: a fala vira o mesmo relato, com a mesma prova."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_atualizacao_com_audio_transcrito_e_foto(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": RELATO}))
        demanda = self._demanda()

        ditado = self.client.post(
            reverse("demandas:transcrever"),
            {"demanda": str(demanda.pk), "audio": recado()},
        )
        self.assertEqual(ditado.status_code, 200)
        texto = ditado.json()["texto"]

        etapa = self._revisar(demanda, texto=texto, arquivos=[png()])
        resposta = self._confirmar(demanda, etapa, texto=texto, arquivos=[png()])

        self.assertEqual(resposta.status_code, 302)
        evidencia = EvidenciaDemanda.objects.get()
        self.assertEqual(evidencia.evento.tipo_evento, TipoEventoDemanda.OBSERVACAO)
        self.assertEqual(evidencia.evento.demanda_id, demanda.pk)
        self.assertEqual(EvidenciaDemanda.objects.count(), 1)


class AcessoAMidiaTests(BaseDeEvidencias):
    """A prova é da demanda: quem não a acompanha não a alcança."""

    def _com_arquivo(self):
        demanda = self._demanda()
        self._anexar(png(), demanda=demanda)
        return demanda, EvidenciaDemanda.objects.get()

    def test_quem_acompanha_recebe_o_endereco_temporario(self):
        demanda, evidencia = self._com_arquivo()

        resposta = self.client.get(
            reverse("demandas:evidencia_arquivo", args=[demanda.pk, evidencia.pk])
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(evidencia.storage_key, resposta["Location"])

    def test_usuario_sem_autorizacao_recebe_404(self):
        demanda, evidencia = self._com_arquivo()
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self.client.get(
            reverse("demandas:evidencia_arquivo", args=[demanda.pk, evidencia.pk])
        )

        self.assertEqual(resposta.status_code, 404)

    def test_outro_condominio_recebe_404(self):
        demanda, evidencia = self._com_arquivo()
        outro = criar_condominio("Beta")
        vizinho = criar_pessoa("vizinho@exemplo.test", "Vizinha")
        criar_vinculo(vizinho, outro, papel=Papel.SINDICA)
        self.client.force_login(vizinho)

        arquivo = self.client.get(
            reverse("demandas:evidencia_arquivo", args=[demanda.pk, evidencia.pk])
        )
        anexar = self._anexar(png(marca="b"), demanda=demanda)

        self.assertEqual(arquivo.status_code, 404)
        self.assertEqual(anexar.status_code, 404)

    def test_morador_nao_anexa_evidencia(self):
        demanda = self._demanda()
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._anexar(png(), demanda=demanda)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(EvidenciaDemanda.objects.count(), 0)


class VisualizacaoDoDetalheTests(BaseDeEvidencias):
    """O histórico mostra a prova sem virar galeria."""

    def test_detalhe_com_foto_e_video_renderiza(self):
        demanda = self._demanda()
        self._anexar(png(), mp4(), demanda=demanda)

        resposta = self.client.get(
            f"{reverse('demandas:detalhe', args=[demanda.pk])}?aba=historico"
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.context["tem_evidencias"])
        self.assertTrue(resposta.context["tem_videos"])
        self.assertContains(resposta, "ver-evidencia")
        self.assertContains(resposta, "eithra-thumb-video")
        for evidencia in EvidenciaDemanda.objects.all():
            self.assertContains(
                resposta,
                reverse(
                    "demandas:evidencia_arquivo", args=[demanda.pk, evidencia.pk]
                ),
            )

    def test_detalhe_sem_prova_nao_abre_janela_de_ampliacao(self):
        demanda = self._demanda()

        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["tem_evidencias"])
        self.assertNotContains(resposta, 'id="ver-evidencia"')

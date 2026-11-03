"""A prova anexada a uma manutenção: o que ela guarda e o que ela não decide.

A evidência pertence à manutenção e serve à execução e à conferência — não é
acervo paralelo nem inicia, conclui, aprova ou reprova nada. Estes testes
prendem exatamente isso: o que entra (foto, vídeo, documento), o que é recusado,
o que fica registrado sobre o arquivo, a data informada pela imagem quando ela
a traz, a data de recebimento escrita pelo servidor, a idempotência do mesmo
conteúdo e o isolamento entre condomínios.

Nenhum teste fala com armazenamento de verdade: o provedor é substituído por um
storage em memória, como nos balanços e nas demandas. Assim o que se verifica é
o que as Manutenções fazem com a prova, não o que o bucket faria com ela.
"""
import hashlib
from datetime import date, datetime
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.core.storage import ErroDeStorage
from apps.core.tests.apoio import CONTEUDO_JPG, CONTEUDO_PDF, CONTEUDO_PNG
from apps.manutencoes.constants import (
    ContextoEvidencia,
    FrequenciaUnidade,
    OrigemCaptura,
    StatusConferencia,
    StatusManutencao,
    TipoEvidenciaManutencao,
    TipoManutencao,
)
from apps.manutencoes.evidencias import (
    ErroDeEvidencia,
    adicionar_evidencia,
    data_da_captura,
    validar_evidencia,
)
from apps.manutencoes.models import (
    EvidenciaManutencao,
    HistoricoManutencao,
    PlanoPreventivo,
)
from apps.manutencoes.services import (
    aprovar_conferencia,
    concluir_manutencao,
    criar_manutencao,
    gerar_ocorrencia_preventiva,
    registrar_execucao,
)
from apps.manutencoes.tests.apoio import (
    StorageDeMentira,
    arquivo_qualquer,
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
    jpg,
    mp4,
    pdf,
    png,
    webm,
    webp,
)

TITULO = "Trocar o selo mecânico da bomba"
TIPO_EVIDENCIA = "evidencia_adicionada"


# -- Material de teste -----------------------------------------------------------


def _bloco_tiff(data: str) -> bytes:
    """Monta o bloco TIFF de um EXIF com o ``DateTimeOriginal`` informado.

    Só o essencial: cabeçalho, o ponteiro para a IFD do EXIF e a tag da data
    como texto ASCII. Nada de GPS, nada de dump — é o mínimo que o leitor do
    domínio precisa para reconhecer a data que a imagem declara.
    """
    texto = data.encode("ascii") + b"\x00"
    ifd0 = 8
    ifd_exif = 26
    texto_em = 44

    cabecalho = b"MM" + (42).to_bytes(2, "big") + ifd0.to_bytes(4, "big")
    entrada_ifd0 = (
        (0x8769).to_bytes(2, "big")
        + (4).to_bytes(2, "big")
        + (1).to_bytes(4, "big")
        + ifd_exif.to_bytes(4, "big")
    )
    bytes_ifd0 = (1).to_bytes(2, "big") + entrada_ifd0 + (0).to_bytes(4, "big")

    entrada_exif = (
        (0x9003).to_bytes(2, "big")
        + (2).to_bytes(2, "big")
        + len(texto).to_bytes(4, "big")
        + texto_em.to_bytes(4, "big")
    )
    bytes_exif = (1).to_bytes(2, "big") + entrada_exif + (0).to_bytes(4, "big")

    return cabecalho + bytes_ifd0 + bytes_exif + texto


def _jpeg_com_data(data="2026:10:30 08:12:00", nome="foto.jpg") -> SimpleUploadedFile:
    """Um JPEG com o segmento APP1 de EXIF trazendo a data informada."""
    carga = b"Exif\x00\x00" + _bloco_tiff(data)
    segmento = b"\xff\xe1" + (len(carga) + 2).to_bytes(2, "big") + carga
    conteudo = b"\xff\xd8" + segmento + b"\xff\xd9"
    return SimpleUploadedFile(nome, conteudo, content_type="image/jpeg")


def _jpeg_com_exif_ilegivel(nome="foto.jpg") -> SimpleUploadedFile:
    """Um JPEG com APP1 de EXIF, mas sem bloco TIFF legível."""
    carga = b"Exif\x00\x00" + b"isto nao e um bloco tiff"
    segmento = b"\xff\xe1" + (len(carga) + 2).to_bytes(2, "big") + carga
    conteudo = b"\xff\xd8" + segmento + b"\xff\xd9"
    return SimpleUploadedFile(nome, conteudo, content_type="image/jpeg")


def _plano(condominio, criado_por, **campos):
    """Um plano preventivo válido, com o que o teste quiser trocar."""
    base = {
        "titulo": "Revisão da bomba de recalque",
        "descricao": "Revisar o conjunto e trocar o selo mecânico.",
        "frequencia_unidade": FrequenciaUnidade.MESES,
        "frequencia_intervalo": 3,
        "proxima_data_prevista": date(2026, 11, 15),
        "criado_por": criado_por,
    }
    base.update(campos)
    plano = PlanoPreventivo(condominio=condominio, **base)
    plano.full_clean()
    plano.save()
    return plano


class BaseDeEvidencias(TestCase):
    """Uma manutenção de teste e um storage em memória no lugar do provedor."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)

        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.manutencoes.evidencias.obter_provider", return_value=self.provider
        )
        self.patch_do_storage = patcher.start()
        self.addCleanup(patcher.stop)

    def _manutencao(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": TITULO,
        }
        base.update(campos)
        return criar_manutencao(**base)

    def _anexar(self, arquivo, manutencao=None, usuario=None, contexto=None):
        return adicionar_evidencia(
            manutencao=manutencao or self._manutencao(),
            arquivo=arquivo,
            usuario=usuario or self.gestao,
            contexto=contexto or ContextoEvidencia.GERAL,
        )


# -- Upload válido ---------------------------------------------------------------


class AnexoValidoTests(BaseDeEvidencias):
    """Foto, vídeo e documento entram: o conteúdo real é quem decide."""

    def test_foto_jpg_e_aceita(self):
        evidencia = self._anexar(jpg())
        self.assertEqual(evidencia.tipo_midia, TipoEvidenciaManutencao.FOTO)
        self.assertEqual(evidencia.mime_type, "image/jpeg")

    def test_foto_png_e_aceita(self):
        evidencia = self._anexar(png())
        self.assertEqual(evidencia.tipo_midia, TipoEvidenciaManutencao.FOTO)
        self.assertEqual(evidencia.mime_type, "image/png")

    def test_foto_webp_e_aceita(self):
        evidencia = self._anexar(webp())
        self.assertEqual(evidencia.tipo_midia, TipoEvidenciaManutencao.FOTO)
        self.assertEqual(evidencia.mime_type, "image/webp")

    def test_video_mp4_e_aceito(self):
        evidencia = self._anexar(mp4())
        self.assertEqual(evidencia.tipo_midia, TipoEvidenciaManutencao.VIDEO)
        self.assertEqual(evidencia.mime_type, "video/mp4")

    def test_video_webm_e_aceito(self):
        evidencia = self._anexar(webm())
        self.assertEqual(evidencia.tipo_midia, TipoEvidenciaManutencao.VIDEO)
        self.assertEqual(evidencia.mime_type, "video/webm")

    def test_documento_pdf_e_aceito(self):
        evidencia = self._anexar(pdf())
        self.assertEqual(evidencia.tipo_midia, TipoEvidenciaManutencao.DOCUMENTO)
        self.assertEqual(evidencia.mime_type, "application/pdf")

    def test_contexto_e_guardado(self):
        evidencia = self._anexar(png(), contexto=ContextoEvidencia.CONFERENCIA)
        self.assertEqual(evidencia.contexto, ContextoEvidencia.CONFERENCIA)


# -- Validação -------------------------------------------------------------------


class ValidacaoTests(BaseDeEvidencias):
    """O que não é prova aceita não vira registro nem objeto no bucket."""

    def test_arquivo_que_nao_e_prova_e_recusado(self):
        with self.assertRaises(ErroDeEvidencia):
            self._anexar(arquivo_qualquer())
        self.assertEqual(EvidenciaManutencao.objects.count(), 0)

    def test_extensao_enganosa_com_conteudo_alheio_e_recusada(self):
        # O nome diz .png, mas o conteúdo não é imagem nenhuma. O nome não decide.
        disfarcado = SimpleUploadedFile(
            "foto.png", b"isto nao e uma imagem", content_type="image/png"
        )
        with self.assertRaises(ErroDeEvidencia):
            self._anexar(disfarcado)

    def test_tipo_informado_divergente_do_conteudo_e_recusado(self):
        # Um JPEG de verdade, declarado como PNG pelo navegador: recusar é a
        # única resposta honesta, porque aceitar guardaria um tipo que não é.
        enganoso = SimpleUploadedFile("foto.png", CONTEUDO_JPG, content_type="image/png")
        with self.assertRaises(ErroDeEvidencia):
            self._anexar(enganoso)

    def test_arquivo_vazio_e_recusado(self):
        vazio = SimpleUploadedFile("vazio.png", b"", content_type="image/png")
        with self.assertRaises(ErroDeEvidencia):
            self._anexar(vazio)

    @override_settings(MANUTENCOES_MAX_FOTO_BYTES=8)
    def test_foto_acima_do_limite_e_recusada(self):
        with self.assertRaises(ErroDeEvidencia):
            self._anexar(png())
        self.assertEqual(EvidenciaManutencao.objects.count(), 0)

    @override_settings(MANUTENCOES_MAX_VIDEO_BYTES=8)
    def test_video_acima_do_limite_e_recusado(self):
        with self.assertRaises(ErroDeEvidencia):
            self._anexar(mp4())

    @override_settings(MANUTENCOES_MAX_DOCUMENTO_BYTES=8)
    def test_documento_acima_do_limite_e_recusado(self):
        with self.assertRaises(ErroDeEvidencia):
            self._anexar(pdf())

    def test_validar_evidencia_devolve_o_tipo_real(self):
        _, mime, extensao, tipo = validar_evidencia(png())
        self.assertEqual(mime, "image/png")
        self.assertEqual(extensao, "png")
        self.assertEqual(tipo, TipoEvidenciaManutencao.FOTO)

    def test_arquivo_nenhum_e_recusado(self):
        with self.assertRaises(ErroDeEvidencia):
            validar_evidencia(None)


# -- Data informada pela imagem --------------------------------------------------


class MetadadosTests(BaseDeEvidencias):
    """A captura só existe quando há uma fonte que a sustente."""

    def test_imagem_com_exif_registra_a_data_informada(self):
        evidencia = self._anexar(_jpeg_com_data("2026:10:30 08:12:00"))
        self.assertEqual(
            evidencia.capturada_em,
            timezone.make_aware(datetime(2026, 10, 30, 8, 12, 0)),
        )
        self.assertEqual(evidencia.origem_captura, OrigemCaptura.EXIF)

    def test_imagem_sem_exif_nao_inventa_horario(self):
        evidencia = self._anexar(png())
        self.assertIsNone(evidencia.capturada_em)
        self.assertEqual(evidencia.origem_captura, OrigemCaptura.NAO_IDENTIFICADA)

    def test_exif_ilegivel_nao_vira_data(self):
        evidencia = self._anexar(_jpeg_com_exif_ilegivel())
        self.assertIsNone(evidencia.capturada_em)
        self.assertEqual(evidencia.origem_captura, OrigemCaptura.NAO_IDENTIFICADA)

    def test_exif_com_texto_invalido_nao_vira_data(self):
        evidencia = self._anexar(_jpeg_com_data("nao e uma data"))
        self.assertIsNone(evidencia.capturada_em)
        self.assertEqual(evidencia.origem_captura, OrigemCaptura.NAO_IDENTIFICADA)

    def test_video_e_documento_nao_informam_captura(self):
        for arquivo in (mp4(), pdf()):
            evidencia = self._anexar(arquivo)
            self.assertIsNone(evidencia.capturada_em)
            self.assertEqual(evidencia.origem_captura, OrigemCaptura.NAO_IDENTIFICADA)

    def test_data_da_captura_le_so_o_datetimeoriginal(self):
        momento = data_da_captura(_jpeg_com_data("2024:03:01 19:45:10").read())
        self.assertEqual(
            momento, timezone.make_aware(datetime(2024, 3, 1, 19, 45, 10))
        )

    def test_enviada_em_e_sempre_escrita_pelo_servidor(self):
        antes = timezone.now()
        evidencia = self._anexar(png())
        self.assertIsNotNone(evidencia.enviada_em)
        self.assertGreaterEqual(evidencia.enviada_em, antes)
        self.assertEqual(evidencia.enviada_por, self.gestao)


# -- Hash e idempotência ---------------------------------------------------------


class HashIdempotenciaTests(BaseDeEvidencias):
    """O mesmo conteúdo não entra duas vezes na mesma manutenção."""

    def test_sha256_do_conteudo(self):
        evidencia = self._anexar(png())
        esperado = hashlib.sha256(CONTEUDO_PNG).hexdigest()
        self.assertEqual(evidencia.sha256, esperado)

    def test_tamanho_do_conteudo(self):
        evidencia = self._anexar(png())
        self.assertEqual(evidencia.tamanho_bytes, len(CONTEUDO_PNG))

    def test_mesmo_arquivo_na_mesma_manutencao_nao_duplica(self):
        manutencao = self._manutencao()
        primeira = self._anexar(png(), manutencao=manutencao)
        segunda = self._anexar(png(), manutencao=manutencao)

        self.assertEqual(primeira.pk, segunda.pk)
        self.assertEqual(EvidenciaManutencao.objects.count(), 1)

    def test_reenvio_nao_cria_evento_duplicado(self):
        manutencao = self._manutencao()
        self._anexar(png(), manutencao=manutencao)
        self._anexar(png(), manutencao=manutencao)

        eventos = HistoricoManutencao.objects.filter(
            manutencao=manutencao, tipo_evento=TIPO_EVIDENCIA
        )
        self.assertEqual(eventos.count(), 1)

    def test_mesmo_arquivo_em_outra_manutencao_e_permitido(self):
        self._anexar(png(), manutencao=self._manutencao())
        self._anexar(png(), manutencao=self._manutencao())
        self.assertEqual(EvidenciaManutencao.objects.count(), 2)

    def test_conteudos_diferentes_nao_se_confundem(self):
        manutencao = self._manutencao()
        self._anexar(png(marca="a"), manutencao=manutencao)
        self._anexar(png(marca="b"), manutencao=manutencao)
        self.assertEqual(EvidenciaManutencao.objects.count(), 2)


# -- Onde o arquivo fica ---------------------------------------------------------


class StorageTests(BaseDeEvidencias):
    """O arquivo vai para o bucket privado, com chave feita só de identificadores."""

    def test_arquivo_e_enviado_e_chave_nao_usa_o_nome(self):
        evidencia = self._anexar(png())
        self.assertIn(evidencia.storage_key, self.provider.objetos)
        self.assertEqual(self.provider.objetos[evidencia.storage_key], CONTEUDO_PNG)
        self.assertNotIn("evidencia.png", evidencia.storage_key)
        self.assertEqual(evidencia.storage_provider, self.provider.nome)

    def test_nome_com_diretorio_nao_escapa_da_pasta(self):
        # Um nome com caminho é só rótulo; a chave do objeto não depende dele.
        travessia = SimpleUploadedFile(
            "..\\..\\etc\\passwd.png", CONTEUDO_PNG, content_type="image/png"
        )
        evidencia = self._anexar(travessia)
        self.assertEqual(evidencia.nome_original, "passwd.png")
        self.assertNotIn("..", evidencia.storage_key)

    def test_falha_de_armazenamento_nao_deixa_registro(self):
        self.provider.falhar_ao_enviar = True
        with self.assertRaises(ErroDeStorage):
            self._anexar(png())
        self.assertEqual(EvidenciaManutencao.objects.count(), 0)
        self.assertEqual(
            HistoricoManutencao.objects.filter(tipo_evento=TIPO_EVIDENCIA).count(), 0
        )

    def test_falha_no_envio_nao_tenta_apagar_objeto(self):
        """Sem envio bem-sucedido não há objeto a desfazer."""
        self.provider.falhar_ao_enviar = True
        with self.assertRaises(ErroDeStorage):
            self._anexar(png())

        self.assertEqual(self.provider.removidos, [])


# -- Banco e storage não cabem na mesma transação --------------------------------


class CompensacaoDeStorageTests(BaseDeEvidencias):
    """Uma falha depois do envio não deixa objeto órfão nem apaga o que já havia.

    Não existe transação que cubra banco e bucket: quando a gravação falha
    depois do envio, o arquivo recém-criado é desfeito. Só o objeto desta
    tentativa é removido — o que já existia por idempotência permanece.
    """

    def test_falha_ao_gravar_desfaz_o_arquivo_enviado(self):
        with patch.object(
            EvidenciaManutencao, "save", side_effect=DatabaseError("banco fora")
        ):
            with self.assertRaises(DatabaseError):
                self._anexar(png())

        self.assertEqual(EvidenciaManutencao.objects.count(), 0)
        self.assertEqual(self.provider.objetos, {})
        self.assertEqual(len(self.provider.removidos), 1)

    def test_falha_ao_registrar_o_historico_desfaz_o_arquivo_enviado(self):
        with patch(
            "apps.manutencoes.evidencias._registrar",
            side_effect=DatabaseError("histórico fora"),
        ):
            with self.assertRaises(DatabaseError):
                self._anexar(png())

        self.assertEqual(EvidenciaManutencao.objects.count(), 0)
        self.assertEqual(
            HistoricoManutencao.objects.filter(tipo_evento=TIPO_EVIDENCIA).count(), 0
        )
        self.assertEqual(self.provider.objetos, {})
        self.assertEqual(len(self.provider.removidos), 1)

    def test_reenvio_idempotente_nao_apaga_o_arquivo_existente(self):
        manutencao = self._manutencao()
        primeira = self._anexar(png(), manutencao=manutencao)
        self.assertIn(primeira.storage_key, self.provider.objetos)

        segunda = self._anexar(png(), manutencao=manutencao)

        self.assertEqual(primeira.pk, segunda.pk)
        self.assertIn(primeira.storage_key, self.provider.objetos)
        self.assertEqual(self.provider.removidos, [])

    def test_falha_na_limpeza_nao_mascara_a_causa(self):
        """O erro que importa sobe; a falha de limpeza só fica registrada."""
        self.provider.falhar_ao_remover = True

        with patch.object(
            EvidenciaManutencao, "save", side_effect=DatabaseError("banco fora")
        ):
            with self.assertLogs("apps.manutencoes.evidencias", level="ERROR") as diario:
                with self.assertRaises(DatabaseError):
                    self._anexar(png())

        self.assertEqual(len(self.provider.removidos), 1)
        self.assertEqual(EvidenciaManutencao.objects.count(), 0)
        self.assertIn("armazenamento", " ".join(diario.output))


# -- Isolamento ------------------------------------------------------------------


class IsolamentoTests(BaseDeEvidencias):
    """Anexar é do condomínio da manutenção e de quem responde por ela."""

    def test_gestao_de_outro_condominio_nao_anexa(self):
        outro = criar_condominio("Beta")
        forasteiro = criar_pessoa("outro@exemplo.test", "Forasteiro")
        criar_vinculo(forasteiro, outro, papel=Papel.SINDICA)

        with self.assertRaises(ErroDeEvidencia):
            self._anexar(png(), usuario=forasteiro)

    def test_usuario_sem_papel_no_modulo_nao_anexa(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)

        with self.assertRaises(ErroDeEvidencia):
            self._anexar(png(), usuario=morador)

    def test_operacional_nao_responsavel_nao_anexa(self):
        operacional = criar_pessoa("oper@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)

        with self.assertRaises(ErroDeEvidencia):
            self._anexar(png(), usuario=operacional)

    def test_operacional_responsavel_anexa(self):
        operacional = criar_pessoa("oper@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        manutencao = self._manutencao(responsavel=operacional)

        evidencia = self._anexar(png(), manutencao=manutencao, usuario=operacional)
        self.assertEqual(evidencia.enviada_por, operacional)
        self.assertEqual(evidencia.manutencao, manutencao)

    def test_supervisora_anexa(self):
        supervisora = criar_pessoa("super@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)

        evidencia = self._anexar(png(), usuario=supervisora)
        self.assertEqual(evidencia.enviada_por, supervisora)


# -- Histórico e estado ----------------------------------------------------------


class HistoricoEEstadoTests(BaseDeEvidencias):
    """Anexar é registrado na memória da manutenção e não decide nada."""

    def test_evento_de_evidencia_e_registrado(self):
        manutencao = self._manutencao()
        evidencia = self._anexar(png(), manutencao=manutencao)

        evento = manutencao.historico.get(tipo_evento=TIPO_EVIDENCIA)
        self.assertEqual(evento.usuario, self.gestao)
        self.assertIn(evidencia.nome_original, evento.descricao)
        self.assertEqual(evento.dados_novos["mime"], "image/png")
        self.assertEqual(evento.dados_novos["sha256"], evidencia.sha256)

    def test_descricao_do_evento_nao_expoe_identificador_tecnico(self):
        manutencao = self._manutencao()
        evidencia = self._anexar(png(), manutencao=manutencao)
        evento = manutencao.historico.get(tipo_evento=TIPO_EVIDENCIA)
        self.assertNotIn(str(evidencia.pk), evento.descricao)

    def test_anexar_nao_altera_a_situacao(self):
        manutencao = self._manutencao()
        antes = (
            manutencao.status,
            manutencao.status_conferencia,
            manutencao.prioridade,
        )
        self._anexar(png(), manutencao=manutencao)

        manutencao.refresh_from_db()
        self.assertEqual(
            (
                manutencao.status,
                manutencao.status_conferencia,
                manutencao.prioridade,
            ),
            antes,
        )
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)

    def test_anexar_nao_conclui_nem_aprova(self):
        manutencao = self._manutencao()
        self._anexar(_jpeg_com_data(), manutencao=manutencao)

        manutencao.refresh_from_db()
        self.assertIsNone(manutencao.data_conclusao)
        self.assertNotEqual(manutencao.status_conferencia, StatusConferencia.APROVADA)


# -- Compatibilidade -------------------------------------------------------------


class CompatibilidadeTests(BaseDeEvidencias):
    """A prova acompanha os fluxos de sempre e não os substitui."""

    def test_corretiva_segue_ate_a_conclusao(self):
        manutencao = self._manutencao()
        self._anexar(png(), manutencao=manutencao)

        registrar_execucao(
            manutencao=manutencao,
            usuario=self.gestao,
            descricao_servico="Trocado o selo.",
        )
        concluir_manutencao(
            manutencao=manutencao,
            usuario=self.gestao,
            descricao_servico="Trocado o selo.",
        )

        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.CONCLUIDA)

    def test_conferencia_continua_funcionando_com_evidencia(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        manutencao = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)
        self.assertEqual(manutencao.status_conferencia, StatusConferencia.PENDENTE)

        self._anexar(
            png(), manutencao=manutencao, contexto=ContextoEvidencia.CONFERENCIA
        )
        manutencao = registrar_execucao(
            manutencao=manutencao,
            usuario=self.gestao,
            descricao_servico="Serviço feito.",
        )
        manutencao = aprovar_conferencia(manutencao=manutencao, usuario=self.gestao)
        concluir_manutencao(
            manutencao=manutencao,
            usuario=self.gestao,
            descricao_servico="Serviço feito.",
        )

        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status_conferencia, StatusConferencia.APROVADA)
        self.assertEqual(manutencao.status, StatusManutencao.CONCLUIDA)

    def test_preventiva_aceita_evidencia(self):
        plano = _plano(self.condominio, self.gestao)
        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)
        self.assertEqual(ocorrencia.tipo, TipoManutencao.PREVENTIVA)

        evidencia = self._anexar(png(), manutencao=ocorrencia)
        self.assertEqual(evidencia.manutencao, ocorrencia)

"""A foto da portaria: a prova opcional de uma chegada registrada.

A foto pertence ao acesso que já existe e serve à conferência de quem chegou —
não abre nem encerra a passagem, não decide destino. Estes testes prendem
exatamente isso: a chegada sem foto continua funcionando, a foto válida entra e
deixa a referência no acesso, o que não é imagem é recusado sem gravar nada, e
a chave não carrega nome de arquivo nem URL.

Nenhum teste fala com armazenamento de verdade: o provedor é substituído por um
storage em memória, como nos balanços, nas demandas e nas manutenções. Assim o
que se verifica é o que a portaria faz com a foto, não o que o bucket faria com
ela.
"""
from unittest.mock import patch

from django.test import TestCase

from apps.condominios.constants import Papel
from apps.core.tests.apoio import (
    CONTEUDO_PNG,
    StorageDeMentira,
    foto,
    pdf,
    png,
)
from apps.demandas.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo
from apps.portaria.fotos import (
    ErroDeFoto,
    acesso_temporario,
    guardar_foto,
    montar_chave,
    validar_foto,
)
from apps.portaria.models import AcessoPortaria, AutorizacaoPortaria
from apps.portaria.tests.apoio import criar_acesso


class BaseDeFotos(TestCase):
    """Uma chegada de teste e um storage em memória no lugar do provedor."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.porteiro = criar_pessoa("portaria@exemplo.test", "Porteiro")
        criar_vinculo(self.porteiro, self.condominio, papel=Papel.SINDICA)

        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.portaria.fotos.obter_provider", return_value=self.provider
        )
        self.patch_do_storage = patcher.start()
        self.addCleanup(patcher.stop)

    def _acesso(self, **campos):
        return criar_acesso(self.condominio, self.porteiro, **campos)


# -- Foto opcional ---------------------------------------------------------------


class FotoOpcionalTests(BaseDeFotos):
    """A chegada sem foto continua funcionando: nada é exigido nem gravado."""

    def test_sem_arquivo_nao_faz_nada(self):
        acesso = self._acesso()

        guardar_foto(acesso=acesso, arquivo=None)

        acesso.refresh_from_db()
        self.assertEqual(acesso.foto_storage_key, "")
        self.assertEqual(acesso.foto_storage_provider, "")
        self.assertEqual(acesso.foto_tamanho_bytes, 0)
        self.assertEqual(self.provider.objetos, {})

    def test_acesso_nasce_sem_referencia_de_foto(self):
        acesso = self._acesso()
        self.assertEqual(acesso.foto_storage_key, "")
        self.assertEqual(acesso.foto_mime_type, "")


# -- Foto válida -----------------------------------------------------------------


class FotoValidaTests(BaseDeFotos):
    """A imagem de verdade entra: o conteúdo real é quem decide."""

    def test_foto_valida_grava_a_referencia(self):
        acesso = self._acesso()

        guardar_foto(acesso=acesso, arquivo=png())

        acesso.refresh_from_db()
        self.assertEqual(acesso.foto_storage_provider, self.provider.nome)
        self.assertEqual(acesso.foto_storage_bucket, self.provider.bucket)
        self.assertEqual(acesso.foto_mime_type, "image/png")
        # O tamanho guardado é o do que foi enviado — a imagem marcada —, e não
        # o do arquivo original: a identificação visual é gravada antes de subir.
        self.assertGreater(acesso.foto_tamanho_bytes, 0)
        self.assertEqual(
            acesso.foto_tamanho_bytes,
            len(self.provider.objetos[acesso.foto_storage_key]),
        )

    def test_objeto_fica_no_storage(self):
        acesso = self._acesso()

        guardar_foto(acesso=acesso, arquivo=png())

        acesso.refresh_from_db()
        self.assertIn(acesso.foto_storage_key, self.provider.objetos)
        guardado = self.provider.objetos[acesso.foto_storage_key]
        # Segue um PNG de verdade (a identificação visual é desenhada sobre a
        # imagem, não troca o formato) e já não é o arquivo cru que subiu.
        self.assertTrue(guardado.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertNotEqual(guardado, CONTEUDO_PNG)

    def test_validar_foto_devolve_o_tipo_real(self):
        _, mime, extensao = validar_foto(png())
        self.assertEqual(mime, "image/png")
        self.assertEqual(extensao, "png")


# -- Recusa ----------------------------------------------------------------------


class RecusaTests(BaseDeFotos):
    """O que não é imagem aceita não vira referência nem objeto no bucket."""

    def test_arquivo_que_nao_e_imagem_e_recusado(self):
        acesso = self._acesso()

        with self.assertRaises(ErroDeFoto):
            guardar_foto(acesso=acesso, arquivo=pdf())

        acesso.refresh_from_db()
        self.assertEqual(acesso.foto_storage_key, "")
        self.assertEqual(acesso.foto_storage_provider, "")
        self.assertEqual(self.provider.objetos, {})

    def test_tipo_informado_divergente_do_conteudo_e_recusado(self):
        # Um PNG de verdade, declarado como JPEG pelo navegador: recusar é a
        # única resposta honesta, porque aceitar guardaria um tipo que não é.
        enganoso = foto(nome="foto.jpg", conteudo=CONTEUDO_PNG, content_type="image/jpeg")
        acesso = self._acesso()

        with self.assertRaises(ErroDeFoto):
            guardar_foto(acesso=acesso, arquivo=enganoso)

        acesso.refresh_from_db()
        self.assertEqual(acesso.foto_storage_key, "")

    def test_falha_no_storage_recusa_sem_gravar(self):
        self.provider.falhar_ao_enviar = True
        acesso = self._acesso()

        with self.assertRaises(ErroDeFoto):
            guardar_foto(acesso=acesso, arquivo=png())

        acesso.refresh_from_db()
        self.assertEqual(acesso.foto_storage_key, "")
        self.assertEqual(self.provider.objetos, {})


# -- A chave do objeto -----------------------------------------------------------


class ChaveDoObjetoTests(BaseDeFotos):
    """A chave é feita só de identificadores — sem nome de arquivo, sem URL."""

    def test_chave_usa_identificadores(self):
        acesso = self._acesso()

        guardar_foto(acesso=acesso, arquivo=png())

        acesso.refresh_from_db()
        self.assertEqual(
            acesso.foto_storage_key,
            f"portaria/{self.condominio.pk}/{acesso.pk}.png",
        )
        self.assertEqual(
            montar_chave(condominio=self.condominio, acesso=acesso, extensao="png"),
            acesso.foto_storage_key,
        )

    def test_chave_nao_carrega_url_nem_nome_do_arquivo(self):
        acesso = self._acesso()

        guardar_foto(acesso=acesso, arquivo=png(nome="retrato do visitante.png"))

        acesso.refresh_from_db()
        self.assertNotIn("http://", acesso.foto_storage_key)
        self.assertNotIn("https://", acesso.foto_storage_key)
        self.assertNotIn("retrato do visitante", acesso.foto_storage_key)


# -- Onde a foto mora ------------------------------------------------------------


class DonoDaFotoTests(TestCase):
    """A foto pertence ao acesso que aconteceu — não à autorização que o previu.

    O acesso é o fato: alguém passou. A autorização é a intenção que o antecedeu
    e pode nem existir. Guardar a imagem no par errado tiraria a foto de quem
    entrou e a daria a quem apenas esperava.
    """

    def test_os_campos_da_foto_estao_no_acesso(self):
        campos = {campo.name for campo in AcessoPortaria._meta.get_fields()}

        self.assertIn("foto_storage_key", campos)
        self.assertIn("foto_storage_provider", campos)
        self.assertIn("foto_tamanho_bytes", campos)

    def test_a_autorizacao_nao_guarda_foto(self):
        campos = {campo.name for campo in AutorizacaoPortaria._meta.get_fields()}

        self.assertNotIn("foto_storage_key", campos)
        self.assertNotIn("foto_mime_type", campos)


# -- Substituir ------------------------------------------------------------------


class SubstituicaoTests(BaseDeFotos):
    """Trocar a foto sobrepõe a mesma referência: sem objeto órfão no bucket."""

    def test_substituir_a_foto_nao_deixa_objeto_orfao(self):
        acesso = self._acesso()

        guardar_foto(acesso=acesso, arquivo=png(nome="primeira.png"))
        guardar_foto(acesso=acesso, arquivo=png(nome="segunda.png"))

        acesso.refresh_from_db()
        # A chave é determinística pelo acesso: a segunda gravação cobre a
        # primeira no mesmo objeto, e o bucket fica com um só.
        self.assertEqual(len(self.provider.objetos), 1)
        self.assertEqual(list(self.provider.objetos), [acesso.foto_storage_key])


# -- Acesso temporário -----------------------------------------------------------


class AcessoTemporarioTests(BaseDeFotos):
    """O endereço é gerado sob demanda — nunca guardado."""

    def test_devolve_acesso_quando_ha_foto(self):
        acesso = self._acesso()
        guardar_foto(acesso=acesso, arquivo=png())

        endereco = acesso_temporario(acesso, validade_segundos=30)

        self.assertIn(acesso.foto_storage_key, endereco)
        self.assertEqual(self.provider.assinaturas[-1], (acesso.foto_storage_key, 30))

    def test_recusa_quando_nao_ha_foto(self):
        acesso = self._acesso()

        with self.assertRaises(ErroDeFoto):
            acesso_temporario(acesso)

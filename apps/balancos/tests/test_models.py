"""Testes do domínio de balanços mensais.

Todos os dados são genéricos: Condomínio A, Condomínio B, Usuário Gestão e
Documento A. Nunca se usa nome, e-mail, valor ou arquivo real.
"""
import datetime
import hashlib
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import PROTECT, SET_NULL
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from apps.balancos.constants import (
    LayoutBalanco,
    ProcessamentoBalanco,
    StatusBalanco,
)
from apps.balancos.models import BalancoMensal, DocumentoBalanco
from apps.condominios.models import Condominio

# Hash genérico do "Documento A". Serve para exercitar o formato, não um
# arquivo real.
HASH_DOCUMENTO_A = hashlib.sha256(b"Documento A").hexdigest()

AGOSTO = datetime.date(2026, 8, 1)
SETEMBRO = datetime.date(2026, 9, 1)


def criar_pessoa(email="gestao@exemplo.com.br", nome="Usuário Gestão"):
    """Cria uma pessoa de teste. Nunca usa dados reais."""
    return get_user_model().objects.create_user(
        email=email, password="senha-forte-123", full_name=nome
    )


def novo_balanco(condominio, criado_por, competencia=AGOSTO, **extra):
    """Monta um balanço em memória, pronto para a validação de domínio.

    O balanço existe a partir da confirmação da gestão, então por padrão ele já
    nasce validado, com quem confirmou e quando.
    """
    dados = {
        "condominio": condominio,
        "criado_por": criado_por,
        "competencia": competencia,
        "validado_por": criado_por,
        "validado_em": timezone.now(),
    }
    dados.update(extra)
    return BalancoMensal(**dados)


def criar_balanco(condominio, criado_por, competencia=AGOSTO, **extra):
    """Grava um balanço, sem acionar as validações de domínio."""
    balanco = novo_balanco(condominio, criado_por, competencia, **extra)
    balanco.save()
    return balanco


class BaseBalancoTests(TestCase):
    """Prepara o cenário mínimo reutilizado pelos demais testes."""

    def setUp(self):
        self.gestao = criar_pessoa()
        self.condominio_a = Condominio.objects.create(nome="Condomínio A")
        self.condominio_b = Condominio.objects.create(nome="Condomínio B")


class BalancoMensalTests(BaseBalancoTests):
    """O balanço mensal é o agregado canônico do documento do mês."""

    def test_cria_balanco_valido(self):
        balanco = criar_balanco(self.condominio_a, self.gestao)
        self.assertIsInstance(balanco.pk, uuid.UUID)
        self.assertEqual(balanco.status, StatusBalanco.VALIDADO)
        self.assertEqual(balanco.status_processamento, ProcessamentoBalanco.PENDENTE)
        self.assertEqual(balanco.layout_detectado, LayoutBalanco.DESCONHECIDO)
        self.assertEqual(balanco.versao, 1)
        self.assertIsNotNone(balanco.criado_em)
        self.assertIsNotNone(balanco.atualizado_em)
        balanco.full_clean()

    def test_balanco_nasce_confirmado(self):
        """A gestão confirma a importação antes de o mês valer."""
        balanco = criar_balanco(self.condominio_a, self.gestao)
        self.assertEqual(balanco.status, StatusBalanco.VALIDADO)
        self.assertEqual(balanco.validado_por, self.gestao)
        self.assertIsNotNone(balanco.validado_em)

    def test_competencia_armazenada_como_primeiro_dia_do_mes(self):
        """A competência é o mês, não um dia financeiro específico."""
        balanco = criar_balanco(self.condominio_a, self.gestao, competencia="2026-08-01")
        balanco.refresh_from_db()
        self.assertEqual(balanco.competencia, AGOSTO)
        self.assertEqual(balanco.competencia.day, 1)

    def test_rejeita_competencia_fora_do_primeiro_dia(self):
        balanco = novo_balanco(
            self.condominio_a,
            self.gestao,
            competencia=datetime.date(2026, 8, 17),
        )
        with self.assertRaises(ValidationError) as erro:
            balanco.full_clean()
        self.assertIn("competencia", erro.exception.error_dict)

    def test_rejeita_competencia_fora_do_primeiro_dia_no_banco(self):
        """A constraint protege mesmo quando clean() é ignorado."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                criar_balanco(
                    self.condominio_a,
                    self.gestao,
                    competencia=datetime.date(2026, 8, 17),
                )

    def test_versao_comeca_em_um(self):
        balanco = criar_balanco(self.condominio_a, self.gestao)
        self.assertEqual(balanco.versao, 1)

    def test_rejeita_versao_zero_ou_negativa(self):
        for invalida in (0, -1):
            with self.subTest(versao=invalida):
                balanco = novo_balanco(
                    self.condominio_a, self.gestao, versao=invalida
                )
                with self.assertRaises(ValidationError) as erro:
                    balanco.full_clean()
                self.assertIn("versao", erro.exception.error_dict)

    def test_permite_versao_dois_substituindo_a_um_da_mesma_competencia(self):
        versao_1 = criar_balanco(self.condominio_a, self.gestao, versao=1)
        versao_1.status = StatusBalanco.SUBSTITUIDO
        versao_1.save(update_fields=["status"])

        versao_2 = criar_balanco(
            self.condominio_a, self.gestao, versao=2, substitui=versao_1
        )

        self.assertEqual(BalancoMensal.objects.filter(competencia=AGOSTO).count(), 2)
        self.assertEqual(versao_2.substitui_id, versao_1.pk)
        self.assertEqual(versao_1.versoes_corrigidas.count(), 1)

    def test_impede_duplicar_a_mesma_versao(self):
        criar_balanco(self.condominio_a, self.gestao, versao=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                criar_balanco(self.condominio_a, self.gestao, versao=1)

    def test_apenas_uma_versao_em_vigor_por_competencia(self):
        """A versão em vigor é derivada: só uma pode estar fora de substituído."""
        criar_balanco(self.condominio_a, self.gestao, versao=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                criar_balanco(self.condominio_a, self.gestao, versao=2)

    def test_substituicao_dentro_do_mesmo_condominio(self):
        versao_1 = criar_balanco(self.condominio_a, self.gestao, versao=1)
        versao_1.status = StatusBalanco.SUBSTITUIDO
        versao_1.save(update_fields=["status"])

        versao_2 = novo_balanco(
            self.condominio_a, self.gestao, versao=2, substitui=versao_1
        )
        versao_2.full_clean()
        self.assertEqual(versao_2.substitui, versao_1)

    def test_rejeita_versao_nova_menor_que_a_substituida(self):
        versao_1 = criar_balanco(self.condominio_a, self.gestao, versao=1)
        versao_1.status = StatusBalanco.SUBSTITUIDO
        versao_1.save(update_fields=["status"])

        nova = novo_balanco(
            self.condominio_a, self.gestao, versao=1, substitui=versao_1
        )
        with self.assertRaises(ValidationError) as erro:
            nova.full_clean()
        self.assertIn("versao", erro.exception.error_dict)

    def test_impede_substituir_balanco_de_outro_condominio(self):
        de_b = criar_balanco(self.condominio_b, self.gestao, versao=1)
        de_a = novo_balanco(
            self.condominio_a, self.gestao, versao=2, substitui=de_b
        )
        with self.assertRaises(ValidationError) as erro:
            de_a.full_clean()
        self.assertIn("substitui", erro.exception.error_dict)

    def test_impede_substituir_competencia_diferente(self):
        de_agosto = criar_balanco(self.condominio_a, self.gestao, competencia=AGOSTO)
        de_setembro = novo_balanco(
            self.condominio_a,
            self.gestao,
            competencia=SETEMBRO,
            versao=2,
            substitui=de_agosto,
        )
        with self.assertRaises(ValidationError) as erro:
            de_setembro.full_clean()
        self.assertIn("substitui", erro.exception.error_dict)

    def test_impede_auto_substituicao(self):
        balanco = criar_balanco(self.condominio_a, self.gestao, versao=1)
        balanco.substitui = balanco
        with self.assertRaises(ValidationError) as erro:
            balanco.full_clean()
        self.assertIn("substitui", erro.exception.error_dict)

    def test_historico_nao_e_apagado_ao_substituir(self):
        versao_1 = criar_balanco(self.condominio_a, self.gestao, versao=1)
        versao_1.status = StatusBalanco.SUBSTITUIDO
        versao_1.save(update_fields=["status"])
        criar_balanco(self.condominio_a, self.gestao, versao=2, substitui=versao_1)
        self.assertTrue(BalancoMensal.objects.filter(pk=versao_1.pk).exists())

    def test_nomes_das_constraints_e_indices(self):
        nomes_constraints = {c.name for c in BalancoMensal._meta.constraints}
        self.assertIn("balanco_unico_por_competencia_e_versao", nomes_constraints)
        self.assertIn("balanco_em_vigor_unico_por_competencia", nomes_constraints)
        self.assertIn("balanco_versao_minima", nomes_constraints)
        self.assertIn("balanco_competencia_no_primeiro_dia", nomes_constraints)
        nomes_indices = {i.name for i in BalancoMensal._meta.indexes}
        self.assertIn("balanco_condominio_status_idx", nomes_indices)


class EstadosTests(BaseBalancoTests):
    """Os estados são poucos e separam o funcional do técnico."""

    def test_estados_funcionais_validos(self):
        self.assertEqual(
            set(StatusBalanco.values),
            {"validado", "publicado", "substituido"},
        )

    def test_estados_de_processamento_validos(self):
        self.assertEqual(
            set(ProcessamentoBalanco.values),
            {"pendente", "processando", "concluido", "erro"},
        )
        self.assertEqual(
            BalancoMensal._meta.get_field("status_processamento").choices,
            ProcessamentoBalanco.choices,
        )

    def test_layout_atual_canonico(self):
        balanco = criar_balanco(
            self.condominio_a,
            self.gestao,
            layout_detectado=LayoutBalanco.ATUAL_CANONICO,
        )
        balanco.refresh_from_db()
        self.assertEqual(balanco.layout_detectado, LayoutBalanco.ATUAL_CANONICO)

    def test_layout_legado(self):
        balanco = criar_balanco(
            self.condominio_a, self.gestao, layout_detectado=LayoutBalanco.LEGADO
        )
        balanco.refresh_from_db()
        self.assertEqual(balanco.layout_detectado, LayoutBalanco.LEGADO)

    def test_layout_desconhecido_por_padrao(self):
        balanco = criar_balanco(self.condominio_a, self.gestao)
        self.assertEqual(balanco.layout_detectado, LayoutBalanco.DESCONHECIDO)

    def test_validado_exige_quem_e_quando(self):
        balanco = BalancoMensal(
            condominio=self.condominio_a,
            criado_por=self.gestao,
            competencia=AGOSTO,
        )
        with self.assertRaises(ValidationError) as erro:
            balanco.full_clean()
        self.assertIn("validado_em", erro.exception.error_dict)
        self.assertIn("validado_por", erro.exception.error_dict)

    def test_validado_aceito_com_quem_e_quando(self):
        balanco = novo_balanco(self.condominio_a, self.gestao)
        balanco.full_clean()

    def test_um_validado_por_competencia(self):
        criar_balanco(self.condominio_a, self.gestao)
        self.assertEqual(
            BalancoMensal.objects.filter(
                status=StatusBalanco.VALIDADO, competencia=AGOSTO
            ).count(),
            1,
        )

    def test_um_publicado_por_competencia(self):
        criar_balanco(self.condominio_a, self.gestao, status=StatusBalanco.PUBLICADO)
        self.assertEqual(
            BalancoMensal.objects.filter(
                status=StatusBalanco.PUBLICADO, competencia=AGOSTO
            ).count(),
            1,
        )

    def test_nao_permite_duas_versoes_vigentes(self):
        """Validado e publicado disputam a mesma vaga: só uma fica em vigor."""
        criar_balanco(self.condominio_a, self.gestao, versao=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                criar_balanco(
                    self.condominio_a,
                    self.gestao,
                    versao=2,
                    status=StatusBalanco.PUBLICADO,
                )

    def test_substituido_e_validado_na_mesma_competencia(self):
        anterior = criar_balanco(self.condominio_a, self.gestao, versao=1)
        anterior.status = StatusBalanco.SUBSTITUIDO
        anterior.save(update_fields=["status"])

        vigente = criar_balanco(
            self.condominio_a, self.gestao, versao=2, substitui=anterior
        )

        self.assertEqual(vigente.status, StatusBalanco.VALIDADO)
        self.assertEqual(
            BalancoMensal.objects.filter(competencia=AGOSTO).count(), 2
        )

    def test_substituido_e_publicado_na_mesma_competencia(self):
        anterior = criar_balanco(self.condominio_a, self.gestao, versao=1)
        anterior.status = StatusBalanco.SUBSTITUIDO
        anterior.save(update_fields=["status"])

        vigente = criar_balanco(
            self.condominio_a,
            self.gestao,
            versao=2,
            status=StatusBalanco.PUBLICADO,
            substitui=anterior,
        )

        self.assertEqual(vigente.status, StatusBalanco.PUBLICADO)
        self.assertEqual(
            BalancoMensal.objects.filter(competencia=AGOSTO).count(), 2
        )

    def test_estados_tecnicos_independentes_dos_funcionais(self):
        """Ler o arquivo não aprova o mês, e aprovar não depende da leitura."""
        balanco = criar_balanco(self.condominio_a, self.gestao)
        balanco.status_processamento = ProcessamentoBalanco.ERRO
        balanco.save(update_fields=["status_processamento"])
        balanco.refresh_from_db()

        self.assertEqual(balanco.status_processamento, ProcessamentoBalanco.ERRO)
        self.assertEqual(balanco.status, StatusBalanco.VALIDADO)
        balanco.full_clean()


class DadosTests(BaseBalancoTests):
    """O que o sistema entendeu não se confunde com o que a gestão confirmou."""

    def test_schema_versao_persistido(self):
        balanco = criar_balanco(self.condominio_a, self.gestao)
        self.assertEqual(balanco.schema_versao, 1)

        outro = criar_balanco(
            self.condominio_a, self.gestao, competencia=SETEMBRO, schema_versao=2
        )
        outro.refresh_from_db()
        self.assertEqual(outro.schema_versao, 2)

    def test_extraido_e_validado_ficam_separados(self):
        balanco = criar_balanco(
            self.condominio_a,
            self.gestao,
            dados_extraidos={"receitas_total": "100,00"},
        )
        balanco.refresh_from_db()
        self.assertEqual(balanco.dados_extraidos, {"receitas_total": "100,00"})
        self.assertEqual(balanco.dados_validados, {})

    def test_confirmacao_nao_copia_extraido_automaticamente(self):
        balanco = criar_balanco(
            self.condominio_a, self.gestao, dados_extraidos={"a": 1}
        )
        balanco.dados_validados = {"a": 2}
        balanco.save(update_fields=["dados_validados"])
        balanco.refresh_from_db()
        self.assertEqual(balanco.dados_extraidos, {"a": 1})
        self.assertEqual(balanco.dados_validados, {"a": 2})

    def test_inconsistencias_e_resumo_iniciam_vazios(self):
        balanco = criar_balanco(self.condominio_a, self.gestao)
        self.assertEqual(balanco.inconsistencias, [])
        self.assertEqual(balanco.resumo, {})


class IsolamentoTests(BaseBalancoTests):
    """O condomínio é o limite de isolamento do domínio."""

    def test_balancos_nao_se_misturam_entre_condominios(self):
        criar_balanco(self.condominio_a, self.gestao)
        criar_balanco(self.condominio_b, self.gestao)

        self.assertEqual(
            BalancoMensal.objects.filter(condominio=self.condominio_a).count(), 1
        )
        self.assertEqual(
            BalancoMensal.objects.filter(condominio=self.condominio_b).count(), 1
        )

    def test_condominio_do_balanco_e_obrigatorio(self):
        campo = BalancoMensal._meta.get_field("condominio")
        self.assertFalse(campo.null)

    def test_balancos_aparecem_no_lado_do_condominio(self):
        criar_balanco(self.condominio_a, self.gestao)
        self.assertEqual(self.condominio_a.balancos.count(), 1)


class RelacoesCriticasTests(BaseBalancoTests):
    """Histórico financeiro não se apaga por remoção de cadastros."""

    def test_fk_usa_o_modelo_de_pessoa_do_projeto(self):
        self.assertIs(
            BalancoMensal._meta.get_field("criado_por").remote_field.model,
            get_user_model(),
        )
        self.assertIs(
            DocumentoBalanco._meta.get_field("enviado_por").remote_field.model,
            get_user_model(),
        )
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")

    def test_relacoes_de_preservacao_historica(self):
        self.assertIs(BalancoMensal._meta.get_field("condominio").remote_field.on_delete, PROTECT)
        self.assertIs(BalancoMensal._meta.get_field("criado_por").remote_field.on_delete, PROTECT)
        self.assertIs(BalancoMensal._meta.get_field("substitui").remote_field.on_delete, PROTECT)
        self.assertIs(BalancoMensal._meta.get_field("validado_por").remote_field.on_delete, SET_NULL)
        self.assertIs(DocumentoBalanco._meta.get_field("balanco").remote_field.on_delete, PROTECT)
        self.assertIs(DocumentoBalanco._meta.get_field("enviado_por").remote_field.on_delete, PROTECT)

    def test_condominio_com_balanco_nao_pode_ser_removido(self):
        criar_balanco(self.condominio_a, self.gestao)
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.condominio_a.delete()


class DocumentoBalancoTests(BaseBalancoTests):
    """O documento original é referência a um objeto em storage privado."""

    def setUp(self):
        super().setUp()
        self.balanco = criar_balanco(self.condominio_a, self.gestao)

    def criar_documento(self, **extra):
        dados = {
            "balanco": self.balanco,
            "nome_original": "Documento A.pdf",
            "storage_provider": "supabase",
            "storage_bucket": "balancos-privados",
            "storage_key": "condominio-a/2026-08/documento-a.pdf",
            "mime_type": "application/pdf",
            "tamanho_bytes": 1024,
            "sha256": HASH_DOCUMENTO_A,
            "enviado_por": self.gestao,
        }
        dados.update(extra)
        return DocumentoBalanco.objects.create(**dados)

    def test_documento_vinculado_ao_balanco(self):
        documento = self.criar_documento()
        self.assertIsInstance(documento.pk, uuid.UUID)
        self.assertEqual(documento.balanco, self.balanco)
        self.assertEqual(self.balanco.documentos.count(), 1)

    def test_hash_persistido(self):
        documento = self.criar_documento()
        documento.refresh_from_db()
        self.assertEqual(documento.sha256, HASH_DOCUMENTO_A)

    def test_hash_fora_do_formato_e_rejeitado(self):
        documento = self.criar_documento(sha256="abc")
        with self.assertRaises(ValidationError) as erro:
            documento.full_clean()
        self.assertIn("sha256", erro.exception.error_dict)

    def test_mesmo_hash_nao_repete_no_mesmo_balanco(self):
        self.criar_documento()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.criar_documento()

    def test_mesmo_hash_pode_existir_em_balancos_distintos(self):
        """O mesmo arquivo pode existir legitimamente em contextos distintos."""
        outro = criar_balanco(
            self.condominio_b, self.gestao, competencia=SETEMBRO
        )
        self.criar_documento()
        outro_documento = self.criar_documento(balanco=outro)
        self.assertEqual(outro_documento.sha256, HASH_DOCUMENTO_A)

    def test_storage_recusa_url_publica(self):
        documento = self.criar_documento(
            storage_key="https://exemplo.supabase.co/objeto.pdf"
        )
        with self.assertRaises(ValidationError) as erro:
            documento.full_clean()
        self.assertIn("storage_key", erro.exception.error_dict)

    def test_storage_recusa_url_assinada_no_bucket(self):
        documento = self.criar_documento(
            storage_bucket="https://exemplo.supabase.co/sign/documento-a"
        )
        with self.assertRaises(ValidationError) as erro:
            documento.full_clean()
        self.assertIn("storage_bucket", erro.exception.error_dict)

    def test_storage_aceita_apenas_referencia_do_objeto(self):
        documento = self.criar_documento()
        documento.full_clean()
        self.assertNotIn("http", documento.storage_key)

    def test_nomes_das_constraints_e_indices(self):
        nomes_constraints = {c.name for c in DocumentoBalanco._meta.constraints}
        self.assertIn("documento_unico_por_balanco_e_hash", nomes_constraints)
        self.assertIn("documento_tamanho_nao_negativo", nomes_constraints)
        nomes_indices = {i.name for i in DocumentoBalanco._meta.indexes}
        self.assertIn("documento_sha256_idx", nomes_indices)

    def test_documentos_aparecem_no_lado_do_balanco(self):
        self.criar_documento()
        self.assertEqual(list(self.balanco.documentos.all())[0].nome_original, "Documento A.pdf")

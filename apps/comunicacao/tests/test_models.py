"""O comunicado como ele é guardado: coerência da origem, situação e leitura.

O que se verifica aqui é a régua do próprio modelo — a origem que não pode
discordar do vínculo, a origem que não pode apontar para outro condomínio, e as
propriedades que as telas leem sem recalcular nada.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.condominios.constants import Papel

from .apoio import (
    criar_condominio,
    criar_demanda,
    criar_pessoa,
    criar_vinculo,
)
from ..constants import (
    ModeloComunicado,
    OrigemComunicado,
    PublicoComunicado,
    StatusComunicacao,
)
from ..models import Comunicacao


class ComunicacaoModelTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.autor = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.autor, self.condominio, papel=Papel.SINDICA)

    def _comunicacao(self, **campos):
        dados = {
            "condominio": self.condominio,
            "titulo": "Aviso de manutenção",
            "criado_por": self.autor,
        }
        dados.update(campos)
        return Comunicacao(**dados)

    def test_nasce_rascunho_e_sem_imagem(self):
        comunicacao = self._comunicacao()
        comunicacao.full_clean()
        comunicacao.save()

        self.assertEqual(comunicacao.status, StatusComunicacao.RASCUNHO)
        self.assertFalse(comunicacao.tem_imagem)
        self.assertFalse(comunicacao.esta_pronta)

    def test_origem_de_demanda_exige_o_vinculo_da_demanda(self):
        comunicacao = self._comunicacao(origem_tipo=OrigemComunicado.DEMANDA)
        with self.assertRaises(ValidationError) as capturado:
            comunicacao.full_clean()
        self.assertIn("origem_demanda", capturado.exception.message_dict)

    def test_origem_de_manutencao_exige_o_vinculo_da_manutencao(self):
        comunicacao = self._comunicacao(origem_tipo=OrigemComunicado.MANUTENCAO)
        with self.assertRaises(ValidationError) as capturado:
            comunicacao.full_clean()
        self.assertIn("origem_manutencao", capturado.exception.message_dict)

    def test_avulso_nao_pode_apontar_para_demanda(self):
        demanda = criar_demanda(self.condominio, self.autor)
        comunicacao = self._comunicacao(
            origem_tipo=OrigemComunicado.AVULSO, origem_demanda=demanda
        )
        with self.assertRaises(ValidationError) as capturado:
            comunicacao.full_clean()
        self.assertIn("origem_tipo", capturado.exception.message_dict)

    def test_origem_de_outro_condominio_e_recusada(self):
        outro = criar_condominio("Beta")
        demanda_alheia = criar_demanda(outro, self.autor)
        comunicacao = self._comunicacao(
            origem_tipo=OrigemComunicado.DEMANDA, origem_demanda=demanda_alheia
        )
        with self.assertRaises(ValidationError) as capturado:
            comunicacao.full_clean()
        self.assertIn("origem_demanda", capturado.exception.message_dict)

    def test_pronta_sem_arquivo_e_recusada_pela_restricao(self):
        comunicacao = self._comunicacao(status=StatusComunicacao.PRONTA)
        # A restrição "pronta tem arquivo" é do banco; o full_clean a antecipa
        # como uma recusa de validação, para a incoerência não esperar um INSERT.
        with self.assertRaises(ValidationError):
            comunicacao.full_clean()

    def test_publico_para_exibir_prefere_o_detalhe(self):
        comunicacao = self._comunicacao(
            publico=PublicoComunicado.BLOCO, publico_descricao="Bloco B, aptos 101 a 204"
        )
        self.assertEqual(comunicacao.publico_para_exibir, "Bloco B, aptos 101 a 204")

        sem_detalhe = self._comunicacao(publico=PublicoComunicado.TODOS)
        self.assertEqual(
            sem_detalhe.publico_para_exibir, PublicoComunicado.TODOS.label
        )

    def test_descricao_da_origem_nomeia_o_registro(self):
        demanda = criar_demanda(self.condominio, self.autor, titulo="Falta de água")
        comunicacao = self._comunicacao(
            origem_tipo=OrigemComunicado.DEMANDA, origem_demanda=demanda
        )
        self.assertIn("Falta de água", comunicacao.descricao_da_origem)

    def test_periodo_por_extenso_e_vazio_sem_data(self):
        comunicacao = self._comunicacao(modelo=ModeloComunicado.AVISO)
        self.assertEqual(comunicacao.periodo_por_extenso, "")

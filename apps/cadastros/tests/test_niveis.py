"""Onde cada lugar do prédio cai — e o desenho que depende dessa resposta.

O nível de um local sai do nome que a gestão já usa: "Cobertura · salão",
"10º andar", "G1 (rua)". É uma leitura pura, sem banco, e é ela que o desenho
do prédio consulta por nível. Por isso o desenho entra aqui como prova: se ele
trouxer um ``data-nivel`` que o helper não conhece, aquele nível nunca acenderia.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from apps.cadastros.niveis import (
    BAIXOS,
    COBERTURA,
    DUPLEX,
    G1,
    G4,
    NIVEIS,
    PLAYGROUND,
    PREDIO,
    nivel_do_local,
)

CAMINHO_DO_DESENHO = (
    Path(settings.BASE_DIR) / "templates" / "pages" / "predio" / "_predio.svg"
)


class NivelDoLocalTests(SimpleTestCase):
    """O nome do local vira nível, na ordem em que o prédio se apresenta."""

    def test_cobertura(self):
        self.assertEqual(nivel_do_local("Cobertura · salão de festas"), COBERTURA)

    def test_andar_duplex(self):
        self.assertEqual(nivel_do_local("10º andar"), DUPLEX)

    def test_andar_baixo(self):
        self.assertEqual(nivel_do_local("3º andar"), BAIXOS)

    def test_garagem(self):
        self.assertEqual(nivel_do_local("G4 · Casa de bombas de recalque"), G4)

    def test_garagem_na_rua(self):
        self.assertEqual(nivel_do_local("G1 (rua)"), G1)

    def test_playground(self):
        self.assertEqual(nivel_do_local("Playground"), PLAYGROUND)

    def test_o_que_nao_tem_nivel_e_do_predio(self):
        self.assertEqual(nivel_do_local("Torre (todos os andares)"), PREDIO)


class DesenhoDoPredioTests(SimpleTestCase):
    """O desenho e o helper têm de falar dos mesmos níveis."""

    def test_todo_data_nivel_do_desenho_existe_no_helper(self):
        conteudo = CAMINHO_DO_DESENHO.read_text(encoding="utf-8")
        do_desenho = set(re.findall(r'data-nivel="([^"]+)"', conteudo))

        self.assertTrue(do_desenho, "O desenho não traz nenhum data-nivel.")
        self.assertFalse(
            do_desenho - set(NIVEIS),
            f"Níveis do desenho que o helper não conhece: {do_desenho - set(NIVEIS)}",
        )

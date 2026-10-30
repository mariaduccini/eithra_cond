"""Guardas de arquitetura da camada neutra de recorrência.

A recorrência é infraestrutura de domínio: quem tem uma regra que se repete
consome a mesma conta, e ninguém precisa depender de outro domínio para saber
quando algo volta a acontecer. Duas direções erradas são possíveis, e as duas
ficam fechadas aqui — Manutenções passando a ler Rotinas, e a camada neutra
passando a conhecer um domínio.

O teste lê o próprio código-fonte em vez de seguir o grafo de imports em tempo
de execução: é simples de propósito, e quebra no primeiro import que contrariar
a direção combinada.
"""
import ast

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


def _pacote(nome: str) -> Path:
    """A pasta de um app do projeto."""
    return Path(settings.BASE_DIR) / "apps" / nome


def _importados(caminho: Path) -> set[str]:
    """Os módulos citados pelos imports absolutos de um arquivo."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(apelido.name for apelido in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module)
    return nomes


class DirecaoDosImportsTests(SimpleTestCase):
    """Quem pode importar quem, e quem nunca pode."""

    def test_manutencoes_nao_importa_rotinas(self):
        """A preventiva não depende de Rotinas para saber quando volta."""
        ofensores = []

        for caminho in sorted(_pacote("manutencoes").rglob("*.py")):
            de_rotinas = {
                nome for nome in _importados(caminho) if nome.startswith("apps.rotinas")
            }
            if de_rotinas:
                ofensores.append(f"{caminho.name}: {sorted(de_rotinas)}")

        self.assertEqual(
            ofensores,
            [],
            "Manutenções passou a depender de Rotinas; a conta de recorrência "
            "vive na camada neutra, em apps.core.recorrencia.",
        )

    def test_camada_neutra_nao_importa_dominio_funcional(self):
        """A conta de calendário não conhece nenhum domínio do Eithra."""
        neutro = _pacote("core") / "recorrencia.py"
        de_dominio = sorted(
            nome
            for nome in _importados(neutro)
            if nome.startswith("apps.") and not nome.startswith("apps.core.")
        )

        self.assertEqual(
            de_dominio,
            [],
            "A camada neutra de recorrência passou a importar um domínio "
            "funcional; ela deve continuar valendo sozinha.",
        )

"""Guardas de arquitetura da camada neutra.

A recorrência e a leitura de arquivo são infraestrutura de domínio: quem tem uma
regra que se repete, ou quem recebe um arquivo, consome a mesma conta e ninguém
precisa depender de outro domínio para isso. Duas direções erradas são
possíveis, e as duas ficam fechadas aqui — um domínio passando a ler outro, e a
camada neutra passando a conhecer um domínio funcional.

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


def _ofensores(pacote: str, prefixo: str) -> list[str]:
    """Os arquivos de um app que importam algo de um prefixo proibido."""
    apontados = []
    for caminho in sorted(_pacote(pacote).rglob("*.py")):
        proibidos = sorted(
            nome for nome in _importados(caminho) if nome.startswith(prefixo)
        )
        if proibidos:
            apontados.append(f"{caminho.name}: {proibidos}")
    return apontados


def _de_dominio_funcional(caminho: Path) -> list[str]:
    """Os domínios funcionais do Eithra citados por um módulo neutro."""
    return sorted(
        nome
        for nome in _importados(caminho)
        if nome.startswith("apps.") and not nome.startswith("apps.core.")
    )


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

    def test_manutencoes_nao_importa_balancos(self):
        """A evidência de manutenção não lê o domínio de balanços."""
        self.assertEqual(
            _ofensores("manutencoes", "apps.balancos"),
            [],
            "Manutenções passou a depender de Balanços; o que é neutro — como o "
            "SHA-256 e a leitura do arquivo — vive na camada compartilhada, em "
            "apps.core.arquivos.",
        )

    def test_camada_neutra_de_arquivo_nao_importa_dominio_funcional(self):
        """A leitura de arquivo não conhece nenhum domínio do Eithra."""
        self.assertEqual(
            _de_dominio_funcional(_pacote("core") / "arquivos.py"),
            [],
            "A camada neutra de arquivo passou a importar um domínio funcional; "
            "ela deve continuar valendo sozinha.",
        )

    def test_camada_neutra_de_recorrencia_nao_importa_dominio_funcional(self):
        """A conta de calendário segue sem conhecer nenhum domínio do Eithra."""
        self.assertEqual(
            _de_dominio_funcional(_pacote("core") / "recorrencia.py"),
            [],
            "A camada neutra de recorrência passou a importar um domínio "
            "funcional; ela deve continuar valendo sozinha.",
        )

    def test_balancos_e_manutencoes_usam_o_mesmo_hash(self):
        """Há uma só implementação de SHA-256, e os dois domínios consomem dela."""
        from apps.balancos.services import calcular_hash as do_balanco
        from apps.core.arquivos import calcular_hash as do_core
        from apps.manutencoes.evidencias import calcular_hash as da_manutencao

        self.assertIs(do_balanco, do_core)
        self.assertIs(da_manutencao, do_core)

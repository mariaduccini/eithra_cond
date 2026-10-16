#!/usr/bin/env python
"""Utilitário de linha de comando do Eithra."""
import os
import sys


def main() -> None:
    """Executa comandos administrativos do Django."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - falha de ambiente
        raise ImportError(
            "Não foi possível importar o Django. Ative o ambiente virtual do "
            "projeto e instale as dependências de requirements.txt."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

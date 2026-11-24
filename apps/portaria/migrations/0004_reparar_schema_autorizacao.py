from django.db import migrations


CAMPOS_A_REPARAR = (
    "autorizador_nome",
    "meio_autorizacao",
    "resultado_registrado_em",
    "resultado_registrado_por",
)


def reparar_schema_autorizacao(apps, schema_editor):
    model = apps.get_model("portaria", "AutorizacaoPortaria")
    table = model._meta.db_table
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        colunas_existentes = {
            coluna.name
            for coluna in connection.introspection.get_table_description(
                cursor,
                table,
            )
        }

    for nome_campo in CAMPOS_A_REPARAR:
        field = model._meta.get_field(nome_campo)

        if field.column not in colunas_existentes:
            schema_editor.add_field(model, field)
            colunas_existentes.add(field.column)


class Migration(migrations.Migration):

    dependencies = [
        ("portaria", "0003_reparar_autorizador_pessoa"),
    ]

    operations = [
        migrations.RunPython(
            reparar_schema_autorizacao,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

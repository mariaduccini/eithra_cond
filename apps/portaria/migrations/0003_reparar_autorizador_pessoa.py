from django.db import migrations


def reparar_autorizador_pessoa(apps, schema_editor):
    model = apps.get_model("portaria", "AutorizacaoPortaria")
    field = model._meta.get_field("autorizador_pessoa")

    connection = schema_editor.connection
    table = model._meta.db_table

    with connection.cursor() as cursor:
        colunas = {
            coluna.name
            for coluna in connection.introspection.get_table_description(cursor, table)
        }

    if field.column not in colunas:
        schema_editor.add_field(model, field)


class Migration(migrations.Migration):

    dependencies = [
        ("portaria", "0002_acessoportaria_instrucao_entrega_and_more"),
    ]

    operations = [
        migrations.RunPython(
            reparar_autorizador_pessoa,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

"""A administração do condomínio passa a se chamar Administradora.

Até aqui o papel de quem respondia pelo condomínio se chamava ``admin``
("Administração"). O nome se confundia com administração *do sistema*, que é
global e vive em ``is_staff``/``is_superuser``. O papel passa a se chamar
``administradora``, e o vínculo legado é convertido junto.

A conversão troca apenas o campo ``papel``: nenhum vínculo é apagado, nenhuma
conta é recriada, e condomínio, usuário e situação de cada acesso continuam
exatamente como estavam.

A conversão é deliberadamente irreversível. Depois de aplicada, o mesmo sistema
passa a criar vínculos legítimos de ``administradora`` que nunca vieram do
legado, e não há marcador que separe estes dos convertidos. Uma volta genérica
de ``administradora`` para ``admin`` rebaixaria também as Administradoras novas,
tomando por legado o que não é. Por isso a operação de dados não declara reverso
(``RunPython`` sem ``reverse_code``), e reverter a migration falha com
``IrreversibleError`` em vez de corromper o significado gravado.
"""
from django.db import migrations, models

# O nome antigo do papel de administração do condomínio.
PAPEL_LEGADO = "admin"
# O nome pelo qual o mesmo papel se chama agora.
PAPEL_ADMINISTRADORA = "administradora"


def administradora_no_lugar_da_administracao(apps, schema_editor):
    """Converte os vínculos de administração do condomínio para Administradora.

    Só o papel legado é alcançado: os demais vínculos ficam como estão.
    """
    Acesso = apps.get_model("condominios", "AcessoUsuarioCondominio")
    Acesso.objects.filter(papel=PAPEL_LEGADO).update(papel=PAPEL_ADMINISTRADORA)


class Migration(migrations.Migration):

    dependencies = [
        ("condominios", "0006_condominio_logo_storage_bucket_and_more"),
    ]

    operations = [
        # Sem ``reverse_code``: a operação de dados é irreversível de propósito.
        # O Django recusa o rollback com ``IrreversibleError`` em vez de converter
        # as Administradoras novas de volta ao nome legado.
        migrations.RunPython(administradora_no_lugar_da_administracao),
        migrations.AlterField(
            model_name="acessousuariocondominio",
            name="papel",
            field=models.CharField(
                choices=[
                    ("administradora", "Administradora"),
                    ("sindica", "Síndico(a)"),
                    ("supervisora", "Supervisão"),
                    ("operacional", "Operacional"),
                    ("porteiro", "Portaria"),
                    ("morador", "Morador(a)"),
                ],
                max_length=20,
                verbose_name="papel",
            ),
        ),
    ]

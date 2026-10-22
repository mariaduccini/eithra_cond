import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0001_initial"),
        ("demandas", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="demanda",
            name="local",
            field=models.ForeignKey(
                blank=True,
                help_text="Onde a demanda acontece. Pode ficar sem local definido.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="demandas",
                to="cadastros.localoperacional",
                verbose_name="Local",
            ),
        ),
    ]

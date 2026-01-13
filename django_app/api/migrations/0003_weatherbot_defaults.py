from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0002_weatherbot"),
    ]

    operations = [
        migrations.AddField(
            model_name="weathertask",
            name="use_default_backgrounds",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="weathertask",
            name="use_default_icons",
            field=models.BooleanField(default=True),
        ),
    ]

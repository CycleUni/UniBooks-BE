from django.db import migrations, models


class Migration(migrations.Migration):
    """Conversation.created_at, left empty for conversations that already exist.

    Added in two steps on purpose: AddField with auto_now_add fills every
    existing row with the time the migration ran, which would date all past
    conversations to today. A plain nullable column keeps them null — unknown —
    and the AlterField only changes what new rows get.
    """

    dependencies = [
        ('messaging', '0006_delete_message'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='created_at',
            field=models.DateTimeField(null=True),
        ),
        migrations.AlterField(
            model_name='conversation',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
    ]

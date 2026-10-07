from django.db import migrations, models


class Migration(migrations.Migration):
    """The push switch covers every kind of push now, not only chat messages.

    A rename, not remove + add: 0007 is already applied where push shipped, and
    anyone who turned the switch off keeps it off.
    """

    dependencies = [
        ('accounts', '0007_push_devices'),
    ]

    operations = [
        migrations.RenameField(
            model_name='user',
            old_name='notify_new_message_push',
            new_name='notify_push',
        ),
        migrations.AlterField(
            model_name='user',
            name='notify_push',
            field=models.BooleanField(default=True, help_text='Send push notifications to the browsers the user registered'),
        ),
    ]

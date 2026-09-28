from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class AddIndexConcurrentlyOnPostgres(AddIndexConcurrently):
    """CONCURRENTLY on PostgreSQL, so building the index never blocks writes
    to the listings table; a plain CREATE INDEX elsewhere (the SQLite the
    tests and local development run on), which has no such option."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != 'postgresql':
            return migrations.AddIndex.database_forwards(self, app_label, schema_editor, from_state, to_state)
        return super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != 'postgresql':
            return migrations.AddIndex.database_backwards(self, app_label, schema_editor, from_state, to_state)
        return super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ('listings', '0001_consolidated'),
    ]

    operations = [
        AddIndexConcurrentlyOnPostgres(
            model_name='listing',
            index=models.Index(fields=['region', 'status', 'id'], name='listing_region_status_id_idx'),
        ),
    ]

from django.db import migrations


class AddPostgresIndex(migrations.AddIndex):
    """AddIndex that builds only on PostgreSQL.

    For an index SQLite cannot express — a GIN trigram index on an
    expression, say — so the test database, which is SQLite, still migrates.
    The migration state records it either way.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == 'postgresql':
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == 'postgresql':
            super().database_backwards(app_label, schema_editor, from_state, to_state)

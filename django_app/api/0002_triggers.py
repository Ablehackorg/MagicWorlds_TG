from django.db import migrations

TRIGGER_SQL = """
-- Функция, отправляющая уведомления в канал tasks_changed
CREATE OR REPLACE FUNCTION notify_tasks_changed() RETURNS TRIGGER AS $$
DECLARE
    payload JSON;
BEGIN
    payload = json_build_object(
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'id', NEW.id
    );

    PERFORM pg_notify('tasks_changed', payload::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGERS = [
    # EntityPostTask
    ("api_entityposttask", "entityposttask_notify"),
    # TaskTime
    ("api_tasktime", "tasktime_notify"),
    # MainEntity
    ("api_mainentity", "mainentity_notify"),
]


def create_triggers(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    cursor.execute(TRIGGER_SQL)
    for table, name in TRIGGERS:
        # сначала дропнем, если уже есть
        cursor.execute(f"DROP TRIGGER IF EXISTS {name} ON {table};")
        cursor.execute(f"""
            CREATE TRIGGER {name}
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION notify_tasks_changed();
        """)


def drop_triggers(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for table, name in TRIGGERS:
        cursor.execute(f"DROP TRIGGER IF EXISTS {name} ON {table};")
    cursor.execute("DROP FUNCTION IF EXISTS notify_tasks_changed();")


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0001_initial"),  # замени на последнюю актуальную
    ]

    operations = [
        migrations.RunPython(create_triggers, drop_triggers),
    ]

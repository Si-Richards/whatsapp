from sqlalchemy import inspect, text

from app.database import engine


def ensure_phase2_schema() -> None:
    """Add Phase 2 columns to an existing POC database without destroying data."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "conversations" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    statements: list[str] = []

    if "assigned_agent_id" not in columns:
        statements.append("ALTER TABLE conversations ADD COLUMN assigned_agent_id INTEGER")
    if "is_archived" not in columns:
        statements.append(
            "ALTER TABLE conversations ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0"
        )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

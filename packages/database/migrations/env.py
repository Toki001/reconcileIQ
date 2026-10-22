from alembic import context
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import Base
from reconcileiq_database.session import build_engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = build_engine(get_settings())
    try:
        with engine.connect() as connection:
            sqlite = connection.dialect.name == "sqlite"
            if sqlite:
                # SQLite batch rebuilds require FK checks off on this maintenance connection.
                # Explicit BEGIN makes every DDL/backfill operation rollback together.
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
            try:
                with connection.begin():
                    if sqlite:
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                    context.configure(
                        connection=connection,
                        target_metadata=target_metadata,
                        compare_type=True,
                        transactional_ddl=True,
                    )
                    with context.begin_transaction():
                        context.run_migrations()
                    if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                        raise RuntimeError("Migration would leave invalid foreign key references")
            finally:
                if sqlite:
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    connection.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

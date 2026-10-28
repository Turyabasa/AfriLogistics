import asyncio
import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool
from app.main import Base
config = context.config
config.set_main_option('sqlalchemy.url', os.environ.get('DATABASE_URL', config.get_main_option('sqlalchemy.url')))
target_metadata = Base.metadata

def offline():
    context.configure(url=config.get_main_option('sqlalchemy.url'), target_metadata=target_metadata, literal_binds=True, dialect_opts={'paramstyle':'named'})
    with context.begin_transaction(): context.run_migrations()

async def online():
    engine = async_engine_from_config(config.get_section(config.config_ini_section), prefix='sqlalchemy.', poolclass=pool.NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(lambda connection: context.configure(connection=connection, target_metadata=target_metadata))
        await conn.run_sync(lambda connection: context.run_migrations())
    await engine.dispose()

if context.is_offline_mode(): offline()
else: asyncio.run(online())

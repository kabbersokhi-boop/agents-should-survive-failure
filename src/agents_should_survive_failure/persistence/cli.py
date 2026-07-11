"""Persistence lifecycle command line entry points."""

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from agents_should_survive_failure.persistence.seed import seed_database
from agents_should_survive_failure.policy import PolicyEmbeddingService
from agents_should_survive_failure.provider_factory import build_embedding_provider
from agents_should_survive_failure.settings import get_settings


async def _seed() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        await seed_database(engine)
    finally:
        await engine.dispose()


async def _reindex_policies() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            from sqlalchemy.ext.asyncio import AsyncSession

            session = AsyncSession(bind=connection)
            try:
                service = PolicyEmbeddingService(build_embedding_provider(settings))
                count = await service.reindex_all(session)
                await session.flush()
            finally:
                await session.close()
        print(f"Reindexed {count} policy document(s).")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_seed())


def reindex_main() -> None:
    asyncio.run(_reindex_policies())


if __name__ == "__main__":
    main()

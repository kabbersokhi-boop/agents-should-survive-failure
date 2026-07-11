"""Persistence lifecycle command line entry points."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents_should_survive_failure.evaluation import EvaluationRunner
from agents_should_survive_failure.persistence.seed import seed_database, seed_id
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


async def _evaluate_vendor_onboarding(idempotency_key: str) -> UUID:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            run = await EvaluationRunner().run_vendor_onboarding(
                session,
                requested_by_id=str(seed_id("user:demo-operator")),
                idempotency_key=idempotency_key,
            )
        print(f"Evaluation run {run.id} completed with status {run.status.value}.")
        return run.id
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_seed())


def reindex_main() -> None:
    asyncio.run(_reindex_policies())


def evaluate_main(idempotency_key: str) -> None:
    asyncio.run(_evaluate_vendor_onboarding(idempotency_key))


if __name__ == "__main__":
    main()

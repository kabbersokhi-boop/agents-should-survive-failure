"""Persistence lifecycle command line entry points."""

import asyncio
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from agents_should_survive_failure.dependencies import create_resources
from agents_should_survive_failure.evaluation import EvaluationRunner
from agents_should_survive_failure.evaluation_reports import export_reports
from agents_should_survive_failure.persistence.models import EvaluationStatus
from agents_should_survive_failure.persistence.seed import seed_database, seed_id
from agents_should_survive_failure.persistence.session import Database
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


async def _evaluate_vendor_onboarding(idempotency_key: str) -> tuple[UUID, EvaluationStatus]:
    settings = get_settings()
    resources = await create_resources(settings, lazy_temporal_client=False)
    try:
        run = await EvaluationRunner().run_production_vendor_onboarding(
            Database(resources.engine),
            resources.temporal_client,
            requested_by_id=seed_id("user:demo-operator"),
            idempotency_key=idempotency_key,
            fault_injection_enabled=settings.fault_injection_enabled,
        )
        print(f"Evaluation run {run.id} completed with status {run.status.value}.")
        return run.id, run.status
    finally:
        await resources.close()


def main() -> None:
    asyncio.run(_seed())


def reindex_main() -> None:
    asyncio.run(_reindex_policies())


def evaluate_main(idempotency_key: str) -> None:
    run_id, status = asyncio.run(_evaluate_vendor_onboarding(idempotency_key))
    if status is EvaluationStatus.FAILED:
        raise SystemExit(f"Evaluation run {run_id} failed production workflow evidence checks.")


async def _export_evaluation_reports(evaluation_run_id: UUID, output_directory: Path) -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        json_path, markdown_path = await export_reports(
            Database(engine), evaluation_run_id, output_directory
        )
    finally:
        await engine.dispose()
    print(f"Wrote evaluation reports: {json_path} and {markdown_path}")


def evaluation_report_main(
    evaluation_run_id: str, output_directory: str = "artifacts/evaluations"
) -> None:
    asyncio.run(_export_evaluation_reports(UUID(evaluation_run_id), Path(output_directory)))


if __name__ == "__main__":
    main()

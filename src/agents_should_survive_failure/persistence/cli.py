"""Persistence lifecycle command line entry points."""

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from agents_should_survive_failure.persistence.seed import seed_database
from agents_should_survive_failure.settings import get_settings


async def _seed() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        await seed_database(engine)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()

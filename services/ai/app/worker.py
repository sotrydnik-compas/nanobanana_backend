import asyncio
import logging

from arq import create_worker
from arq.connections import RedisSettings

from app.core.config import settings
from app.services.arq_queue import WORKER_FUNCTIONS, REDIS_SETTINGS
from app.core.logger import setup_logging

# Настраиваем логирование
setup_logging()
logger = logging.getLogger("nb_ai.worker")


async def main():
    """Запуск воркера"""
    worker = create_worker(
        RedisSettings.from_dsn(settings.REDIS_URL.replace("/0", "/1")),
        functions=WORKER_FUNCTIONS,
        queue_name="arq:queue",
        poll_delay=0.5,  # как часто проверять новые задачи
        max_jobs=1,  # одно задание за раз (важно для последовательности!)
        job_timeout=3600,  # максимум 1 час на пакет
    )

    logger.info("Starting ARQ worker...")
    await worker.async_run()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging

from arq import Worker

from app.services.arq_queue import WORKER_FUNCTIONS, REDIS_SETTINGS
from app.core.logger import setup_logging
from app.core.config import settings

# Настраиваем логирование
setup_logging()
logger = logging.getLogger("nb_ai.worker")


async def main():
    """Запуск воркера"""
    logger.info(f"Starting ARQ worker with Redis: {REDIS_SETTINGS}")
    logger.info(f"Registered functions: {[f.__name__ for f in WORKER_FUNCTIONS]}")

    # Создаем и запускаем worker
    max_jobs = settings.ARQ_MAX_JOBS
    worker = Worker(
        functions=WORKER_FUNCTIONS,
        redis_settings=REDIS_SETTINGS,
        queue_name="arq:queue",
        poll_delay=0.5,
        max_jobs=max_jobs,
        job_timeout=7200,
        health_check_interval=60
    )

    logger.info("Worker is ready and waiting for jobs...")
    await worker.async_run()


if __name__ == "__main__":
    asyncio.run(main())

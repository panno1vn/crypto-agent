"""
dags/dag_calculate_indicators.py

Ngày 13 — DAG tính indicators cho toàn bộ 5 coin x 4 timeframe.
"""

from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator


def run_pipeline():
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from data_pipeline.logger import get_logger
    from technical_analysis.indicator_pipeline import calculate_and_save_all

    logger = get_logger(__name__)

    DB_DSN = (
        f"postgresql+asyncpg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'postgres')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'crypto_agent')}"
    )

    async def _run():
        engine = create_async_engine(DB_DSN)
        session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            await calculate_and_save_all(session_factory)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception:
        logger.error("[DAG] dag_calculate_indicators thất bại.", exc_info=True)
        raise


with DAG(
    "dag_calculate_indicators",
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["technical_analysis"],
) as calc_dag:
    calc_task = PythonOperator(
        task_id="calc_and_save",
        python_callable=run_pipeline,
    )

"""Fresh bounded PostgreSQL operation; no pool, credential cache or diagnostic logging."""

import asyncio
import psycopg
from psycopg.rows import dict_row


async def run_database(
    dsn, operation, *, readonly=True, timeout=5, application_name="kai-binding-lookup"
):
    connection = None

    async def run():
        nonlocal connection
        connection = await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            connect_timeout=3,
            row_factory=dict_row,
            sslmode="verify-full",
            application_name=application_name,
            options=f'-c default_transaction_read_only={"on" if readonly else "off"} -c statement_timeout=3000',
        )
        try:
            return await operation(connection)
        finally:
            await connection.close()

    # Separate task: the deadline must not wait for a driver that is slow to honour cancellation.
    task = asyncio.create_task(run())
    task.add_done_callback(
        lambda result: None if result.cancelled() else result.exception()
    )
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            raise TimeoutError("Database operation deadline exceeded")
        return task.result()
    finally:
        if not task.done():
            try:
                if connection is not None:
                    await connection.close()
            finally:
                task.cancel()

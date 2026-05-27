# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Long-running Service Bus worker entry-point for the AWReason engine.

Connects to Azure Service Bus queue ``engine-runs`` (PeekLock mode) with
``AutoLockRenewer``, deserialises each message into a ``RunMessage``,
runs the assessment, and reports results back to the platform.

Usage::

    python -m wrappers.queue-worker.main

Environment variables are documented in ``runtime/config.py``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Any

from contracts.models import FinishRunRequest, RunMessage, RunResultMessage
from contracts.version import log_version_and_validate
from runtime.config import engine_settings
from runtime.telemetry import (
    correlation_id_var,
    get_logger,
    init_opentelemetry,
    run_id_var,
    setup_logging,
)

logger = get_logger(__name__)

# Graceful shutdown flag
_shutdown = asyncio.Event()


def _handle_signal(sig: int, _frame) -> None:  # type: ignore[override]
    logger.info("Signal %s received – initiating graceful shutdown.", sig)
    _shutdown.set()


def _coerce_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return str(v)


# ══════════════════════════════════════════════════════════════════════
#  Process a single message
# ══════════════════════════════════════════════════════════════════════

async def _process_message(raw_body: str, traceparent: str = "") -> None:
    """Deserialise, check idempotency, execute, and report."""
    from engine_core.runner import execute_run

    # Lazy imports – avoid circular / module-name issues
    from importlib import import_module
    _idempotency = import_module("wrappers.queue-worker.idempotency")
    _platform    = import_module("wrappers.queue-worker.platform_client")
    _sbio        = import_module("wrappers.queue-worker.servicebus_io")
    is_duplicate_run   = _idempotency.is_duplicate_run
    load_run_marker = _idempotency.load_run_marker

    # 1) Deserialise
    run_msg = RunMessage.model_validate_json(raw_body)
    correlation_id_var.set(run_msg.correlation_id)
    run_id_var.set(run_msg.run_id)
    logger.info(
        "Processing run_id=%s engine=%s correlation_id=%s",
        run_msg.run_id,
        run_msg.engine,
        run_msg.correlation_id,
    )

    # 2) Idempotency check
    if is_duplicate_run(run_msg.run_id):
        marker = load_run_marker(run_msg.run_id)
        marker_status = "Succeeded"
        marker_error = None
        if isinstance(marker, dict):
            status_value = str(marker.get("status", "Succeeded"))
            if status_value in {"Succeeded", "Failed", "Partial"}:
                marker_status = status_value
            error_value = marker.get("error_message")
            marker_error = str(error_value) if error_value else None

        logger.info(
            "Duplicate detected for run_id=%s; replaying marker status=%s.",
            run_msg.run_id,
            marker_status,
        )
        result = RunResultMessage(
            run_id=run_msg.run_id,
            status=marker_status,
            duration_ms=0,
            tokens_prompt=0,
            tokens_completion=0,
            error_message=marker_error,
            correlation_id=run_msg.correlation_id,
        )
        _report_result(result, run_msg, traceparent=traceparent)
        return

    # 3) Execute the run (blocking – runs in executor)
    loop = asyncio.get_running_loop()
    result_msg, artifacts = await loop.run_in_executor(None, execute_run, run_msg)

    # 4) Report result
    _report_result(result_msg, run_msg, traceparent=traceparent)


def _report_result(result: RunResultMessage, run_msg: RunMessage, *, traceparent: str = "") -> None:
    """Send result via Service Bus or HTTP PATCH depending on REPORT_MODE."""
    from importlib import import_module
    _platform = import_module("wrappers.queue-worker.platform_client")
    _sbio     = import_module("wrappers.queue-worker.servicebus_io")
    patch_run            = _platform.patch_run
    send_result_to_queue = _sbio.send_result_to_queue

    if engine_settings.report_mode == "servicebus":
        send_result_to_queue(result, traceparent=traceparent)
    else:
        body = FinishRunRequest(
            status=result.status,
            duration_ms=result.duration_ms,
            tokens_prompt=result.tokens_prompt,
            tokens_completion=result.tokens_completion,
            error_message=result.error_message,
            artifacts=result.artifacts,
        )
        patch_run(
            run_id=run_msg.run_id,
            body=body,
            correlation_id=run_msg.correlation_id,
            traceparent=traceparent,
        )


# ══════════════════════════════════════════════════════════════════════
#  Main worker loop
# ══════════════════════════════════════════════════════════════════════

async def _worker_loop() -> None:
    """Continuously receive and process messages until shutdown."""
    from importlib import import_module
    _sbio = import_module("wrappers.queue-worker.servicebus_io")
    create_async_receiver = _sbio.create_async_receiver

    concurrency = engine_settings.per_replica_concurrency
    semaphore = asyncio.Semaphore(concurrency)
    logger.info(
        "Worker starting: queue=%s, concurrency=%d, report_mode=%s",
        engine_settings.sb_queue,
        concurrency,
        engine_settings.report_mode,
    )

    sb_client, receiver, renewer, credential = await create_async_receiver()

    try:
        while not _shutdown.is_set():
            try:
                messages = await receiver.receive_messages(
                    max_message_count=concurrency,
                    max_wait_time=10,
                )
            except Exception:
                logger.exception("Error receiving messages – backing off 5s.")
                await asyncio.sleep(5)
                continue

            if not messages:
                continue

            tasks = []
            for msg in messages:
                # Register auto-lock renewal for long processing
                renewer.register(receiver, msg, max_lock_renewal_duration=600)

                async def _handle(m=msg):  # type: ignore[assignment]
                    async with semaphore:
                        try:
                            body = str(m)
                            app_props = getattr(m, "application_properties", None) or {}
                            traceparent = _coerce_str(
                                app_props.get("traceparent") or app_props.get(b"traceparent")
                            )
                            await _process_message(body, traceparent=traceparent)
                            await receiver.complete_message(m)
                            logger.info("Message completed.")
                        except Exception:
                            logger.exception("Message processing failed – dead-lettering.")
                            try:
                                await receiver.dead_letter_message(
                                    m, reason="ProcessingError",
                                    error_description="See engine logs for details.",
                                )
                            except Exception:
                                logger.exception("Dead-lettering failed – abandoning.")
                                try:
                                    await receiver.abandon_message(m)
                                except Exception:
                                    logger.exception("Abandon also failed.")

                tasks.append(asyncio.create_task(_handle()))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        await renewer.close()
        await receiver.close()
        await sb_client.close()
        await credential.close()
        logger.info("Worker loop exited cleanly.")


# ══════════════════════════════════════════════════════════════════════
#  Entry-point
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    setup_logging()
    init_opentelemetry()
    log_version_and_validate()

    os.makedirs(engine_settings.workdir_base, exist_ok=True)
    logger.info(
        "AWReason queue worker starting (workdir=%s, log_level=%s).",
        engine_settings.workdir_base,
        engine_settings.log_level,
    )

    # Register signal handlers for graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)

    asyncio.run(_worker_loop())
    logger.info("Worker shut down.")


if __name__ == "__main__":
    main()

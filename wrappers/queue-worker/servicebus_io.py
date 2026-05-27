# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Azure Service Bus I/O – receive from ``engine-runs``, send results.

Uses **PeekLock** with ``AutoLockRenewer`` so that long-running
assessment runs do not lose their message lock.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.servicebus.aio import (
    AutoLockRenewer,
    ServiceBusClient as AsyncServiceBusClient,
    ServiceBusReceiver as AsyncServiceBusReceiver,
)

from contracts.models import RunResultMessage
from runtime.config import engine_settings

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
#  Sync helpers (used for sending results from sync context)
# ══════════════════════════════════════════════════════════════════════

_sync_client: Optional[ServiceBusClient] = None


def _get_sync_client() -> ServiceBusClient:
    global _sync_client
    if _sync_client is None:
        credential = DefaultAzureCredential()
        fqns = engine_settings.sb_namespace
        if not fqns.endswith(".servicebus.windows.net"):
            fqns = f"{fqns}.servicebus.windows.net"
        _sync_client = ServiceBusClient(
            fully_qualified_namespace=fqns,
            credential=credential,  # type: ignore[arg-type]
        )
    return _sync_client


def send_result_to_queue(result: RunResultMessage, *, traceparent: str = "") -> None:
    """Send a ``RunResultMessage`` to ``SB_RESULTS_QUEUE`` (sync)."""
    queue = engine_settings.sb_results_queue
    if not queue:
        raise RuntimeError("SB_RESULTS_QUEUE is not configured for servicebus REPORT_MODE.")

    client = _get_sync_client()
    with client.get_queue_sender(queue) as sender:
        body = result.model_dump_json()
        msg = ServiceBusMessage(body, content_type="application/json")
        msg.correlation_id = result.correlation_id
        app_props: dict[str, str] = {
            "correlationId": result.correlation_id,
            "runId": result.run_id,
        }
        if traceparent:
            app_props["traceparent"] = traceparent
        msg.application_properties = app_props  # type: ignore[assignment]
        sender.send_messages(msg)
        logger.info(
            "Result for run_id=%s sent to queue %s", result.run_id, queue
        )


# ══════════════════════════════════════════════════════════════════════
#  Async receiver factory (used by main.py)
# ══════════════════════════════════════════════════════════════════════

async def create_async_receiver() -> tuple[
    AsyncServiceBusClient,
    AsyncServiceBusReceiver,
    AutoLockRenewer,
    AsyncDefaultAzureCredential,
]:
    """Create and return ``(client, receiver, auto_lock_renewer)``.

    Caller owns the lifecycle and **must** close them when done.
    """
    credential = AsyncDefaultAzureCredential()
    fqns = engine_settings.sb_namespace
    if not fqns.endswith(".servicebus.windows.net"):
        fqns = f"{fqns}.servicebus.windows.net"

    client = AsyncServiceBusClient(
        fully_qualified_namespace=fqns,
        credential=credential,  # type: ignore[arg-type]
    )
    receiver = client.get_queue_receiver(
        queue_name=engine_settings.sb_queue,
        max_wait_time=30,  # seconds to wait for new messages before yielding
    )
    renewer = AutoLockRenewer()

    return client, receiver, renewer, credential

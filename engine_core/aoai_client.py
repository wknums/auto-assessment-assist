# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Optional APIM → Azure OpenAI helper.

When ``APIM_AOAI_BASE_URL`` is configured the engine routes AOAI calls
through API Management; otherwise direct ``AZURE_OPENAI_ENDPOINT`` is used.

This module is **optional** – the existing ``awreason.py`` CLI already
handles its own AOAI client.  The helper is provided for future in-process
invocations where the engine core needs a pre-configured client.
"""
from __future__ import annotations

import logging
from typing import Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from runtime.config import engine_settings

logger = logging.getLogger(__name__)

_client: Optional[AzureOpenAI] = None


def get_aoai_client() -> AzureOpenAI:
    """Return a singleton ``AzureOpenAI`` client configured from env vars.

    Uses APIM endpoint if available, else falls back to direct AOAI endpoint.
    """
    global _client
    if _client is not None:
        return _client

    endpoint = engine_settings.apim_aoai_base_url or ""
    if not endpoint:
        raise RuntimeError(
            "Neither APIM_AOAI_BASE_URL nor AZURE_OPENAI_ENDPOINT is configured."
        )

    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )

    _client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=engine_settings.aoai_api_version or "2024-12-01-preview",
    )
    logger.info("AzureOpenAI client initialised (endpoint=%s).", endpoint)
    return _client

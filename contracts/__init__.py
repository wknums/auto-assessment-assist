# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Platform ↔ Engine contracts for the AWReason engine.

All models use **Pydantic v2 strict mode** by default so that callers
cannot silently pass wrong types.
"""
from contracts.models import (
    AoaiParams,
    ArtifactItem,
    FinishRunRequest,
    OutputParams,
    RunMessage,
    RunParameters,
    RunProfile,
    RunResultMessage,
)
from contracts.version import CONTRACT_VERSION

__all__ = [
    "AoaiParams",
    "ArtifactItem",
    "CONTRACT_VERSION",
    "FinishRunRequest",
    "OutputParams",
    "RunMessage",
    "RunParameters",
    "RunProfile",
    "RunResultMessage",
]

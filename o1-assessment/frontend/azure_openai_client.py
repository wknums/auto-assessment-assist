# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

"""
Azure OpenAI client utility module for chat functionality.
Provides reusable functions for initializing the Azure OpenAI client and sending chat completions.
"""

import os
from pathlib import Path
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv


def initialize_azure_openai_client():
    """
    Initialize and return an AzureOpenAI client with Entra ID authentication.
    
    Returns:
        AzureOpenAI: Initialized client ready for chat completions
        
    Raises:
        Exception: If environment variables are not set or authentication fails
    """
    # Load environment variables from the parent directory's .env file
    current_dir = Path(__file__).parent
    dotenv_path = current_dir.parent.parent / ".env"
    dotenv_path = dotenv_path.resolve()
    
    load_dotenv(dotenv_path=dotenv_path, override=True)
    
    # Retrieve environment variables
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    
    if not endpoint:
        raise ValueError(
            "AZURE_OPENAI_ENDPOINT not set. Please configure your .env file with Azure OpenAI credentials."
        )
    
    # Initialize with Entra ID authentication
    cognitiveServicesResource = "https://cognitiveservices.azure.com/"
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        f'{cognitiveServicesResource}.default'
    )
    
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version='2024-12-01-preview',
    )
    
    return client


def send_chat_completion(client, messages, deployment=None, max_tokens=4000, temperature=None):
    """
    Send a chat completion request to Azure OpenAI and return the response.
    
    Args:
        client (AzureOpenAI): Initialized Azure OpenAI client
        messages (list): List of message dictionaries with 'role' and 'content'
        deployment (str, optional): Deployment name. Defaults to AZURE_OPENAI_DEPLOYMENT_O1 env var
        max_tokens (int, optional): Maximum tokens for completion. Defaults to 4000
        temperature (float, optional): Sampling temperature. If None, uses model default
        
    Returns:
        tuple: (response_content: str, usage_info: dict)
        
    Raises:
        Exception: If the API call fails
    """
    if deployment is None:
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_O1", "o1")
    
    # Build completion parameters
    completion_params = {
        "model": deployment,
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "seed": 31457  # For consistent results across runs
    }
    
    # Add temperature if specified (note: o1 models may not support temperature)
    if temperature is not None:
        completion_params["temperature"] = temperature
    
    try:
        # Send the request
        completion = client.chat.completions.create(**completion_params)
        
        # Extract response and usage information
        response_content = completion.choices[0].message.content
        usage_info = {
            'prompt_tokens': completion.usage.prompt_tokens,
            'completion_tokens': completion.usage.completion_tokens,
            'total_tokens': completion.usage.total_tokens
        }
        
        return response_content, usage_info
        
    except Exception as e:
        # Re-raise with more context
        raise Exception(f"Azure OpenAI API call failed: {str(e)}")


def count_messages_by_role(messages, role):
    """
    Count the number of messages with a specific role.
    
    Args:
        messages (list): List of message dictionaries
        role (str): Role to count ('user', 'assistant', 'system')
        
    Returns:
        int: Count of messages with the specified role
    """
    return sum(1 for msg in messages if msg.get('role') == role)


def estimate_token_count(text):
    """
    Rough estimation of token count for a text string.
    Uses a simple heuristic: ~4 characters per token on average.
    
    For more accurate counting, consider using tiktoken library.
    
    Args:
        text (str): Text to estimate token count for
        
    Returns:
        int: Estimated token count
    """
    return len(text) // 4


def truncate_context_to_fit(messages, max_tokens=50000):
    """
    Truncate message history to fit within token limits.
    Keeps system messages and removes oldest user/assistant pairs.
    
    Args:
        messages (list): List of message dictionaries
        max_tokens (int): Maximum allowed tokens
        
    Returns:
        list: Truncated message list
    """
    # Separate system messages from conversation
    system_messages = [msg for msg in messages if msg['role'] == 'system']
    conversation = [msg for msg in messages if msg['role'] != 'system']
    
    # Estimate current token count
    total_text = ''.join([msg['content'] for msg in messages])
    estimated_tokens = estimate_token_count(total_text)
    
    # If within limits, return as-is
    if estimated_tokens <= max_tokens:
        return messages
    
    # Remove oldest conversation pairs until we're under the limit
    while estimated_tokens > max_tokens and len(conversation) > 2:
        # Remove the oldest pair (user + assistant)
        conversation = conversation[2:]
        
        # Recalculate
        all_messages = system_messages + conversation
        total_text = ''.join([msg['content'] for msg in all_messages])
        estimated_tokens = estimate_token_count(total_text)
    
    return system_messages + conversation

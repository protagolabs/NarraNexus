""" 
@file_name: openai_agents_sdk.py
@author: NetMind.AI
@date: 2025-11-07
@description: This file contains the openai agents sdk.
"""


from typing import AsyncGenerator
from agents import Agent, Runner, OpenAIChatCompletionsModel
from pydantic import BaseModel
from openai import AsyncOpenAI

from loguru import logger
from xyz_agent_context.settings import settings


class OpenAIAgentsSDK:
    def __init__(self):
        pass
    
    async def agent_loop(
        self) -> AsyncGenerator[str, None]:
        pass
    
    # Default model for auxiliary LLM calls
    DEFAULT_MODEL = "gpt-5.1-2025-11-13"

    async def llm_function(
        self,
        instructions: str,
        user_input: str,
        output_type: BaseModel = None,
        model: str = None,
    ) -> str:

        model_name = model or self.DEFAULT_MODEL
        logger.info(f"OpenAIAgentsSDK: llm_function using model={model_name}")

        agent = Agent(
            name="ChatGPT",
            instructions=instructions,
            output_type=output_type,
            model=OpenAIChatCompletionsModel(
                model=model_name,
                openai_client=AsyncOpenAI(api_key=settings.openai_api_key),
            ),
        )
        
        result = await Runner.run(agent, user_input)
        
        return result

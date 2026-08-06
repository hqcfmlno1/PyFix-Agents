import os
from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from httpx import AsyncClient
load_dotenv()

provider = OpenAIProvider(
    base_url='https://api.deepinfra.com/v1/openai',
    api_key=os.getenv('DEEPSEEK_API_KEY'),
    http_client=AsyncClient(timeout=120) 
)
model = OpenAIChatModel('deepseek-ai/DeepSeek-V4-Flash-0731', provider=provider)

agent = Agent(model)
result = agent.run_sync('bạn tên gì')
print(result.output)
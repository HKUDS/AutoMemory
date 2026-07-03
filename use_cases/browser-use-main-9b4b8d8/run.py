"""Local Chromium + OpenRouter LLM. Set OPENROUTER_API_KEY in .env (optional: OPENROUTER_MODEL)."""

import asyncio
import os

from dotenv import load_dotenv

from browser_use import Agent, Browser
from browser_use.llm import ChatOpenRouter

load_dotenv()


async def main():
	# Local Chromium over CDP (not use_cloud). If needed: uv run python -m browser_use.skill_cli.main install
	browser = Browser(headless=True)

	llm = ChatOpenRouter(
		model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
		api_key=os.getenv("OPENROUTER_API_KEY"),
	)

	agent = Agent(
		task="Find license of the browser-use repo",
		llm=llm,
		browser=browser,
	)
	await agent.run()


if __name__ == "__main__":
	asyncio.run(main())

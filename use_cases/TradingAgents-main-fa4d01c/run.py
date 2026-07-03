from dotenv import load_dotenv

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

load_dotenv()

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openrouter"        # openai, google, anthropic, xai, openrouter, ollama
config["deep_think_llm"] = "minimax/minimax-m2.7"     # Model for complex reasoning
config["quick_think_llm"] = "qwen/qwen3.5-flash-02-23" # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("AAPL", "2026-04-20")
print(decision)
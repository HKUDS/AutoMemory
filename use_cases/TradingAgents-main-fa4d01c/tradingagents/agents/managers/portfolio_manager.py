from tradingagents.agents.utils.agent_utils import build_instrument_context, get_language_instruction
from tradingagents.agents.utils.memory_stores import DecisionsLog, MacroNotes, UserContext


def _format_prior_decisions(records) -> str:
    """Format last-N decision records as a compact markdown block."""
    if not records:
        return ""
    lines = ["## Prior decisions on this ticker (most recent last)"]
    for r in records:
        date = r.get("trade_date", "?")
        rating = r.get("rating", "?")
        thesis = r.get("one_line_thesis", "")
        risks = r.get("key_risks", [])
        risk_str = "; ".join(risks[:2]) if risks else ""
        line = f"- {date}: **{rating}** — {thesis}"
        if risk_str:
            line += f" (risks: {risk_str})"
        lines.append(line)
    return "\n".join(lines)


def create_portfolio_manager(llm, memory):
    def portfolio_manager_node(state) -> dict:
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker)

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        market_research_report = state["market_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        sentiment_report = state["sentiment_report"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # Type D: inject last-3 decisions for drift awareness
        prior_decisions_str = _format_prior_decisions(
            DecisionsLog.read_last_n(ticker, n=3)
        )

        # Type U: inject user portfolio + profile
        user_context_str = UserContext.read(ticker=ticker)

        # Type M: inject recent macro context
        macro_block = MacroNotes.read_recent(n_weeks=2)
        macro_section = (
            f"\n## Recent macro context (last 2 weeks)\n{macro_block}"
            if macro_block
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
- Lessons from past decisions: **{past_memory_str}**

{prior_decisions_str}

{user_context_str}
{macro_section}

**Required Output Structure:**
1. **Rating**: State one of Buy / Overweight / Hold / Underweight / Sell.
2. **Executive Summary**: A concise action plan covering entry strategy, position sizing, key risk levels, and time horizon.
3. **Investment Thesis**: Detailed reasoning anchored in the analysts' debate and past reflections.

---

**Risk Analysts Debate History:**
{history}

---

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        response = llm.invoke(prompt)

        new_risk_debate_state = {
            "judge_decision": response.content,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": response.content,
        }

    return portfolio_manager_node

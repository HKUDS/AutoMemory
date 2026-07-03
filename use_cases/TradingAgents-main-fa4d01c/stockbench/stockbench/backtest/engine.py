from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple
import json
from datetime import datetime, timezone

import pandas as pd
import logging
logger = logging.getLogger(__name__)

from stockbench.backtest.metrics import evaluate
from stockbench.backtest.slippage import Slippage
from stockbench.backtest.metrics import compute_nav_to_metrics_series


@dataclass
class Position:
	shares: float = 0.0  
	avg_price: float = 0.0
	holding_days: int = 0
	total_cost: float = 0.0  # Cumulative investment cost (based on net_cost)


@dataclass
class Portfolio:
	cash: float
	positions: Dict[str, Position] = field(default_factory=dict)

	def equity(self, open_prices: Dict[str, float] = None, previous_open_prices: Dict[str, float] = None) -> float:
		"""Calculate total equity (cash + current market value)"""
		if open_prices is None:
			# If no opening prices provided, must provide previous trading day opening prices
			if previous_open_prices is None:
				raise ValueError("Must provide either open_prices or previous_open_prices")
			return self.cash + self.get_total_position_value({}, previous_open_prices)
		else:
			# Calculate using opening prices
			return self.cash + self.get_total_position_value(open_prices, previous_open_prices)
	
	def get_position_value(self, symbol: str, mark_price: float) -> float:
		"""Get position market value for specified symbol"""
		pos = self.positions.get(symbol)
		if pos and pos.shares != 0:
			return pos.shares * mark_price
		return 0.0
	
	def get_total_position_value(self, open_prices: Dict[str, float], previous_open_prices: Dict[str, float] = None) -> float:
		"""Get total position value (based on opening prices)"""
		total = 0.0
		for symbol, pos in self.positions.items():
			if pos.shares != 0:
				# Price fallback priority: opening price -> previous trading day opening price -> cost price
				price = open_prices.get(symbol)
				if price is None and previous_open_prices:
					price = previous_open_prices.get(symbol)
				if price is None:
					# If no opening price or previous trading day opening price available, fallback to cost price
					price = pos.avg_price
				total += pos.shares * price
		return total
	
	def get_position_pct(self, symbol: str, open_price: float, open_prices: Dict[str, float] = None) -> float:
		"""Get position percentage for specified symbol"""
		# Build open_prices dictionary for equity calculation
		if open_prices is None:
			# If no opening price dictionary provided, need to use previous trading day closing price or throw exception
			raise ValueError("Must provide open_prices dictionary for position percentage calculation")
		else:
			open_prices_dict = open_prices.copy()
		open_prices_dict[symbol] = open_price  # Ensure current symbol's price is correct
		total_equity = self.equity(open_prices_dict, None)
		if total_equity <= 0:
			return 0.0
		position_value = self.get_position_value(symbol, open_price)
		return position_value / total_equity
	
	def update_cash(self, amount: float) -> bool:
		"""
		Safely update cash, ensuring cash does not become negative
		
		Args:
			amount: Cash amount to add (positive for increase, negative for decrease)
			
		Returns:
			bool: Whether the update was successful
		"""
		logger.info("=== Cash Update Operation ===")
		logger.info(f"[CASH_UPDATE] Current cash: {self.cash:.2f}")
		logger.info(f"[CASH_UPDATE] Change amount: {amount:.2f} ({'Increase' if amount >= 0 else 'Decrease'})")
		
		new_cash = self.cash + amount
		logger.debug(f"[CASH_UPDATE] Calculate new cash: {self.cash:.2f} + ({amount:.2f}) = {new_cash:.2f}")
		
		if new_cash < 0:
			logger.warning(f"[CASH_PROTECTION] Cash update rejected: new cash {new_cash:.2f} < 0")
			logger.info("=== Cash Update Failed ===")
			return False
		
		self.cash = new_cash
		logger.info(f"[CASH_UPDATE] Cash update successful: {self.cash:.2f}")
		logger.info("=== Cash Update Completed ===")
		return True
	
	def can_afford(self, cost: float) -> bool:
		"""
		Check if there is enough cash to pay the specified cost
		
		Args:
			cost: Amount to be paid
			
		Returns:
			bool: Whether there is enough cash
		"""
		return self.cash >= cost


@dataclass
class TradeRecord:
	"""Trade record"""
	timestamp: str
	symbol: str
	side: str  # "buy" or "sell"
	qty: float
	exec_price: float
	exec_ref_price: float
	commission_bps: float
	fill_ratio: float
	# New fields
	trade_value: float  # Trade amount (excluding commission)
	commission: float   # Commission
	net_cost: float     # Net cost
	# Pre/post trade status
	cash_before: float
	cash_after: float
	position_before: int
	position_after: int
	avg_price_before: float
	avg_price_after: float
	# Portfolio status
	total_equity_before: float
	total_equity_after: float
	total_position_value_before: float
	total_position_value_after: float
	# Profit/loss information
	unrealized_pnl_before: float
	unrealized_pnl_after: float
	realized_pnl: float  # If selling, record realized P&L


@dataclass
class PortfolioSnapshot:
	"""Portfolio snapshot"""
	timestamp: str
	date: str
	cash: float
	total_equity: float
	total_position_value: float
	unrealized_pnl: float
	nav: float  # Net asset value
	positions: Dict[str, Dict]  # Detailed information for each symbol
	benchmark_nav: float = 0.0  # Benchmark net asset value


# ===== Benchmark helpers (M2) =====

def load_benchmark_components(bench_cfg: Dict, datasets, dates: pd.DatetimeIndex, field: str = "adjusted_close") -> Dict[str, pd.Series]:
	"""Read price series by symbol, return {symbol: price_series}.
	- field prioritizes adjusted_close, fallback to close if not available.
	- Only supports daily level (timespan=day).
	"""
	series_dict: Dict[str, pd.Series] = {}
	start = str(dates.min().date()) if len(dates) > 0 else None
	end = str(dates.max().date()) if len(dates) > 0 else None
	if start is None or end is None:
		return series_dict

	symbols: List[str] = []
	if isinstance(bench_cfg.get("basket"), list) and len(bench_cfg.get("basket")) > 0:
		for comp in bench_cfg["basket"]:
			if isinstance(comp, dict) and isinstance(comp.get("symbol"), str):
				symbols.append(comp["symbol"])
			elif isinstance(comp, str):
				symbols.append(comp)
	elif isinstance(bench_cfg.get("symbol"), str):
		symbols.append(str(bench_cfg.get("symbol")))

	for sym in symbols:
		bars = datasets.get_day_bars(sym, start, end)
		if bars is None or bars.empty:
			continue
		# Select field
		px_col = None
		candidates = [field]
		for c in candidates:
			if c in bars.columns:
				px_col = c
				break
		if px_col is None:
			px_col = "close" if "close" in bars.columns else None
		if px_col is None:
			continue
		if "date" in bars.columns:
			idx = pd.to_datetime(bars["date"])
		elif "timestamp" in bars.columns:
			idx = pd.to_datetime(bars["timestamp"]).dt.normalize()
		else:
			idx = pd.to_datetime(bars.index)
		ser = pd.Series(bars[px_col].astype(float).values, index=idx).sort_index()
		ser = ser[~ser.index.duplicated(keep="last")]
		ser = ser[(ser.index >= pd.to_datetime(start)) & (ser.index <= pd.to_datetime(end))]
		ser = ser.dropna()
		if len(ser) == 0:
			continue
		series_dict[sym] = ser
	return series_dict


def _load_symbol_price_series(datasets, symbols: List[str], start: str, end: str, field: str = "open") -> Dict[str, pd.Series]:
	"""Read daily price series by symbol, return {symbol: price_series}.
	field supports: 'open'|'close'|'adjusted_close' (priority order: specified field -> close).
	"""
	series_dict: Dict[str, pd.Series] = {}
	if not symbols:
		return series_dict
	for sym in symbols:
		try:
			bars = datasets.get_day_bars(sym, start, end)
			if bars is None or bars.empty:
				continue
			px_col = None
			candidates = [field]
			if field != "close":
				candidates.append("close")
			for c in candidates:
				if c in bars.columns:
					px_col = c
					break
			if px_col is None:
				px_col = "close" if "close" in bars.columns else None
			if px_col is None:
				continue
			if "date" in bars.columns:
				idx = pd.to_datetime(bars["date"]).sort_values()
			elif "timestamp" in bars.columns:
				idx = pd.to_datetime(bars["timestamp"]).dt.normalize().sort_values()
			else:
				idx = pd.to_datetime(bars.index).sort_values()
			ser = pd.Series(bars[px_col].astype(float).values, index=idx)
			ser = ser[~ser.index.duplicated(keep="last")]
			ser = ser[(ser.index >= pd.to_datetime(start)) & (ser.index <= pd.to_datetime(end))]
			ser = ser.dropna()
			if len(ser) == 0:
				continue
			series_dict[sym] = ser
		except Exception:
			continue
	return series_dict


def build_per_symbol_bh_benchmark(dates: pd.DatetimeIndex, symbols: List[str], price_field: str, cfg: Dict,
									 commission_bps: float, slippage_bps: float) -> pd.DataFrame:
	"""Build per-symbol equal amount buy and hold benchmark, return nav DataFrame with symbol columns.
	Fees are only included on first day buy-in, NAV_0=1/(1+fee_rate) (if fees included).
	"""
	if dates is None or len(dates) == 0 or not symbols:
		return pd.DataFrame([])
	bench_cfg = ((cfg.get("backtest", {}) or {}).get("benchmark", {}) or {})
	total_cash_override = bench_cfg.get("total_cash")

	# Fee rate (calculated directly by bps value, bps=0 means no fees)
	fee_rate = float(commission_bps or 0.0) / 1e4 + float(slippage_bps or 0.0) / 1e4

	start = str(dates.min().date())
	end = str(dates.max().date())
	# Read prices
	series_map = _load_symbol_price_series(datasets=cfg.get("_datasets"), symbols=symbols, start=start, end=end, field=price_field)
	if not series_map:
		return pd.DataFrame([])
	# Total capital
	portfolio_cash = float(cfg.get("portfolio", {}).get("total_cash", 1_000_000))
	C_total = float(total_cash_override) if (total_cash_override is not None) else portfolio_cash
	# Number of valid symbols (having valid prices on first day)
	valid_symbols: List[str] = []
	first_price_map: Dict[str, float] = {}
	for sym, ser in series_map.items():
		aligned = ser.reindex(dates)
		# Do not forward fill to avoid generating fake prices on first day
		p0 = aligned.dropna().iloc[0] if aligned.dropna().shape[0] > 0 else None
		if p0 is not None and p0 > 0:
			valid_symbols.append(sym)
			first_price_map[sym] = float(p0)
	if not valid_symbols:
		return pd.DataFrame([])
	# Equal capital allocation
	n = len(valid_symbols)
	C_per = C_total / max(n, 1)
	C_net = C_per / (1.0 + fee_rate) if (fee_rate > 0) else C_per

	nav_dict: Dict[str, pd.Series] = {}
	for sym in valid_symbols:
		ser = series_map[sym].reindex(dates)
		# Allow missing values to be ffilled later according to configuration (here only for calculating shares to get P0)
		p0 = first_price_map[sym]
		shares = C_net / max(p0, 1e-12)
		nav = shares * ser / max(C_per, 1e-12)
		nav_dict[sym] = nav.astype(float)
	return pd.DataFrame(nav_dict, index=dates)


def price_to_returns(series_dict: Dict[str, pd.Series]) -> pd.DataFrame:
	"""Convert price series to daily returns DataFrame (columns as components). Default alignment uses inner join to ensure all columns have returns for the day."""
	if not series_dict:
		return pd.DataFrame([])
	df = pd.DataFrame(series_dict).sort_index()
	# Today/yesterday - 1
	ret = df.pct_change()
	# First row or respective start dates will be NaN; when synthesizing baskets, only use dates where all columns have returns
	ret = ret.dropna(how="any")
	return ret


def aggregate_with_rebalance(returns_df: pd.DataFrame, basket: List[Dict], freq: str = "daily") -> pd.Series:
	"""Aggregate returns by weight with rebalancing support:
	- daily: Daily weighted by target weights, weights reset daily
	- monthly: Reset to target weights on first day of each month, others drift naturally (buy and hold), calculate returns using current weights first, then update drifted weights based on daily returns
	- none: Buy and hold throughout (natural drift, starting from first day)
	"""
	if returns_df is None or returns_df.empty:
		return pd.Series(dtype=float)
	# Parse target weights
	weights_map: Dict[str, float] = {}
	if isinstance(basket, list) and len(basket) > 0:
		total = 0.0
		for comp in basket:
			if isinstance(comp, dict) and isinstance(comp.get("symbol"), str):
				w = float(comp.get("weight", 0.0))
				weights_map[comp["symbol"]] = w
				total += w
			elif isinstance(comp, str):
				# String without weight, to be equally divided later
				weights_map[comp] = weights_map.get(comp, 0.0)
				total += 0.0
		# Equally divide unspecified weight components or when total is 0
		if total <= 0.0:
			n = max(1, len(returns_df.columns))
			for c in returns_df.columns:
				weights_map[c] = 1.0 / n
		else:
			# Normalize to 1
			for k in list(weights_map.keys()):
				weights_map[k] = weights_map[k] / total
	else:
		# If no basket provided, equally divide
		n = max(1, len(returns_df.columns))
		for c in returns_df.columns:
			weights_map[c] = 1.0 / n

	# Only keep columns that actually exist in returns_df
	w = pd.Series({c: weights_map.get(c, 0.0) for c in returns_df.columns}, index=returns_df.columns)
	if w.sum() == 0:
		w = pd.Series([1.0 / len(returns_df.columns)] * len(returns_df.columns), index=returns_df.columns)
	else:
		w = w / w.sum()

	freq = (freq or "daily").lower()
	if freq == "daily":
		# Daily reset is equivalent to daily weighted sum with fixed weights
		agg = (returns_df * w).sum(axis=1)
		return agg.astype(float)

	# Simulate weight drift
	current_w = w.copy().astype(float)
	out: List[float] = []
	idx: List[pd.Timestamp] = []
	prev_month = None
	for dt, row in returns_df.iterrows():
		if prev_month is None:
			prev_month = dt.month
		# Monthly rebalancing: reset to target weights on first trading day of each month
		if freq == "monthly":
			if dt.month != prev_month:
				current_w = w.copy()
				prev_month = dt.month
		# Portfolio daily return
		day_ret = float((current_w * row).sum())
		out.append(day_ret)
		idx.append(dt)
		# Natural weight drift after returns
		# w'_i = w_i * (1 + r_i) / (1 + r_p)
		den = (1.0 + day_ret)
		if den != 0:
			current_w = (current_w * (1.0 + row)).astype(float)
			current_w = current_w / den
		else:
			# Avoid division by zero in extreme cases
			current_w = current_w.copy()
	return pd.Series(out, index=pd.DatetimeIndex(idx)).astype(float)


def align_with_strategy_nav(bench_nav: pd.Series, dates: pd.DatetimeIndex, reindex: str = "inner_join", fill: str = "ffill", fill_limit: int = 0) -> pd.Series:
	"""Align benchmark nav according to strategy dates, and perform forward fill and missing value removal according to configuration."""
	if bench_nav is None or len(bench_nav) == 0:
		return pd.Series(dtype=float)
	bench = bench_nav.copy()
	reindex_mode = str(reindex or "inner_join").lower()
	fill_mode = str(fill or "ffill").lower()
	try:
		if reindex_mode == "business":
			bench = bench.reindex(dates)
		else:
			bench = bench[bench.index.isin(dates)]
		if fill_mode == "ffill":
			bench = bench.ffill(limit=None if int(fill_limit or 0) <= 0 else int(fill_limit))
		bench = bench.dropna()
		return bench.astype(float)
	except Exception:
		return pd.Series(dtype=float)


class BacktestEngine:
	def __init__(self, cfg: Dict, datasets, slippage_model: Slippage) -> None:
		self.cfg = cfg
		self.datasets = datasets
		self.slippage = slippage_model
		self.commission_bps = float(cfg.get("backtest", {}).get("commission_bps", 0.0))
		self.fill_ratio = float((cfg.get("backtest", {}) or {}).get("fill_ratio", 1.0))
		self.rebalance_at_open = bool((cfg.get("backtest", {}) or {}).get("rebalance_at_open", True))
		# Risk management parameters
		rcfg = cfg.get("risk", {}) or {}
		self.max_positions = int(rcfg.get("max_positions", 999999))
		self.cooldown_days = int(rcfg.get("cooldown_days", 0))
		self.min_holding_days = int(rcfg.get("min_holding_days", 0))
		# Trading record configuration
		self.enable_detailed_logging = bool(cfg.get("backtest", {}).get("enable_detailed_logging", False))

		# Get initial capital, prioritize using portfolio.total_cash
		portfolio_cash = float(self.cfg.get("portfolio", {}).get("total_cash", 1_000_000))
		self.initial_cash = float(self.cfg.get("backtest", {}).get("cash", portfolio_cash))

		# Re-decision deduplication flag (avoid infinite triggering of same symbol on same day/minute)
		self._redecide_flags = set()


	def _validate_portfolio_position_consistency(self, pf: Portfolio, ctx: Dict, date: pd.Timestamp) -> None:
		"""
		Validate portfolio position consistency to catch data integrity issues early
		
		Args:
			pf: Portfolio object
			ctx: Strategy context
			date: Current trading date
		"""
		try:
			logger.info(f"[POSITION_VALIDATION] {date.date()}: Validating portfolio position consistency")
			
			inconsistencies_found = 0
			for symbol, position in pf.positions.items():
				if position is None:
					continue
					
				shares = getattr(position, 'shares', 0)
				avg_price = getattr(position, 'avg_price', 0)
				holding_days = getattr(position, 'holding_days', 0)
				
				# Check for suspicious values
				if shares < 0:
					logger.error(f"[POSITION_VALIDATION] {symbol}: Negative shares detected: {shares}")
					inconsistencies_found += 1
				
				if shares > 0 and avg_price <= 0:
					logger.error(f"[POSITION_VALIDATION] {symbol}: Has shares ({shares}) but invalid avg_price ({avg_price})")
					inconsistencies_found += 1
				
				if shares == 0 and (avg_price > 0 or holding_days > 0):
					logger.warning(f"[POSITION_VALIDATION] {symbol}: Zero shares but avg_price={avg_price}, holding_days={holding_days}")
				
				# Check for the specific 57.01 bug
				if abs(shares - 57.01) < 0.001:
					logger.error(f"[POSITION_VALIDATION] {symbol}: Suspicious shares value 57.01 detected! This may be the target_cash_amount/price bug.")
					inconsistencies_found += 1
				
				logger.debug(f"[POSITION_VALIDATION] {symbol}: shares={shares:.2f}, avg_price={avg_price:.4f}, holding_days={holding_days}")
			
			if inconsistencies_found > 0:
				logger.error(f"[POSITION_VALIDATION] Found {inconsistencies_found} position inconsistencies on {date.date()}")
			else:
				logger.info(f"[POSITION_VALIDATION] {date.date()}: All positions validated successfully")
				
		except Exception as e:
			logger.warning(f"[POSITION_VALIDATION] Validation failed: {e}")

	def _maybe_redecide_qty(self, strategy, ctx: Dict, symbol: str, current_shares: float, proposed_filled_qty: float, key: Tuple) -> float | None:
		"""When position would become 0 after execution, trigger strategy re-decision.
		Returns new qty (0 means cancel), None means no change.
		"""
		try:
			if (current_shares + proposed_filled_qty) != 0:
				return None
			if key in self._redecide_flags:
				return None
			self._redecide_flags.add(key)
			ctx2 = dict(ctx or {})
			ctx2.update({
				"redecide": True,
				"redecide_reason": "position_would_be_zero",
				"redecide_symbol": symbol,
				"current_shares": round(float(current_shares), 2),
				"proposed_filled_qty": round(float(proposed_filled_qty), 2),
			})
			fn = getattr(strategy, "on_redecide", None)
			if callable(fn):
				result = fn(ctx2)
				if isinstance(result, dict):
					sym = result.get("symbol", symbol)
					if sym == symbol and "qty" in result:
						return round(float(result.get("qty", 0)), 2)
				elif isinstance(result, list):
					for od in result:
						try:
							if isinstance(od, dict) and od.get("symbol") == symbol and "qty" in od:
								return round(float(od.get("qty", 0)), 2)
						except Exception:
							continue
		except Exception:
			return None
		return None

	def _create_portfolio_snapshot(self, pf: Portfolio, date: pd.Timestamp, open_prices: Dict[str, float], 
								 benchmark_nav: float = 0.0, previous_open_prices: Dict[str, float] = None) -> PortfolioSnapshot:
		"""Create portfolio snapshot (use opening prices to be consistent with decision prompts)"""
		# Use the same calculation method as decision prompts: compute position value based on opening prices
		available_cash = pf.cash  # Available cash = current cash
		position_value = pf.get_total_position_value(open_prices, previous_open_prices)  # Position value = opening price market value
		total_assets = available_cash + position_value  # Total assets = cash + position value
		
		# For backward compatibility, still use the original variable names
		total_position_value = position_value
		total_equity = total_assets
		
		# Calculate unrealized P&L
		unrealized_pnl = 0.0
		positions_detail = {}
		
		for symbol, pos in pf.positions.items():
			if pos.shares != 0:
				# Price fallback priority: current opening price -> previous trading day opening price -> cost price
				open_price = open_prices.get(symbol)
				if open_price is None and previous_open_prices:
					open_price = previous_open_prices.get(symbol)
				if open_price is None:
					open_price = pos.avg_price
				position_value = pos.shares * open_price
				unrealized_pnl += pos.shares * (open_price - pos.avg_price)
				
				# Use complete price data when calculating position percentage
				# If open_prices is empty, use previous_open_prices as fallback
				price_for_pct = open_prices if open_prices else previous_open_prices
				position_pct = pf.get_position_pct(symbol, open_price, price_for_pct) if price_for_pct else 0.0
				
				positions_detail[symbol] = {
					"shares": pos.shares,
					"avg_price": pos.avg_price,
					"mark_price": open_price,  # This is actually the opening price, keep field name compatibility
					"position_value": position_value,
					"position_pct": position_pct,
					"unrealized_pnl": pos.shares * (open_price - pos.avg_price),
					"holding_days": pos.holding_days,
					"total_cost": pos.total_cost  # Add cumulative cost information
				}
		
		nav = total_equity / self.initial_cash if self.initial_cash > 0 else 1.0
		
		return PortfolioSnapshot(
			timestamp=datetime.now(timezone.utc).isoformat(),
			date=date.strftime("%Y-%m-%d"),
			cash=pf.cash,
			total_equity=total_equity,
			total_position_value=total_position_value,
			unrealized_pnl=unrealized_pnl,
			nav=nav,
			positions=positions_detail,
			benchmark_nav=benchmark_nav
		)

	def _get_next_day_open_prices(self, next_date: pd.Timestamp, symbols: List[str]) -> Dict[str, float]:
		"""Get opening prices for the next trading day; if the next day is a holiday, keep moving forward.
		
		Args:
			next_date: Next trading day date
			symbols: List of tickers
			
		Returns:
			Dict[str, float]: Mapping from symbol to opening price
		"""
		next_open_map: Dict[str, float] = {}
		current_date = next_date
		max_days_ahead = 10  # Push forward up to 10 days to avoid infinite loops
		
		for day_offset in range(max_days_ahead):
			logger.debug(f"[NEXT_DAY_PRICE] Trying to fetch open prices for {current_date.strftime('%Y-%m-%d')} (attempt {day_offset+1})")
			
			# Try to get the opening prices for the current date
			day_success_count = 0
			for s in symbols:
				if s not in next_open_map:  # Only fetch for symbols without prices yet
					try:
						bars = self.datasets.get_day_bars(s, current_date.strftime("%Y-%m-%d"), current_date.strftime("%Y-%m-%d"))
						if not bars.empty:
							row = bars.loc[bars["date"] == current_date.date()].iloc[0]
							open_px = float(row["open"])
							next_open_map[s] = open_px
							day_success_count += 1
							logger.debug(f"[NEXT_DAY_PRICE] {current_date.strftime('%Y-%m-%d')} {s}: got opening price {open_px}")
						else:
							logger.debug(f"[NEXT_DAY_PRICE] {current_date.strftime('%Y-%m-%d')} {s}: empty daily bars (possibly a holiday)")
					except Exception as e:
						logger.warning(f"[NEXT_DAY_PRICE] {current_date.strftime('%Y-%m-%d')} {s}: failed to get opening price - {e}")
			
			# Stop if all symbols have prices, or at least some have been obtained
			if len(next_open_map) == len(symbols) or day_success_count > 0:
				break
				
			# If no symbol has prices on this day, move to the next day
			current_date = current_date + pd.Timedelta(days=1)
		
		logger.info(f"[NEXT_DAY_PRICE] Final result: successfully fetched opening prices for {len(next_open_map)}/{len(symbols)} symbols")
		if len(next_open_map) < len(symbols):
			missing_symbols = [s for s in symbols if s not in next_open_map]
			logger.warning(f"[NEXT_DAY_PRICE] Warning: failed to get opening prices for the following symbols: {missing_symbols}")
		
		return next_open_map

	def _create_trade_record(self, symbol: str, qty: float, exec_price: float, exec_ref_price: float,
							filled_qty: float, net_cost: float, pf: Portfolio, open_prices: Dict[str, float],
							ts: pd.Timestamp) -> TradeRecord:
		"""Create a detailed trade record"""
		pos = pf.positions.get(symbol)
		
		# Pre-trade state
		cash_before = pf.cash + net_cost  # Restore pre-trade cash
		position_before = pos.shares - filled_qty if pos else -filled_qty
		avg_price_before = pos.avg_price if pos and pos.shares != filled_qty else 0.0
		
		# Compute pre-trade portfolio state
		temp_pf = Portfolio(cash=cash_before, positions=pf.positions.copy())
		if symbol in temp_pf.positions:
			temp_pf.positions[symbol] = Position(
				shares=position_before,
				avg_price=avg_price_before,
				holding_days=pos.holding_days if pos else 0
			)
		
		# Temporarily use None for previous_open_prices because this is mainly a trade record
		total_equity_before = temp_pf.get_total_position_value(open_prices, None) + cash_before
		total_position_value_before = temp_pf.get_total_position_value(open_prices, None)
		
		# Compute unrealized P&L before trade
		unrealized_pnl_before = 0.0
		for sym, pos_temp in temp_pf.positions.items():
			if pos_temp.shares != 0:
				# Price fallback priority: opening price -> cost price (use opening price for valuation)
				mark_price = open_prices.get(sym, pos_temp.avg_price)
				unrealized_pnl_before += pos_temp.shares * (mark_price - pos_temp.avg_price)
		
		# Post-trade state
		cash_after = pf.cash
		position_after = pos.shares if pos else 0
		avg_price_after = pos.avg_price if pos else 0.0
		
		total_equity_after = pf.equity(open_prices, None)
		total_position_value_after = pf.get_total_position_value(open_prices, None)
		
		# Compute unrealized P&L after trade
		unrealized_pnl_after = 0.0
		for sym, pos_temp in pf.positions.items():
			if pos_temp.shares != 0:
				# Price fallback priority: opening price -> cost price (use opening price for valuation)
				mark_price = open_prices.get(sym, pos_temp.avg_price)
				unrealized_pnl_after += pos_temp.shares * (mark_price - pos_temp.avg_price)
		
		# Compute realized P&L (when selling)
		realized_pnl = 0.0
		if filled_qty < 0 and position_before > 0:  # Sell
			realized_pnl = filled_qty * (exec_price - avg_price_before)
		
		# Compute trade amount and commission
		# Note: use opening price (exec_ref_price) for trade amount, not execution price (exec_price)
		trade_value = abs(filled_qty * exec_ref_price)
		commission = self._apply_commission(trade_value)
		
		return TradeRecord(
			timestamp=ts.isoformat() if hasattr(ts, 'isoformat') else str(ts),
			symbol=symbol,
			side="buy" if filled_qty > 0 else "sell",
			qty=abs(filled_qty),
			exec_price=exec_price,  # Execution price (with slippage, used for average position price)
			exec_ref_price=exec_ref_price,  # Opening price (used for cash update)
			commission_bps=self.commission_bps,
			fill_ratio=self.fill_ratio,
			trade_value=trade_value,  # Trade amount calculated using opening price
			commission=commission,
			net_cost=net_cost,
			cash_before=cash_before,
			cash_after=cash_after,
			position_before=position_before,
			position_after=position_after,
			avg_price_before=avg_price_before,
			avg_price_after=avg_price_after,
			total_equity_before=total_equity_before,
			total_equity_after=total_equity_after,
			total_position_value_before=total_position_value_before,
			total_position_value_after=total_position_value_after,
			unrealized_pnl_before=unrealized_pnl_before,
			unrealized_pnl_after=unrealized_pnl_after,
			realized_pnl=realized_pnl
		)

	def _save_trading_logs(self, trade_records: List[TradeRecord], portfolio_snapshots: List[PortfolioSnapshot], 
					  output_dir: str) -> None:
		"""Save trade records and portfolio snapshots"""
		if not self.enable_detailed_logging:
			return
		
		try:
			import os
			os.makedirs(output_dir, exist_ok=True)
			
			# Save trade records
			trades_file = os.path.join(output_dir, "detailed_trades.jsonl")
			with open(trades_file, 'w', encoding='utf-8') as f:
				for record in trade_records:
					f.write(json.dumps(record.__dict__, ensure_ascii=False, default=str) + '\n')
			
			# Save portfolio snapshots
			snapshots_file = os.path.join(output_dir, "detailed_portfolio_snapshots.jsonl")
			with open(snapshots_file, 'w', encoding='utf-8') as f:
				for snapshot in portfolio_snapshots:
					f.write(json.dumps(snapshot.__dict__, ensure_ascii=False, default=str) + '\n')
			
			# Save summary
			summary = {
				"total_trades": len(trade_records),
				"total_snapshots": len(portfolio_snapshots),
				"initial_cash": self.initial_cash,
				"final_cash": portfolio_snapshots[-1].cash if portfolio_snapshots else 0.0,
				"final_equity": portfolio_snapshots[-1].total_equity if portfolio_snapshots else 0.0,
				"final_nav": portfolio_snapshots[-1].nav if portfolio_snapshots else 1.0,
				"trading_summary": {
					"buy_trades": len([t for t in trade_records if t.side == "buy"]),
					"sell_trades": len([t for t in trade_records if t.side == "sell"]),
					"total_commission": sum(t.commission for t in trade_records),
					"total_realized_pnl": sum(t.realized_pnl for t in trade_records)
				}
			}
			
			summary_file = os.path.join(output_dir, "detailed_trading_summary.json")
			with open(summary_file, 'w', encoding='utf-8') as f:
				json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
				
		except Exception as e:
			logger.error(f"Failed to save trading records: {e}")

	def _ensure_position(self, pf: Portfolio, symbol: str) -> Position:
		if symbol not in pf.positions:
			pf.positions[symbol] = Position()
		return pf.positions[symbol]

	def _apply_commission(self, notional: float) -> float:
		return notional * (self.commission_bps / 10_000.0)

	def _fill_at_open(self, symbol: str, trade_date: pd.Timestamp, qty: float, open_price: float) -> Tuple[float, float, float]:
		# Match at open price, considering slippage, commission and fill ratio
		logger.info(f"=== Cash flow calculation started [{symbol}] ===")
		logger.info(f"[CASH_FLOW] Initial params: symbol={symbol}, qty={qty}, open_price={open_price:.4f}")
		
		side = 1 if qty > 0 else -1
		logger.info(f"[CASH_FLOW] Trade side: {'BUY' if side > 0 else 'SELL'} (side={side})")
		
		# Compute slippage price for updating average position price
		px = self.slippage.apply_buy(open_price) if side > 0 else self.slippage.apply_sell(open_price)
		logger.debug(f"[CASH_FLOW] Price after slippage: {px:.4f} (original: {open_price:.4f})")
		
		planned_qty = abs(qty)
		filled_qty = round(planned_qty * max(0.0, min(1.0, self.fill_ratio)), 2)  # keep two decimals
		filled_qty = filled_qty * side
		logger.debug(f"[SHARES_CALCULATION] {symbol}: using open {open_price:.4f} (date: {trade_date})")
		logger.debug(f"[SHARES_CALCULATION] {symbol}: planned_shares={planned_qty}, fill_ratio={self.fill_ratio}, filled_shares={filled_qty:.2f}")
		
		# Use raw opening price to compute gross notional and net cost
		gross_open = open_price * abs(filled_qty)
		logger.debug(f"[CASH_FLOW] Gross notional (using open): {open_price:.4f} × {abs(filled_qty):.2f} = {gross_open:.2f}")
		
		commission = self._apply_commission(gross_open)
		logger.debug(f"[CASH_FLOW] Commission: {gross_open:.2f} × {self.commission_bps/10000:.4f} = {commission:.2f}")
		
		net_cost = gross_open + commission if side > 0 else -(gross_open - commission)
		logger.debug(f"[CASH_FLOW] Net cost calc: {'BUY' if side > 0 else 'SELL'}")
		if side > 0:
			logger.debug(f"[CASH_FLOW]   Buy net cost = gross + commission = {gross_open:.2f} + {commission:.2f} = {net_cost:.2f}")
		else:
			logger.debug(f"[CASH_FLOW]   Sell net proceeds = -(gross - commission) = -({gross_open:.2f} - {commission:.2f}) = {net_cost:.2f}")
		
		logger.info(f"[CASH_FLOW] Final: filled_qty={filled_qty:.2f}, exec_px={px:.4f} (for average cost), open_price={open_price:.4f} (for cash), net_cost={net_cost:.2f}")
		logger.info(f"=== Cash flow calculation ended [{symbol}] ===")
		
		return filled_qty, px, net_cost

	def _apply_corporate_actions(self, pf: Portfolio, symbol: str, date: pd.Timestamp) -> None:
		# Handle splits and dividends:
		# - Split: adjust shares and avg_price by ratio (keep position market value unchanged)
		# - Dividend: on ex-date, add cash dividend to pf.cash (only for long positions)
		pos = pf.positions.get(symbol)
		if pos is None or (pos.shares == 0 and pos.avg_price == 0.0):
			return
		
		# Read cached corporate actions (pre-populated by run)
		splits_df = getattr(self, "_cached_splits", {}).get(symbol)
		div_df = getattr(self, "_cached_dividends", {}).get(symbol)
		
		# Current date string
		d_str = date.strftime("%Y-%m-%d")
		
		# 1) Split: find records effective today; common fields: execution_date/effective_date
		try:
			if splits_df is not None and not splits_df.empty:
				df = splits_df.copy()
				# Compatible with common field names
				date_col = None
				for c in ["execution_date", "effective_date", "ex_date", "split_date", "announced_date"]:
					if c in df.columns:
						date_col = c
						break
				if date_col:
					rows = df[df[date_col].astype(str).str[:10] == d_str]
					for _, r in rows.iterrows():
						# Ratio fields: could be split_to/split_from or numerator/denominator, or direct ratio
						ratio = None
						for a, b in [("split_to", "split_from"), ("numerator", "denominator")]:
							if a in df.columns and b in df.columns:
								try:
									up = float(r[a])
									down = float(r[b])
									if up > 0 and down > 0:
										ratio = up / down
										break
								except Exception:
									pass
						if ratio is None and "ratio" in df.columns:
							try:
								ratio = float(r["ratio"]) if float(r["ratio"]) > 0 else None
							except Exception:
								ratio = None
						if ratio and ratio > 0:
							# shares multiplied by ratio; avg_price divided by ratio (keep nominal total cost roughly unchanged)
							old_shares = pos.shares
							pos.shares = round(pos.shares * ratio, 2)  # keep two decimals
							logger.info(f"[SHARES_CALCULATION] Split {symbol}: {old_shares:.2f} × {ratio} = {pos.shares:.2f} (date: {date})")
							if pos.shares != 0 and old_shares != 0:
								pos.avg_price = float(pos.avg_price) / ratio
							# Do not adjust cash on split day
		except Exception:
			pass
		
		# 2) Dividend: find cash dividends whose ex-/payment date is today
		try:
			if div_df is not None and not div_df.empty and pos.shares > 0:
				df = div_df.copy()
				# Identify date: prefer ex_dividend_date; then pay_date/payment_date; finally declared_date
				date_col = None
				for c in ["ex_dividend_date", "pay_date", "payment_date", "declaration_date", "declared_date"]:
					if c in df.columns:
						date_col = c
						break
				if date_col and date_col in df.columns:
					rows = df[df[date_col].astype(str).str[:10] == d_str]
					# Cash fields: cash_amount or amount or dividend
					for _, r in rows.iterrows():
						cash = None
						for c in ["cash_amount", "amount", "dividend", "cash"]:
							if c in df.columns:
								try:
									cash = float(r[c])
									break
								except Exception:
									cash = None
						if cash and cash != 0.0:
							# Use safe cash update method
							dividend_amount = float(pos.shares) * cash
							logger.info(f"\n=== Dividend processing [{symbol}] ===")
							logger.info(f"[DIVIDEND] Ex-dividend date: {d_str}")
							logger.info(f"[DIVIDEND] Shares held: {pos.shares:.2f}")
							logger.info(f"[DIVIDEND] Dividend per share: {cash:.4f}")
							logger.info(f"[DIVIDEND] Total dividend: {pos.shares:.2f} × {cash:.4f} = {dividend_amount:.2f}")
							logger.info(f"[DIVIDEND] Cash before: {pf.cash:.2f}")
							
							if pf.update_cash(dividend_amount):
								logger.info(f"[DIVIDEND] Cash after: {pf.cash:.2f}")
								logger.info(f"[DIVIDEND] Cash increased: +{dividend_amount:.2f}")
							else:
								logger.warning(f"[DIVIDEND] Dividend processing failed")
							logger.info(f"=== Dividend processing completed ===\n")
		except Exception:
			pass

	def _enforce_max_positions(self, pf: Portfolio, orders: List[Dict]) -> List[Dict]:
		if self.max_positions >= 999999:
			return orders
		long_symbols = {s for s, p in pf.positions.items() if p.shares > 0}
		buy_orders = [o for o in orders if o.get("qty", 0) > 0]
		sell_orders = [o for o in orders if o.get("qty", 0) <= 0]
		remain_slots = max(0, self.max_positions - len(long_symbols))
		buy_orders = buy_orders[:remain_slots]
		filtered = buy_orders + sell_orders
		return filtered

	def run(self, strategy, start: str, end: str, symbols: List[str], timespan: str = "day", run_id: str = None) -> Dict:
		"""
		Backtesting main loop.

		Parameters:
		- strategy: Strategy instance, must implement on_bar(ctx)
		- start/end: Backtest start/end dates (YYYY-MM-DD)
		- symbols: List of symbols
		- timespan: Matching time granularity, currently only supports "day"
		- run_id: Backtest run ID, used to generate output directory
		  - day: Opening reference price and valuation price take daily open/close respectively

		Description:
		- Strategy context ctx will contain timespan, strategy can decide how to build features accordingly
		"""
		# Set run_id attribute for strategy context use
		self.run_id = run_id
		

		
		dates = pd.date_range(start=start, end=end, freq="B")
		nav = []
		trade_rows = []
		# Create portfolio, prioritize using portfolio.total_cash
		portfolio_cash = float(self.cfg.get("portfolio", {}).get("total_cash", 1_000_000))
		pf = Portfolio(cash=float(self.cfg.get("backtest", {}).get("cash", portfolio_cash)))
		
		# New: Trading record and portfolio snapshot collection
		trade_records: List[TradeRecord] = []
		portfolio_snapshots: List[PortfolioSnapshot] = []
		
		# Pre-fetch and cache corporate action data to avoid repeated IO in loops
		self._cached_dividends = {}
		self._cached_splits = {}
		for s in symbols:
			try:
				self._cached_dividends[s] = self.datasets.get_dividends(s)
				self._cached_splits[s] = self.datasets.get_splits(s)
			except Exception:
				self._cached_dividends[s] = None
				self._cached_splits[s] = None

		for i, d in enumerate(dates):
			# Check if it's a trading day; if not, skip processing and recording
			from stockbench.core.data_hub import is_trading_day
			if not is_trading_day(d):
				logger.info(f"\n[SKIP_NON_TRADING_DAY] {d.strftime('%Y-%m-%d')} skip non-trading day")
				continue
			
			# Reset re-decision flags for the day
			self._redecide_flags = set()
			# Corporate action placeholder processing (per symbol before open)
			for s in symbols:
				self._apply_corporate_actions(pf, s, d)

			# Compute each symbol's matching price and mark price for the day
			open_map: Dict[str, float] = {}
			mark_map: Dict[str, float] = {}
			# Daily mode: get open and close prices
			# Store previous day's price as fallback
			previous_open_map = {}
			if len(portfolio_snapshots) > 0:
				# Use latest snapshot prices as historical fallback
				last_snapshot = portfolio_snapshots[-1]
				for symbol, symbol_info in last_snapshot.positions.items():
					if hasattr(symbol_info, 'mark_price') and symbol_info.mark_price:
						previous_open_map[symbol] = symbol_info.mark_price
				logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} build previous_open_map: {len(previous_open_map)} symbols")
				if previous_open_map:
					for symbol, price in previous_open_map.items():
						logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} previous_open_map[{symbol}] = {price}")
			
			# Holiday detection (no symbols have data)
			market_data_available = False
			symbols_with_data = []
			symbols_without_data = []
			
			for s in symbols:
				bars = self.datasets.get_day_bars(s, d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d"))
				if not bars.empty:
					try:
						row = bars.loc[bars["date"] == d.date()].iloc[0]
						open_px = float(row["open"])
						close_px = float(row["close"]) if "close" in row else open_px
						logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} {s}: got open {open_px}, close {close_px}")
						market_data_available = True
						symbols_with_data.append(s)
					except Exception as e:
						open_px = None
						close_px = None
						symbols_without_data.append(s)
						logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} {s}: failed to parse prices - {e}")
				else:
					open_px = None
					close_px = None
					symbols_without_data.append(s)
					logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} {s}: daily bars empty (possibly holiday)")
				
				# Add price data fallback mechanism
				if open_px is not None:
					open_map[s] = open_px
				else:
				# Try to use previous day's open as today's open
					if s in previous_open_map:
						open_map[s] = previous_open_map[s]
						logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} {s}: using previous day's open as open {previous_open_map[s]}")
					else:
						logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} {s}: open_px is None, not added to open_map")
				
				# Use open price as mark price
				if open_px is not None:
					mark_map[s] = open_px
					logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} {s}: using open as mark price {open_px}")
				elif s in previous_open_map:
					mark_map[s] = previous_open_map[s]
					logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} {s}: using previous day's open as mark price {previous_open_map[s]}")
			
			# Holiday detection and logging
			if not market_data_available:
				logger.info(f"\n[HOLIDAY_DETECTED] {d.strftime('%Y-%m-%d')} market holiday detected: no symbols have market data")
				logger.info(f"[HOLIDAY_DETECTED] Will use previous trading day's prices for valuation")
				if previous_open_map:
					logger.info(f"[HOLIDAY_DETECTED] Available previous-day open prices: {len(previous_open_map)} symbols")
				else:
					logger.info(f"[HOLIDAY_DETECTED] Warning: no previous-day prices available")
			elif len(symbols_without_data) > 0:
				logger.info(f"\n[PARTIAL_DATA] {d.strftime('%Y-%m-%d')} some symbols missing market data:")
				logger.info(f"[PARTIAL_DATA] with data: {symbols_with_data}")
				logger.info(f"[PARTIAL_DATA] without data (will use previous day): {symbols_without_data}")
			
			# Use unified fallback mechanism to further supplement prices
			from stockbench.core.price_utils import add_price_fallback_mechanism
			is_holiday = not market_data_available
			open_map = add_price_fallback_mechanism(open_map, previous_open_map, 100.0, is_holiday)
			mark_map = add_price_fallback_mechanism(mark_map, previous_open_map, 100.0, is_holiday)
			
			# Summary log: show final open_map state
			logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} open_map built: {len(open_map)} symbols")
			for sym, px in open_map.items():
				logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} open_map[{sym}] = {px}")
			if not open_map:
				logger.info(f"[DEBUG] {d.strftime('%Y-%m-%d')} Warning: open_map is empty!")

			# Strategy hook: provide context (including timespan) for strategy feature construction
			# Unified reference price:
			# - day: choose sizing reference (open or close) based on rebalance_at_open
			ref_price_map: Dict[str, float] = dict(open_map if self.rebalance_at_open else mark_map)
			def _equity_with(price_map: Dict[str, float]) -> float:
				# Mark-to-market portfolio equity with given prices
				mark_to_market = 0.0
				for s, pos in pf.positions.items():
					px = price_map.get(s, pos.avg_price)
					mark_to_market += pos.shares * px
				return pf.cash + mark_to_market

			ctx = {
				"date": d,
				"timespan": timespan,
				"symbols": symbols,
				"datasets": self.datasets,
				# New keys
				"open_price_map": open_map,
				"mark_price_map": mark_map,
				"ref_price_map": ref_price_map,
				"equity_at_open": _equity_with(open_map),
				"equity_at_mark": _equity_with(mark_map),
				# Added: provide equity_for_sizing for LLM strategy
				"equity_for_sizing": _equity_with(open_map),
				# Added: provide run_id for organizing cache directories
				"run_id": getattr(self, 'run_id', None),
				# Backward-compatible keys
				"open_map": open_map,
				"portfolio": pf,
				"cfg": self.cfg,
			}

			# Implement order retry mechanism  
			# Get retry configuration from config (same as dual_agent_llm.py)
			retry_cfg = self.cfg.get("agents", {}).get("retry", {}) if self.cfg else {}
			max_retries = int(retry_cfg.get("max_attempts", 3))  # Business-level retries
			retry_attempt = 0
			rejected_orders = []
			
			# CRITICAL FIX: Add position state validation before strategy execution
			self._validate_portfolio_position_consistency(pf, ctx, d)
			
			while retry_attempt < max_retries:
				# Add rejected_orders to context for retry attempts
				if rejected_orders:
					ctx["rejected_orders"] = rejected_orders
					logger.info(f"[RETRY_MECHANISM] Attempt {retry_attempt + 1}: Retrying with {len(rejected_orders)} rejected orders")
				else:
					ctx.pop("rejected_orders", None)  # Remove if no rejected orders
					
				orders = strategy.on_bar(ctx)
				if not isinstance(orders, list):
					orders = []
				orders = self._enforce_max_positions(pf, orders)
				
				# Track successfully executed trades for delayed decision recording
				executed_symbols = []

				# Daily mode: match orders at opening price
				# Daily processing:
				# Pre-check orders for possible rejection; if rejected, unified retry mechanism will handle
				current_rejected_orders = []
				acceptable_orders = []
				
				# Pre-calculate total cash requirement to provide better context for rejections
				total_cash_required = 0
				order_costs = {}  # Cache to avoid recalculating
				
				for od in orders:
					sym = od.get("symbol")
					qty = round(float(od.get("qty", 0)), 2) if od.get("qty") else 0
					if sym and qty != 0 and open_map.get(sym) is not None:
						try:
							filled, exec_px, net_cost = self._fill_at_open(sym, d, qty, float(open_map.get(sym)))
							order_costs[sym] = (filled, exec_px, net_cost)
							if filled > 0 and net_cost > 0:  # Only count buying orders
								total_cash_required += net_cost
						except Exception as e:
							logger.warning(f"[PRECALC] {sym} cost calculation failed: {e}")
							continue
				
				logger.info(f"[PRECALC] Total cash required for all orders: {total_cash_required:.2f}, available: {pf.cash:.2f}")
				
				for od in orders:
					try:
						sym = od.get("symbol")
						qty = round(float(od.get("qty", 0)), 2)
						if not sym or qty == 0:
							continue
						ref_px_i = open_map.get(sym)
						if ref_px_i is None:
							continue
						
						# Use pre-calculated costs if available, otherwise calculate now
						if sym in order_costs:
							filled, exec_px, net_cost = order_costs[sym]
						else:
							filled, exec_px, net_cost = self._fill_at_open(sym, d, qty, float(ref_px_i))
						
						# Cash sufficiency check
						if filled > 0 and net_cost > 0:  # only check buy trades
							# Debug detailed cash comparison
							logger.info(f"[DAY_PRECHECK_DEBUG] {sym}: pf.cash={pf.cash} (type: {type(pf.cash)}), net_cost={net_cost} (type: {type(net_cost)})")
							logger.info(f"[DAY_PRECHECK_DEBUG] {sym}: pf.cash < net_cost = {pf.cash < net_cost}")
							logger.info(f"[DAY_PRECHECK_DEBUG] {sym}: pf.cash >= net_cost = {pf.cash >= net_cost}")
							
							if pf.cash < net_cost:
								current_rejected_orders.append({
									"symbol": sym,
									"qty": qty,
									"reason": "insufficient_cash",  # Match the expected format in dual_agent_llm.py
									"rejection_reason": "insufficient_cash",
									"context": {
										"required_cash_this_order": net_cost,
										"available_cash": pf.cash,
										"total_cash_required_all_orders": total_cash_required,
										"cash_shortfall": total_cash_required - pf.cash,
										"retry_attempt": retry_attempt,
										"all_orders_count": len(orders),
										"portfolio_rebalance_needed": True,
										"suggestion": f"Total portfolio cash requirement ({total_cash_required:.2f}) exceeds available cash ({pf.cash:.2f}) by {total_cash_required - pf.cash:.2f}. Please reduce all order sizes proportionally or select fewer positions to fit within budget."
									},
									"retry_count": retry_attempt
								})
								logger.info(f"[DAY_PRECHECK] {sym} precheck rejected: insufficient cash {pf.cash:.2f} < required {net_cost:.2f}")
								logger.info(f"[DAY_PRECHECK] Total cash shortfall: {total_cash_required - pf.cash:.2f}")
								continue
							else:
								logger.info(f"[DAY_PRECHECK] {sym} precheck passed: sufficient cash {pf.cash:.2f} >= required {net_cost:.2f}")
						
						acceptable_orders.append(od)
						
					except Exception as e:
						logger.warning(f"[DAY_PRECHECK] {sym if 'sym' in locals() else 'UNKNOWN'} precheck exception: {e}")
						continue
				
				# Check if we should retry or proceed with execution
				if current_rejected_orders:
					rejected_orders = current_rejected_orders
					retry_attempt += 1
					if retry_attempt < max_retries:
						logger.info(f"[RETRY_MECHANISM] Detected {len(rejected_orders)} rejected orders; will retry (attempt {retry_attempt + 1}/{max_retries})")
						continue  # Retry with rejected orders
					else:
						logger.warning(f"[RETRY_MECHANISM] Max retries ({max_retries} from config) reached; proceeding with {len(acceptable_orders)} acceptable orders")
						break  # No more retries, proceed with acceptable orders
				else:
					logger.info(f"[RETRY_MECHANISM] No rejected orders; proceeding with execution")
					break  # No rejected orders, proceed with execution
			
			# Execute acceptable orders
			for od in acceptable_orders:
				try:
					sym = od.get("symbol")
					qty = round(float(od.get("qty", 0)), 2)
					if not sym or qty == 0:
						continue
					ref_px_i = open_map.get(sym)
					if ref_px_i is None:
						continue
					filled, exec_px, net_cost = self._fill_at_open(sym, d, qty, float(ref_px_i))
					# Re-decision: if position would become 0 after execution, ask strategy for new qty
					pos_preview = self._ensure_position(pf, sym)
					new_qty = self._maybe_redecide_qty(strategy, ctx, sym, pos_preview.shares, filled, ("day", d.date(), sym))
					if isinstance(new_qty, int):
						if new_qty == 0:
							continue
						filled, exec_px, net_cost = self._fill_at_open(sym, d, new_qty, float(ref_px_i))
					if filled != 0:
						logger.info(f"\n=== DAY trade processing [{sym}] ===")
						logger.info(f"[DAY_TRADE] trade date: {d.date()}")
						logger.info(f"[DAY_TRADE] params: filled={filled}, exec_px={exec_px:.4f}, net_cost={net_cost:.2f}")
						logger.info(f"[DAY_TRADE] current cash: {pf.cash:.2f}")
						
						# Final cash sufficiency check (should have been handled by retry mechanism)
						if filled > 0 and net_cost > 0:  # only check buys
							if pf.cash < net_cost:
								logger.info(f"[BACKTEST_CASH_CHECK] {sym} trade still rejected: insufficient cash {pf.cash:.2f} < required {net_cost:.2f}")
								logger.info(f"[BACKTEST_CASH_CHECK] This should not happen; retry mechanism may have issues")
								logger.info(f"=== DAY trade processing skipped [{sym}] ===\n")
								continue
						
					pos = self._ensure_position(pf, sym)
					
					logger.info(f"[DAY_TRADE] Position update start")
					logger.info(f"[DAY_TRADE] Before: shares={pos.shares:.2f}, avg_price={pos.avg_price:.4f}")
					
					# Update avg price and shares
					if (pos.shares >= 0 and filled > 0) or (pos.shares <= 0 and filled < 0) or pos.shares == 0:
						# CRITICAL FIX: Initialize old_shares inside the if block to prevent using stale value
						old_shares = pos.shares  # save original shares for cost calc
						new_shares = pos.shares + filled
						logger.info(f"[DAY_TRADE] New shares: {pos.shares:.2f} + {filled:.2f} = {new_shares:.2f}")
						if new_shares != 0:
							old_avg_price = pos.avg_price
							pos.avg_price = (pos.avg_price * abs(pos.shares) + abs(filled) * exec_px) / abs(new_shares)
							logger.info(f"[DAY_TRADE] Avg price calc: ({old_avg_price:.4f} × {abs(pos.shares):.2f} + {abs(filled):.2f} × {exec_px:.4f}) / {abs(new_shares):.2f} = {pos.avg_price:.4f}")
						pos.shares = new_shares
						# Update total cost: buy increases cost; sell reduces proportionally
						if filled > 0:  # buy
							pos.total_cost += net_cost
							logger.info(f"[DAY_TRADE] Buy total cost: {pos.total_cost-net_cost:.2f} + {net_cost:.2f} = {pos.total_cost:.2f}")
						else:  # sell
							if new_shares != 0:  # partial sell
								cost_ratio = abs(filled) / abs(old_shares)  # sold / original
								cost_reduction = pos.total_cost * cost_ratio
								pos.total_cost -= cost_reduction
								logger.info(f"[DAY_TRADE] Partial sell cost reduction: {cost_ratio:.4f} × cost = {cost_reduction:.2f}, new cost: {pos.total_cost:.2f}")
							else:  # full exit
								pos.total_cost = 0.0
								logger.info(f"[DAY_TRADE] Full exit, cost reset to 0")
					else:
						# Handle the case where condition in line 1253 is false
						logger.info(f"[DAY_TRADE] Reverse trade handling")
						pos.shares += filled
						logger.info(f"[DAY_TRADE] Update shares: {pos.shares:.2f}")
						if pos.shares == 0:
							pos.avg_price = 0.0
							pos.total_cost = 0.0
							logger.info(f"[DAY_TRADE] Position zeroed, reset avg_price and total_cost to 0")
					
					logger.info(f"[DAY_TRADE] After: shares={pos.shares:.2f}, avg_price={pos.avg_price:.4f}")
						
					# Safe cash update (cash already checked) - moved outside else block
					logger.info(f"[DAY_TRADE] Update cash by net cost: -{net_cost:.2f}")
					if not pf.update_cash(-net_cost):
						logger.warning(f"[CASH_PROTECTION] Unexpected: cash update failed, cannot pay {net_cost:.2f}")
						logger.info(f"[CASH_PROTECTION] This should not happen; cash was pre-checked")
						continue
							
					logger.info(f"=== DAY trade processing completed [{sym}] ===\n")
					
					# New: Create detailed trade record
					trade_record = self._create_trade_record(
						sym, qty, exec_px, float(ref_px_i), filled, net_cost, pf, open_map, d
					)
					trade_records.append(trade_record)
					
					trade_rows.append({
						"ts": d,
						"symbol": sym,
						"side": "buy" if filled > 0 else "sell",
						"qty": abs(filled),
						"exec_price": exec_px,  # Execution price (with slippage, used for average position price)
						"open_price": open_map.get(sym),
						"mark_price": open_map.get(sym, float(ref_px_i)),
						"exec_ref_price": float(ref_px_i),  # Opening price (used for cash update)
						"commission_bps": self.commission_bps,
						"fill_ratio": self.fill_ratio,
						"trade_value": abs(filled) * float(ref_px_i),  # Trade amount calculated using opening price
					})
					
					# Record successfully executed symbol for delayed decision recording
					if sym not in executed_symbols:
						executed_symbols.append(sym)
						logger.info(f"[DELAYED_RECORD] Daily trade successful, marking symbol: {sym}")
				except Exception as e:
					logger.warning(f"[DAY_TRADE] Processing exception: {e}")

			# Daily processing (minute-level support removed)
				# Daily bar timeline (take the first symbol with data as baseline, then take union with other symbols)

			# Calculate daily NAV
			logger.info(f"\n=== Daily NAV Calculation [{d.date()}] ===")
			logger.info(f"[DAILY_NAV] Current cash: {pf.cash:.2f}")
			logger.info(f"[DAILY_NAV] Position details:")
			
			mark_to_market = 0.0
			for s, pos in pf.positions.items():
				# Price fallback priority: opening price -> previous trading day opening price -> purchase cost price
				px = open_map.get(s)
				if px is None and previous_open_map:
					px = previous_open_map.get(s)
				if px is None:
					px = pos.avg_price
				position_value = pos.shares * px
				mark_to_market += position_value
				logger.info(f"[DAILY_NAV]   {s}: {pos.shares:.2f} × {px:.4f} = {position_value:.2f}")
				if pos.shares > 0:
					pos.holding_days += 1
			
			initial_cash = float(self.cfg.get("backtest", {}).get("cash", portfolio_cash))
			total_equity = pf.cash + mark_to_market
			daily_nav = total_equity / initial_cash
			
			logger.info(f"[DAILY_NAV] Total position market value: {mark_to_market:.2f}")
			logger.info(f"[DAILY_NAV] Total equity: {pf.cash:.2f} + {mark_to_market:.2f} = {total_equity:.2f}")
			logger.info(f"[DAILY_NAV] Initial capital: {initial_cash:.2f}")
			logger.info(f"[DAILY_NAV] Daily NAV: {total_equity:.2f} / {initial_cash:.2f} = {daily_nav:.6f}")
			logger.info(f"[DAILY_NAV] Current cash ratio: {pf.cash / total_equity:.4f}")
			logger.info(f"=== Daily NAV Calculation Completed ===\n")
			
			nav.append({"date": d, "nav": daily_nav})
			
			# Record executed decisions after all trading is complete
			if hasattr(strategy, 'record_executed_decisions'):
				if executed_symbols:
					logger.info(f"[DELAYED_RECORD] Daily trading completed, recording executed decisions")
				else:
					logger.info(f"[DELAYED_RECORD] No successful trades today, but still need to record hold decisions")
				strategy.record_executed_decisions(executed_symbols, portfolio=pf)
			
			# New: Create daily portfolio snapshot (using next trading day's opening prices to calculate position values)
			# Snapshot date uses current date, recording the portfolio state after trading for the day
			# To maintain consistency with the price baseline for next day's LLM decisions, use next trading day's opening prices for calculation
			from stockbench.core.data_hub import get_next_trading_day, is_trading_day
			
			# Find the next trading day
			next_trading_date = None
			try:
				# Find the next trading day from the current date
				next_trading_date = get_next_trading_day(d)
				logger.info(f"[NEXT_TRADING_DAY] Next trading day after {d.strftime('%Y-%m-%d')}: {next_trading_date.strftime('%Y-%m-%d')}")
			except Exception as e:
				logger.warning(f"[NEXT_TRADING_DAY_ERROR] Failed to find next trading day: {e}")
			
			if next_trading_date is not None:
				# Use next trading day's opening prices
				next_open_map = self._get_next_day_open_prices(next_trading_date, symbols)
				portfolio_snapshot = self._create_portfolio_snapshot(pf, d, next_open_map, previous_open_prices=open_map)
			else:
				# If next trading day cannot be found, use current day's opening prices
				portfolio_snapshot = self._create_portfolio_snapshot(pf, d, open_map, previous_open_prices=previous_open_map)
			portfolio_snapshots.append(portfolio_snapshot)

		# Summary output
		if not nav:
			nav_df = pd.Series(dtype=float, name="nav")
			trades = pd.DataFrame(trade_rows)
			metrics = evaluate(nav_df, trades)
			
			# New: Save trade records
			if self.enable_detailed_logging:
				# Save detailed records directly in main report directory, avoiding creating subdirectories
				if run_id:
					output_dir = f"storage/reports/backtest/{run_id}"
				else:
					output_dir = f"storage/reports/backtest"
				self._save_trading_logs(trade_records, portfolio_snapshots, output_dir)
			
			return {"nav": nav_df, "trades": trades, "metrics": metrics}

		nav_df = pd.DataFrame(nav).set_index("date")["nav"].astype(float)
		trades = pd.DataFrame(trade_rows)

		# Benchmark and metrics (maintain original logic, and add per-symbol buy&hold benchmark)
		benchmark_nav: pd.Series | None = None
		per_symbol_bh_nav_df: pd.DataFrame | None = None
		per_symbol_bh_metrics: Dict[str, pd.DataFrame] | None = None
		try:
			bench_cfg = (self.cfg.get("backtest", {}) or {}).get("benchmark")
			if bench_cfg and isinstance(bench_cfg, dict):
				# New type: buy and hold by symbol
				type_str = str(bench_cfg.get("type", "")).lower()
				if type_str == "per_symbol_buy_and_hold":
					# Build per-symbol NAV (no alignment first, then align to strategy dates according to configuration)
					trade_price_field = str(bench_cfg.get("trade_price_field", "open")).lower()
					# Temporarily inject datasets into cfg for internal loading
					try:
						self.cfg["_datasets"] = self.datasets  # type: ignore
					except Exception:
						pass
					per_symbol_nav_raw = build_per_symbol_bh_benchmark(
						dates=nav_df.index,
						symbols=symbols,
						price_field=trade_price_field,
						cfg=self.cfg,
						commission_bps=float((self.cfg.get("backtest", {}) or {}).get("commission_bps", 0.0)),
						slippage_bps=float((self.cfg.get("backtest", {}) or {}).get("slippage_bps", 0.0)),
					)
					# Alignment and filling
					reindex_mode = str(bench_cfg.get("reindex", "inner_join")).lower()
					fill_mode = str(bench_cfg.get("fill", "ffill")).lower()
					try:
						fill_limit_raw = bench_cfg.get("fill_limit", 0)
						fill_limit_bh = int(fill_limit_raw) if fill_limit_raw is not None else 0
					except Exception:
						fill_limit_bh = 0
					if isinstance(per_symbol_nav_raw, pd.DataFrame) and not per_symbol_nav_raw.empty:
						ps = per_symbol_nav_raw.copy()
						if reindex_mode == "business":
							ps = ps.reindex(nav_df.index)
						else:
							ps = ps.loc[ps.index.isin(nav_df.index)]
						if fill_mode == "ffill":
							lim = None if int(fill_limit_bh or 0) <= 0 else int(fill_limit_bh)
							ps = ps.ffill(limit=lim)
						ps = ps.dropna(how="all")
						per_symbol_bh_nav_df = ps.astype(float)
						# Generate daily metrics
						per_symbol_bh_metrics = {}
						sortino_mode = str(((bench_cfg.get("daily_metrics", {}) or {}).get("sortino", {}) or {}).get("mode", "rolling"))
						window = int(((bench_cfg.get("daily_metrics", {}) or {}).get("sortino", {}) or {}).get("window", 63))
						for sym in ps.columns:
							try:
								# Filter output columns based on configured daily_metrics.metrics
								metrics_list = None
								try:
									metrics_list = list((bench_cfg.get("daily_metrics", {}) or {}).get("metrics", []) or [])
								except Exception:
									metrics_list = None
								met = compute_nav_to_metrics_series(ps[sym].dropna(), sortino_mode=sortino_mode, window=window, metrics=metrics_list)
								per_symbol_bh_metrics[sym] = met
							except Exception:
								continue
					# If per-symbol is enabled, no longer build old single/basket benchmarks unless symbol/basket is also configured
					# Continue execution, allowing users to get old benchmarks simultaneously
				reindex_mode = str(bench_cfg.get("reindex", "inner_join")).lower()
				fill_mode = str(bench_cfg.get("fill", "ffill")).lower()
			fill_limit_raw = bench_cfg.get("fill_limit", 0)
			try:
				fill_limit = int(fill_limit_raw) if fill_limit_raw is not None else 0
			except Exception:
				fill_limit = 0
			# Daily benchmark
			series_dict = load_benchmark_components(bench_cfg, self.datasets, nav_df.index, field="adjusted_close")
			if series_dict:
				ret_df = price_to_returns(series_dict)
				if not ret_df.empty:
					if isinstance(bench_cfg.get("basket"), list) and len(bench_cfg.get("basket", [])) > 0:
						reb = str(bench_cfg.get("rebalance", "daily")).lower()
						r_b = aggregate_with_rebalance(ret_df, bench_cfg.get("basket", []), freq=reb)
						benchmark_nav = (1.0 + r_b).cumprod()
					else:
						# Single benchmark: normalize by price
						price = list(series_dict.values())[0].reindex(ret_df.index).ffill()
						benchmark_nav = price / max(price.iloc[0], 1e-12)
					# Align to strategy NAV index
					benchmark_nav = align_with_strategy_nav(benchmark_nav, nav_df.index, reindex=reindex_mode, fill=fill_mode, fill_limit=fill_limit)
		except Exception as e:
			logger.warning(f"Benchmark calculation failed: {e}")
			benchmark_nav = None
			per_symbol_bh_nav_df = None
			per_symbol_bh_metrics = None

		# Calculate metrics
		metrics = evaluate(nav_df, trades, benchmark_nav)

		# New: Save trade records
		if self.enable_detailed_logging:
			# Save detailed records directly in main report directory, avoiding creating subdirectories
			if run_id:
				output_dir = f"storage/reports/backtest/{run_id}"
			else:
				output_dir = f"storage/reports/backtest"
			self._save_trading_logs(trade_records, portfolio_snapshots, output_dir)

		return {
			"nav": nav_df,
			"trades": trades,
			"metrics": metrics,
			"benchmark_nav": benchmark_nav,
			# New: Return detailed records
			"trade_records": trade_records,
			"portfolio_snapshots": portfolio_snapshots,
			# New: per-symbol buy and hold benchmark
			"per_symbol_benchmark_nav": per_symbol_bh_nav_df,
			"per_symbol_benchmark_metrics": per_symbol_bh_metrics,
		} 
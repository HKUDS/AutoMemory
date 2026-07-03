# Storage Directory

This directory serves as the central data storage and audit repository for the Trading Agent system. It contains all cached data, backtest results, logs, and generated reports organized in a structured manner.

## Directory Structure

```
storage/
├── cache/                    # Cached data from external APIs
│   ├── corporate_actions/    # Stock dividends and splits data
│   ├── financials/           # Financial statements and metrics
│   ├── llm/                  # LLM responses and analysis
│   ├── news/                 # News articles and sentiment data
│   ├── news_by_day/          # Daily organized news by symbol
│   └── stock_indicators/     # Technical indicators and market data
├── logs/                     # System execution logs
├── parquet/                  # Partitioned market data
└── reports/                  # Generated backtest reports
```

## 📁 Cache Directory (`cache/`)

Contains cached data from external data sources to improve performance and reduce API calls.

### `cache/corporate_actions/`
- **Purpose**: Corporate actions data for accurate backtest calculations
- **Format**: JSON files with separate files for dividends and splits
  - `{SYMBOL}.dividends.json`: Dividend payment records
  - `{SYMBOL}.splits.json`: Stock split/spinoff records
- **Content**: 
  - **Dividends**: Cash amounts, ex-dividend dates, payment dates, declaration dates
  - **Stock Splits**: Split ratios, execution dates, adjustment factors
- **Usage**: Critical for portfolio value adjustments during backtesting
- **Examples**: `AAPL.dividends.json`, `MSFT.splits.json`
- **Supported Symbols**: 20+ major US stocks (AAPL, MSFT, GOOGL, AMZN, etc.)

### `cache/financials/`
- **Purpose**: Cached fundamental financial data
- **Format**: JSON files with granular breakdown:
  - `{SYMBOL}.all.json`: Complete financial dataset
  - `{SYMBOL}.annual.json`: Annual financial statements
  - `{SYMBOL}.quarterly.json`: Quarterly financial statements
- **Content**: Company financial statements, ratios, and metrics
- **Examples**: `AAPL.all.json`, `MSFT.quarterly.json`, `GOOGL.annual.json`

### `cache/llm/`
- **Purpose**: Cached LLM responses and analysis
- **Structure**: 
  - `by_run/{RUN_ID}/`: LLM responses organized by backtest run
  - Contains JSON files with LLM analysis and decision logs
- **Format**: JSON and JSONL files

### `cache/news/`
- **Purpose**: Cached news articles and sentiment data
- **Format**: JSON files (1,644+ files)
- **Content**: News headlines, content, timestamps, and sentiment scores

### `cache/news_by_day/`
- **Purpose**: Daily organized news data by symbol
- **Structure**: `{SYMBOL}/` subdirectories containing daily news files
- **Format**: JSON files organized by trading date
- **Content**: Symbol-specific news aggregated by day for efficient access
- **Coverage**: 20+ symbols with 70-120 days of news data per symbol

### `cache/stock_indicators/`
- **Purpose**: Cached technical indicators and market data
- **Format**: JSON files (2,300+ files)
- **Content**: Technical analysis indicators, price patterns, and market signals

## 📊 Parquet Directory (`parquet/`)

Partitioned market data stored in efficient Parquet format for fast querying and analysis.

### Structure
```
parquet/
├── {SYMBOL}/
│   ├── day/                  # Daily OHLCV data
│   │   ├── *.parquet         # Data files
│   │   └── *.sha256          # Integrity checksums
│   └── minute/               # Minute-level OHLCV data
│       ├── *.parquet         # Data files
│       └── *.sha256          # Integrity checksums
```

### Supported Symbols
- **Major Stocks**: AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA, META, JPM, GS, HD, UNH, V, JNJ, PG, MCD, IBM, HON, CAT, BA, AXP, CRM, TRV, SHW, AMGN
- **ETFs**: SPY
- **Total**: 25+ symbols with comprehensive data coverage including corporate actions

### Data Integrity
- Each Parquet file has a corresponding `.sha256` checksum file
- Ensures data integrity and corruption detection
- Supports incremental updates and validation

## 📈 Reports Directory (`reports/`)

Generated backtest reports and analysis results.

### `reports/backtest/{RUN_ID}/`

Each backtest run creates a unique directory with comprehensive results:

#### Core Data Files
- **`trades.parquet`**: Individual trade records with timestamps, symbols, quantities, prices
- **`daily_nav.parquet`**: Daily net asset value (NAV) progression
- **`benchmark_nav.parquet`**: Benchmark performance data (if benchmark configured)
- **`per_symbol_benchmark_nav.parquet`**: Per-symbol benchmark comparisons

#### Configuration & Metadata
- **`config.json`**: Complete configuration snapshot used for the backtest
- **`meta.json`**: Environment and dependency versions
- **`benchmark_meta.json`**: Benchmark configuration details (if applicable)

#### Performance Metrics
- **`metrics.json`**: Comprehensive performance metrics in JSON format
- **`metrics_summary.csv`**: Metrics summary in CSV format for easy analysis
- **`summary.txt`**: Human-readable summary with key performance indicators

#### Analysis Reports
- **`conclusion.md`**: Detailed analysis conclusion and insights
- **`nl_summary.txt`**: Natural language summary of results

#### Visualizations
- **`equity_vs_spy.png`**: Equity curve comparison with S&P 500
- **`excess_return_vs_spy.png`**: Excess return analysis
- **`multi_period_performance_heatmap.png`**: Performance heatmap across periods

#### Per-Symbol Analysis (`per_symbol_benchmark/`)
- **`individual_stocks/`**: Individual stock performance analysis
  - `{SYMBOL}_metrics.png`: Detailed metrics for each symbol
  - `README.md`: Analysis methodology and interpretation guide
- **`benchmark_comparisons/`**: Strategy vs various benchmark comparisons
  - `strategy_vs_spy/`: Comparison with S&P 500
  - `strategy_vs_simple_avg/`: Comparison with simple average
  - `strategy_vs_weighted_avg/`: Comparison with weighted average
- **`aggregated_cumreturn_analysis.png`**: Cumulative return analysis
- **`stock_price_trends.png`**: Price trend visualizations

## 📋 Logs Directory (`logs/`)

System execution logs for debugging and monitoring.

### Log Files
- **`{RUN_ID}.log`**: Detailed execution logs for each backtest run
- **Format**: Timestamped log entries with different severity levels
- **Content**: System status, errors, warnings, and execution details

## 🔧 Data Management

### File Formats
- **Parquet**: Efficient columnar storage for time series data
- **JSON**: Configuration, metadata, and cached API responses
- **CSV**: Human-readable metrics and summaries
- **PNG**: Generated visualizations and charts

### Data Lifecycle
1. **Ingestion**: External data sources (Polygon, Finnhub) → Cache directory
2. **Corporate Actions**: Dividend/split data cached for accurate price adjustments
3. **Processing**: Raw data → Parquet format for analysis
4. **Analysis**: Processed data + corporate actions → Backtest execution
5. **Reporting**: Results → Reports directory with visualizations

### Storage Optimization
- **Partitioning**: Data organized by symbol and time granularity
- **Compression**: Parquet files use efficient compression
- **Checksums**: SHA256 verification for data integrity
- **Incremental Updates**: Only new/changed data is processed

## 📊 Performance Metrics

The system tracks comprehensive performance metrics including:

### Core Metrics
- **Cumulative Return**: Total return over the backtest period
- **Maximum Drawdown**: Largest peak-to-trough decline
- **Volatility**: Annualized price volatility
- **Sharpe Ratio**: Risk-adjusted return measure
- **Sortino Ratio**: Downside risk-adjusted return

### Trading Statistics
- **Trade Count**: Number of executed trades
- **Notional Value**: Total trading volume
- **Fill Ratio**: Percentage of orders filled

### Relative Performance (vs Benchmark)
- **Information Ratio**: Excess return per unit of tracking error
- **Tracking Error**: Volatility of excess returns
- **Alpha**: Risk-adjusted excess return
- **Beta**: Systematic risk exposure
- **Correlation**: Linear relationship with benchmark
- **Capture Ratios**: Performance during up/down markets

### Rolling Metrics
- **Rolling IR/TE**: 63/126/252-day rolling windows
- **Hit Ratio**: Percentage of outperforming days

## 🚀 Usage Examples

### Accessing Market Data
```python
import pandas as pd

# Load daily data for AAPL
df = pd.read_parquet('storage/parquet/AAPL/day/2025-01-01.parquet')
```

### Accessing Corporate Actions Data
```python
import json
import pandas as pd

# Load dividend data for AAPL
with open('storage/cache/corporate_actions/AAPL.dividends.json', 'r') as f:
    dividends = json.load(f)
    dividends_df = pd.DataFrame(dividends)

# Load stock split data for AAPL  
with open('storage/cache/corporate_actions/AAPL.splits.json', 'r') as f:
    splits = json.load(f)
    splits_df = pd.DataFrame(splits)

# Filter dividends for specific date range
recent_dividends = dividends_df[
    pd.to_datetime(dividends_df['ex_dividend_date']) >= '2024-01-01'
]
```

### Accessing Financial Data
```python
# Load complete financial dataset
with open('storage/cache/financials/AAPL.all.json', 'r') as f:
    all_financials = json.load(f)

# Load quarterly financials only
with open('storage/cache/financials/AAPL.quarterly.json', 'r') as f:
    quarterly_financials = json.load(f)
```

### Accessing News Data
```python
# Load daily news for AAPL
with open('storage/cache/news_by_day/AAPL/2024-12-01.json', 'r') as f:
    daily_news = json.load(f)
```

### Analyzing Backtest Results
```python
# Load trade data
trades = pd.read_parquet('storage/reports/backtest/EXP_OPENAI_MAR_7/trades.parquet')

# Load daily NAV
nav = pd.read_parquet('storage/reports/backtest/EXP_OPENAI_MAR_7/daily_nav.parquet')
```

### Viewing Performance Metrics
```python
import json

# Load comprehensive metrics
with open('storage/reports/backtest/EXP_OPENAI_MAR_7/metrics.json', 'r') as f:
    metrics = json.load(f)
```

## 🔍 Troubleshooting

### Common Issues
1. **Missing Data**: Check if symbol exists in parquet directory
2. **Corrupted Files**: Verify SHA256 checksums
3. **Permission Errors**: Ensure write access to storage directories
4. **Disk Space**: Monitor storage usage for large datasets

### Data Validation
- Use checksum files to verify data integrity
- Check log files for data ingestion errors
- Validate Parquet file schemas before processing

## 📝 Notes

- This directory structure is designed for scalability and maintainability
- All timestamps are in UTC unless otherwise specified
- **Corporate Actions Data**: Critical for accurate backtesting - ensures dividend payments and stock splits are properly reflected in portfolio valuations
- **Cache Strategy**: Three-tier approach (local cache → API → offline fallback) ensures reliability
- Data retention policies should be implemented based on storage constraints
- Regular cleanup of old cache and log files is recommended
- Corporate actions data is automatically applied during backtesting for realistic performance calculations
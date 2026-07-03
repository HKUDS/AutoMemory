#!/usr/bin/env bash
set -euo pipefail

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                    🎯 QUICK START CONFIGURATION                          ║
# ║                   (Modify these 3 settings to run)                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# 📅 Backtest Date Range (YYYY-MM-DD format)
START_DATE="${START_DATE:-2025-03-01}"    # Start date
END_DATE="${END_DATE:-2025-06-30}"        # End date

# 🤖 LLM Model Selection (Available options below)
LLM_PROFILE="${LLM_PROFILE:-openai}"      # Current: openai (uses deepseek-v3.1-250821)
# you can add different LLM models in config.yaml
# Available LLM profiles:
#   - openai              : OpenAI GPT models (currently set to deepseek-v3.1)
#   - deepseek-v3.1       : DeepSeek V3.1 model
#   - kimi-k2-0711-preview: Kimi K2 model
#   - qwen3-235b-a22b-instruct-2507: Qwen3 235B model
#   - gpt-oss-20b         : GPT-OSS 20B model
#   - gpt-oss-120b        : GPT-OSS 120B model

# ═══════════════════════════════════════════════════════════════════════════
# 📝 Usage Examples:
#
#   1. Basic run (use defaults above):
#      bash scripts/run_benchmark.sh
#
#   2. Custom date range:
#      bash scripts/run_benchmark.sh --start-date 2025-04-01 --end-date 2025-05-31
#
#   3. Change LLM model:
#      bash scripts/run_benchmark.sh --llm-profile deepseek-v3.1
#
#   4. Full customization:
#      bash scripts/run_benchmark.sh \
#          --start-date 2025-04-01 \
#          --end-date 2025-05-31 \
#          --llm-profile kimi-k2-0711-preview
# ═══════════════════════════════════════════════════════════════════════════


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║              ⚙️  ADVANCED SETTINGS (Usually no need to change)           ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# Configuration file path
CONFIG_PATH="config.yaml"
APP_MOD="stockbench.apps.run_backtest"

# Strategy and execution settings
STRATEGY="${STRATEGY:-llm_decision}"
TIMESPAN="${TIMESPAN:-day}"
AGENT_MODE="${AGENT_MODE:-dual}"

# Output directories
OUTPUT_DIR="storage/reports/backtest"
LOG_DIR="storage/logs"

# ===== Helper Functions =====
log_info() {
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_success() {
    echo "[SUCCESS] $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

check_prerequisites() {
    log_info "Checking runtime environment..."
    
    # Check configuration file
    if [[ ! -f "${CONFIG_PATH}" ]]; then
        log_error "Configuration file not found: ${CONFIG_PATH}"
        exit 1
    fi
    
    # Check Python module
    if ! python -c "import importlib, sys; importlib.import_module('${APP_MOD}')" 2>/dev/null; then
        log_error "Python module not found: ${APP_MOD}"
        exit 1
    fi
    
    # Create output directories
    mkdir -p "${OUTPUT_DIR}"
    mkdir -p "${LOG_DIR}"
    
    log_success "Environment check passed"
}

run_backtest() {
    local RUN_ID="$1"
    local LLM_PROFILE="$2"
    local CACHE_OPTION="$3"
    local EXTRA_OPTS="$4"
    local LOG_FILE="${LOG_DIR}/${LLM_PROFILE}_${START_DATE}_${END_DATE}.log"
    local START_TIME=$(date +%s)
    
    log_info "Starting backtest: ${RUN_ID}"
    log_info "LLM Profile: ${LLM_PROFILE}"
    
    # Build backtest command
    local CMD="python -m ${APP_MOD} \
        --cfg \"${CONFIG_PATH}\" \
        --start \"${START_DATE}\" --end \"${END_DATE}\" \
        --strategy \"${STRATEGY}\" \
        --run-id \"${RUN_ID}\" \
        --llm-profile \"${LLM_PROFILE}\" \
        --agent-mode \"${AGENT_MODE}\" \
        ${CACHE_OPTION} \
        ${EXTRA_OPTS}"
    

    
    # Execute backtest
    if eval "${CMD}" > "${LOG_FILE}" 2>&1; then
        local END_TIME=$(date +%s)
        local DURATION=$((END_TIME - START_TIME))
        log_success "Backtest completed: ${RUN_ID} (Duration: ${DURATION}s)"
        log_info "Log file: ${LOG_FILE}"
    else
        local END_TIME=$(date +%s)
        local DURATION=$((END_TIME - START_TIME))
        log_error "Backtest failed: ${RUN_ID} (Duration: ${DURATION}s)"
        log_error "Error log: ${LOG_FILE}"
        return 1
    fi
}

show_summary() {
    log_info "=== Backtest Tasks Completed ==="
    log_info "Output directory: ${OUTPUT_DIR}"
    log_info "Log directory: ${LOG_DIR}"
    log_info "Strategy config: ${STRATEGY} | Timespan: ${TIMESPAN} | Agent mode: ${AGENT_MODE} | LLM: ${LLM_PROFILE}"
    log_info "Test configuration:"
    log_info "  • Date range: March-June 2025"
    log_info "  • Symbol universe: 20 stocks"
    log_info "  • Execution mode: Real-time LLM decisions"
}

# ===== Main Program =====
main() {
    log_info "=== Trading Agent Backtest System Started ==="
    
    # Environment check
    check_prerequisites
    
    # Display runtime configuration
    log_info "Runtime configuration:"
    log_info "  Strategy: ${STRATEGY}"
    log_info "  Timespan: ${TIMESPAN}"
    log_info "  Agent mode: ${AGENT_MODE}"
    log_info "  LLM profile: ${LLM_PROFILE}"
    log_info "  Default date range: ${START_DATE} to ${END_DATE}"
    
    # Run backtest tasks
    local FAILED_RUNS=0
    local PROFILE_UPPER=$(echo "${LLM_PROFILE}" | tr '[:lower:]' '[:upper:]' | tr '-' '_')
    
    # Define test configurations (use command line parameters)
    local TESTS=(
        "${PROFILE_UPPER}:${START_DATE}:${END_DATE}:"
        # Define test configurations
        # Empty symbols field (after last colon) will use symbols from config.yaml
        #"${PROFILE_UPPER}:2025-03-01:2025-06-01:GS,MSFT,HD,V,SHW,CAT,MCD,UNH,AXP,AMGN,TRV,CRM,JPM,IBM,HON,BA,AMZN,AAPL,PG,JNJ"
    )
    
    log_info "Starting execution of ${#TESTS[@]} backtest tasks"
    
    # Execute backtest tasks
    local CURRENT_TEST=0
    for test_config in "${TESTS[@]}"; do
        CURRENT_TEST=$((CURRENT_TEST + 1))
        
        # Parse test configuration
        IFS=':' read -ra CONFIG <<< "$test_config"
        local RUN_ID="${CONFIG[0]}"
        local TEST_START="${CONFIG[1]}"
        local TEST_END="${CONFIG[2]}"
        
        log_info "Executing task ${CURRENT_TEST}/${#TESTS[@]}: ${RUN_ID}"
        log_info "  Date range: ${TEST_START} to ${TEST_END}"

        
        # Temporarily modify date and symbol settings
        local ORIG_START_DATE="${START_DATE}"
        local ORIG_END_DATE="${END_DATE}"
        
        START_DATE="${TEST_START}"
        END_DATE="${TEST_END}"
        
        # Execute backtest
        if run_backtest "${RUN_ID}" "${LLM_PROFILE}" "" ""; then
            log_success "Task ${CURRENT_TEST}/${#TESTS[@]} completed: ${RUN_ID}"
        else
            log_error "Task ${CURRENT_TEST}/${#TESTS[@]} failed: ${RUN_ID}"
            FAILED_RUNS=$((FAILED_RUNS + 1))
        fi
        
        # Restore original configuration
        START_DATE="${ORIG_START_DATE}"
        END_DATE="${ORIG_END_DATE}"
        
        echo ""  # Task separator
    done
    
    # Display execution results
    if [[ ${FAILED_RUNS} -eq 0 ]]; then
        log_success "All ${#TESTS[@]} backtest tasks executed successfully"
    else
        log_error "${FAILED_RUNS}/${#TESTS[@]} backtest tasks failed"
    fi
    
    show_summary
}

# Process command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --start-date)
            START_DATE="$2"
            shift 2
            ;;
        --end-date)
            END_DATE="$2"
            shift 2
            ;;
        --strategy)
            STRATEGY="$2"
            shift 2
            ;;
        --timespan)
            TIMESPAN="$2"
            shift 2
            ;;
        --agent-mode)
            AGENT_MODE="$2"
            shift 2
            ;;
        --llm-profile)
            LLM_PROFILE="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --start-date DATE     Start date (default: ${START_DATE})"
            echo "  --end-date DATE       End date (default: ${END_DATE})"
            echo "  --strategy STRATEGY   Strategy name (default: ${STRATEGY})"
            echo "  --timespan TIMESPAN   Time granularity (default: ${TIMESPAN})"
            echo "  --agent-mode MODE     Agent mode (default: ${AGENT_MODE})"
            echo "  --llm-profile PROFILE LLM profile (default: ${LLM_PROFILE})"
            echo "  --help, -h            Show this help message"
            echo ""
            echo "Environment variables:"
            echo "  START_DATE, END_DATE, STRATEGY, TIMESPAN, AGENT_MODE, LLM_PROFILE"
            echo ""
            echo "LLM profile options:"
            echo "  openai        Use OpenAI API (default)"
            echo "  gpt-oss-20b   Use GPT-OSS-20B model"
            echo ""
            echo "Examples:"
            echo "  $0 --start-date 2024-01-01 --end-date 2024-01-31"
            echo "  $0 --llm-profile gpt-oss-20b"
            echo "  LLM_PROFILE=\"gpt-oss-20b\" $0"
            exit 0
            ;;
        *)
            log_error "Unknown parameter: $1"
            echo "Use --help to see help information"
            exit 1
            ;;
    esac
done

# Run main program
main "$@" 
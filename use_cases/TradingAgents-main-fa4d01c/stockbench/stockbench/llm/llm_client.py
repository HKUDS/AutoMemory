from __future__ import annotations

import os
import time
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timedelta

import httpx
from loguru import logger
import openai  # Add OpenAI official client

from stockbench.utils.io import ensure_dir, sha256_text, canonical_json, atomic_append_jsonl
from stockbench.utils.logging_helper import get_llm_logger

# Try to import JSON repair tools, use built-in methods if not installed
try:
    from json_repair import repair_json
    HAS_JSON_REPAIR = True
except ImportError:
    HAS_JSON_REPAIR = False
    
try:
    import demjson3 as demjson
    HAS_DEMJSON = True
except ImportError:
    HAS_DEMJSON = False


@dataclass
class LLMConfig:
    provider: str = "openai"  # Change default provider to openai, use OpenAI SDK uniformly
    base_url: str = "https://api.openai.com/v1/"
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: int = 256
    seed: Optional[int] = None
    timeout_sec: float = 60.0
    max_retries: int = 3
    backoff_factor: float = 0.5
    cache_enabled: bool = True
    cache_ttl_hours: int = 24
    # New: Granular read/write control (backward compatible: defaults to cache_enabled when not set)
    cache_read_enabled: Optional[bool] = None
    cache_write_enabled: Optional[bool] = None
    budget_prompt_tokens: int = 200_000
    budget_completion_tokens: int = 200_000
    # New: Whether authentication is required (vLLM can be auth-free by default)
    auth_required: Optional[bool] = None


class LLMClient:
    def __init__(self, api_key_env: str = "OPENAI_API_KEY", cache_dir: Optional[str] = None) -> None:
        self.api_key = os.getenv(api_key_env) or os.getenv("LLM_API_KEY", "")
        self.cache_dir = cache_dir or os.path.join(os.getcwd(), "storage", "cache", "llm")
        ensure_dir(self.cache_dir)
        self._client: Optional[httpx.Client] = None
        self._openai_client: Optional[openai.OpenAI] = None  # Add OpenAI client
        self._prompt_tokens_used = 0
        self._completion_tokens_used = 0
        self.llm_logger = get_llm_logger()  # Get LLM-specific logger

    def _get_openai_client(self, cfg: LLMConfig) -> openai.OpenAI:
        """Get OpenAI official client"""
        if self._openai_client is None:
            self._openai_client = openai.OpenAI(
                api_key=self.api_key,
                base_url=cfg.base_url,
                timeout=cfg.timeout_sec
            )
        return self._openai_client

    def _get_client(self, base_url: str, timeout_sec: float) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=base_url, timeout=timeout_sec)
        return self._client

    def _cache_path(self, key: str, run_id: Optional[str] = None, role: Optional[str] = None) -> str:
        # If no run_id provided, try to get from environment variable
        if not run_id:
            run_id = os.environ.get("TA_RUN_ID")
        
        # If still no run_id, use "default" to avoid cache fragmentation
        if not run_id:
            run_id = "default"
        
        # Always use new directory structure: group by run_id and add date subdirectories
        run_dir = os.path.join(self.cache_dir, "by_run", run_id)
        
        # Extract date info from key (format usually: 2025-06-02_xxxxx)
        # Only create date subdirectories for keys that explicitly start with a valid date pattern
        date_match = re.match(r'^(\d{4}-\d{2}-\d{2})_', key)
        if date_match:
            date_str = date_match.group(1)
            # Validate the date to ensure it's actually valid (basic validation)
            try:
                year, month, day = map(int, date_str.split('-'))
                if 1 <= month <= 12 and 1 <= day <= 31:  # Basic date validation
                    # Add date subdirectory
                    run_dir = os.path.join(run_dir, date_str)
            except (ValueError, IndexError):
                # Invalid date format, don't create subdirectory
                pass
        
        ensure_dir(run_dir)
        
        # Add role prefix for dual-agent architecture
        if role == "fundamental_filter":
            filename = f"analysis_{key}.json"
        elif role == "decision_agent":
            filename = f"decision_{key}.json"
        else:
            # Keep original format for single agent or other roles
            filename = f"{key}.json"
            
        return os.path.join(run_dir, filename)

    def _run_index_path(self, run_id: str) -> str:
        # New index structure: point directly to run_id folder
        base = os.path.join(self.cache_dir, "by_run", run_id)
        ensure_dir(base)
        return os.path.join(base, "_index.jsonl")

    def _append_run_index(self, run_id: Optional[str], record: Dict[str, Any]) -> None:
        # If no run_id provided, try to get from environment variable
        if not run_id:
            run_id = os.environ.get("TA_RUN_ID")
        
        # If still no run_id, use "default" to avoid losing index records
        if not run_id:
            run_id = "default"
        
        try:
            record2 = {**record, "run_id": run_id}
            atomic_append_jsonl(self._run_index_path(run_id), record2)
        except Exception:
            pass

    def _read_cache(self, key: str, ttl_hours: int, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        path = self._cache_path(key, run_id)
        if not os.path.exists(path):
            return None
        # Remove TTL control - cache is permanently valid
        # st = os.stat(path)
        # if (time.time() - st.st_mtime) > ttl_hours * 3600:
        #     # TTL expired: delete old cache files
        #     try:
        #         os.remove(path)
        #     except Exception:
        #         pass
        #     return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_cache(self, key: str, payload: Dict[str, Any], run_id: Optional[str] = None, role: Optional[str] = None) -> None:
        path = self._cache_path(key, run_id, role)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:
            pass

    def _make_cache_key(self, role: str, cfg: LLMConfig, system_prompt: str, user_prompt: str) -> str:
        ident = {
            "role": role,
            "provider": getattr(cfg, "provider", "openai-compatible"),
            "base_url": getattr(cfg, "base_url", "https://api.openai.com/v1"),
            "model": cfg.model,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,  # Add max_tokens to cache key
            "seed": cfg.seed,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }
        return sha256_text(canonical_json(ident))

    def _extract_json_with_improved_logic(self, content: str) -> dict:
        """
        Improved JSON extraction logic supporting more complex nested structures
        """
        # Method 1: Use stack matching to find complete JSON objects
        def find_complete_json(text: str) -> str:
            """Use stack matching to find complete JSON objects"""
            first_brace = text.find('{')
            if first_brace == -1:
                return None
                
            stack = []
            in_string = False
            escape_next = False
            
            for i in range(first_brace, len(text)):
                char = text[i]
                
                if escape_next:
                    escape_next = False
                    continue
                    
                if char == '\\':
                    escape_next = True
                    continue
                    
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                    
                if in_string:
                    continue
                    
                if char == '{':
                    stack.append(char)
                elif char == '}':
                    if stack:
                        stack.pop()
                        if not stack:  # Found complete JSON object
                            return text[first_brace:i+1]
            
            return None
        
        # Try different extraction strategies, including handling truncated responses
        strategies = [
            # Strategy 1: Extract JSON from <DECISION> tags
            lambda t: self._extract_from_decision_tag(t),
            # Strategy 2: JSON in markdown code blocks
            lambda t: self._extract_from_markdown_block(t),
            # Strategy 3: Complete JSON using stack matching
            lambda t: find_complete_json(t),
            # Strategy 4: Find largest JSON fragment
            lambda t: self._extract_largest_json_fragment(t),
            # Strategy 5: Handle truncated responses (new)
            lambda t: self._handle_truncated_response(t),
        ]
        
        for i, strategy in enumerate(strategies):
            try:
                json_str = strategy(content)
                if json_str:
                    # Try to parse the extracted JSON
                    json_str = json_str.strip()
                    # Fix common issues
                    json_str = self._fix_common_json_issues(json_str)
                    
                    parsed_json = json.loads(json_str)
                    self.llm_logger.debug(f"🎯 JSON extraction successful - Strategy {i+1}")
                    return parsed_json
            except (json.JSONDecodeError, AttributeError, TypeError) as e:
                self.llm_logger.debug(f"⚠️ JSON extraction failed - Strategy {i+1}: {str(e)[:50]}")
                continue
        
        return None
    
    def _extract_from_decision_tag(self, content: str) -> str:
        """Extract JSON from <DECISION> tags"""
        # Match content within <DECISION> tags
        pattern = r'<DECISION>\s*(.*?)\s*</DECISION>'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            decision_content = match.group(1).strip()
            
            # Clean content: remove possible explanatory text and markdown code blocks
            clean_content = decision_content
            
            # Remove markdown code block markers
            clean_content = re.sub(r'```json\s*', '', clean_content)
            clean_content = re.sub(r'```\s*$', '', clean_content)
            
            # Use stack matching to find complete JSON object, ensuring nested structures are handled
            json_start = clean_content.find('{')
            if json_start >= 0:
                stack = []
                in_string = False
                escape_next = False
                
                for i in range(json_start, len(clean_content)):
                    char = clean_content[i]
                    
                    if escape_next:
                        escape_next = False
                        continue
                        
                    if char == '\\':
                        escape_next = True
                        continue
                        
                    if char == '"' and not escape_next:
                        in_string = not in_string
                        continue
                        
                    if in_string:
                        continue
                        
                    if char == '{':
                        stack.append(char)
                    elif char == '}':
                        if stack:
                            stack.pop()
                            if not stack:  # Found complete JSON object
                                return clean_content[json_start:i+1]
        
        return None
    
    def _extract_from_markdown_block(self, content: str) -> str:
        """Extract JSON from markdown code blocks"""
        # Match ```json ... ``` pattern using greedy matching
        pattern = r'```json\s*(\{.*\})\s*```'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(1)
        
        # Match ```...``` pattern (without json identifier)
        pattern = r'```\s*(\{.*\})\s*```'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(1)
        
        return None
    
    def _extract_largest_json_fragment(self, content: str) -> str:
        """Extract the largest JSON fragment"""
        # Find all possible JSON object starting positions
        candidates = []
        for i, char in enumerate(content):
            if char == '{':
                # Try to match JSON starting from this position
                stack = []
                in_string = False
                escape_next = False
                
                for j in range(i, len(content)):
                    c = content[j]
                    
                    if escape_next:
                        escape_next = False
                        continue
                        
                    if c == '\\':
                        escape_next = True
                        continue
                        
                    if c == '"' and not escape_next:
                        in_string = not in_string
                        continue
                        
                    if in_string:
                        continue
                        
                    if c == '{':
                        stack.append(c)
                    elif c == '}':
                        if stack:
                            stack.pop()
                            if not stack:  # Found complete JSON object
                                candidates.append(content[i:j+1])
                                break
        
        # Return the longest candidate
        if candidates:
            return max(candidates, key=len)
        
        return None
    
    def _handle_truncated_response(self, content: str) -> str:
        """Handle truncated responses, try to extract valid JSON decisions from partial content"""
        import re
        import json
        
        try:
            # Find decisions start position
            decisions_start = content.find('"decisions"')
            if decisions_start == -1:
                return None
                
            # Extract all identifiable stock decisions
            stock_decisions = {}
            
            # Use regex to find all stock symbols and their basic information
            # Pattern 1: Match "SYMBOL": { "action": "..., ...
            basic_pattern = r'"([A-Z]{1,5})":\s*\{\s*"action":\s*"([^"]+)"'
            basic_matches = re.findall(basic_pattern, content)
            
            for symbol, action in basic_matches:
                decision = {"action": action}
                
                # Try to extract complete information for each stock
                symbol_section_start = content.find(f'"{symbol}":')
                if symbol_section_start == -1:
                    continue
                
                # Try to extract target_cash_amount
                target_pattern = rf'"{symbol}":[^}}]*?"target_cash_amount":\s*"([^"]+)"'
                target_match = re.search(target_pattern, content)
                if target_match:
                    decision["target_cash_amount"] = target_match.group(1)
                elif action == "hold":
                    decision["target_cash_amount"] = "0.0"
                else:
                    # For non-hold actions, skip this decision if target_cash_amount is not found
                    continue
                
                # Try to extract confidence
                conf_pattern = rf'"{symbol}":[^}}]*?"confidence":\s*"([^"]+)"'
                conf_match = re.search(conf_pattern, content)
                if conf_match:
                    decision["confidence"] = conf_match.group(1)
                else:
                    decision["confidence"] = "0.5"
                
                # Try to extract reasons (more lenient matching)
                reasons = []
                reasons_pattern = rf'"{symbol}":[^}}]*?"reasons":\s*\[(.*?)\]'
                reasons_match = re.search(reasons_pattern, content, re.DOTALL)
                if reasons_match:
                    reasons_content = reasons_match.group(1)
                    # Extract quoted reasons
                    reason_items = re.findall(r'"([^"]*)"', reasons_content)
                    if reason_items:
                        reasons = reason_items
                
                if not reasons:
                    if action == "hold":
                        reasons = ["Hold decision extracted from truncated response"]
                    else:
                        reasons = [f"{action} decision extracted from truncated response", "Based on partial available data"]
                
                decision["reasons"] = reasons
                stock_decisions[symbol] = decision
            
            # If decisions were successfully extracted, construct complete JSON
            if stock_decisions:
                result_json = {"decisions": stock_decisions}
                # Validate JSON format
                json.dumps(result_json)  # This will throw exception if format is incorrect
                self.llm_logger.info(f"✅ Successfully extracted {len(stock_decisions)} stock decisions from truncated response")
                return json.dumps(result_json)
                
        except Exception as e:
            self.llm_logger.debug(f"Truncated response processing failed: {e}")
            
        return None
    
    def _fix_common_json_issues(self, json_str: str) -> str:
        """Fix common JSON format issues, prioritize using professional repair tools"""
        original_json = json_str
        
        # Method 1: Try using json-repair library (recommended)
        if HAS_JSON_REPAIR:
            try:
                self.llm_logger.debug("Trying to fix JSON using json-repair library...")
                fixed_json = repair_json(json_str)
                # Validate repair result
                json.loads(fixed_json)
                self.llm_logger.debug("json-repair fix successful")
                return fixed_json
            except Exception as e:
                self.llm_logger.debug(f"json-repair fix failed: {e}")
        
        # Method 2: Try using demjson library (more lenient parsing)
        if HAS_DEMJSON:
            try:
                self.llm_logger.debug("Trying to fix JSON using demjson library...")
                # demjson can directly parse and then re-serialize
                parsed = demjson.decode(json_str, strict=False)
                fixed_json = json.dumps(parsed, ensure_ascii=False)
                self.llm_logger.debug("demjson fix successful")
                return fixed_json
            except Exception as e:
                self.llm_logger.debug(f"demjson fix failed: {e}")
        
        # Method 3: Use built-in repair methods (fallback option)
        self.llm_logger.debug("Using built-in methods to fix JSON...")
        
        # Remove trailing commas
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        
        # Fix single quote issues
        json_str = re.sub(r"'([^']*)':", r'"\1":', json_str)
        json_str = re.sub(r":\s*'([^']*)'", r': "\1"', json_str)
        
        # Fix Python boolean values
        json_str = re.sub(r'\bTrue\b', 'true', json_str)
        json_str = re.sub(r'\bFalse\b', 'false', json_str)
        json_str = re.sub(r'\bNone\b', 'null', json_str)
        
        # Fix invalid escape sequences
        json_str = re.sub(r'\\(\$)', r'\1', json_str)  # \$ → $
        json_str = re.sub(r'\\(%)', r'\1', json_str)  # \% → %
        json_str = re.sub(r'\\(#)', r'\1', json_str)  # \# → #
        json_str = re.sub(r'\\(&)', r'\1', json_str)  # \& → &
        
        # Fix nested quote issues in JSON strings
        def fix_nested_quotes_in_strings(text):
            """Fix nested quotes in strings"""
            # Directly handle specific known problem patterns
            problematic_phrases = [
                '"cash-heavy stock in doghouse"',
                '"pre-market"',
                '"high-risk"',
                '"post-earnings"',
                '"blue-chip"',
                '"high-yield"',
                '"market-cap"'
            ]
            
            for phrase in problematic_phrases:
                if phrase in text:
                    escaped_phrase = phrase.replace('"', '\\"')
                    text = text.replace(phrase, escaped_phrase)
            
            return text
        
        json_str = fix_nested_quotes_in_strings(json_str)
        
        return json_str

    def _ensure_decisions_extracted(self, parsed_response: Dict[str, Any], raw_response: Dict[str, Any], max_attempts: int = 3) -> Dict[str, Any]:
        """Ensure decisions are extracted from the result, recursively parse if raw_content until success or max attempts reached"""
        
        # If decisions already exist, return directly
        if isinstance(parsed_response, dict) and "decisions" in parsed_response:
            self.llm_logger.debug("Result already contains decisions, returning as-is")
            return parsed_response
        
        # If not in raw_content format, return directly
        if not (isinstance(parsed_response, dict) and "raw_content" in parsed_response):
            self.llm_logger.debug("Result is not raw_content format, returning as-is")
            return parsed_response
        
        self.llm_logger.info(f"Starting recursive parsing for raw_content, max attempts: {max_attempts}")
        
        # Try multiple parsing attempts
        for attempt in range(max_attempts):
            self.llm_logger.debug(f"Parsing attempt {attempt + 1}/{max_attempts}")
            
            try:
                # Get original content from raw_response
                content = ""
                if isinstance(raw_response, dict) and "choices" in raw_response:
                    content = raw_response.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                # If no content, try to get from raw_content
                if not content and "raw_content" in parsed_response:
                    content = parsed_response["raw_content"]
                
                if not content:
                    self.llm_logger.warning(f"No content found for parsing attempt {attempt + 1}")
                    break
                
                # Use improved JSON extraction logic
                extracted = self._extract_json_with_improved_logic(content)
                
                if extracted and isinstance(extracted, dict):
                    if "decisions" in extracted:
                        self.llm_logger.info(f"Successfully extracted decisions on attempt {attempt + 1}")
                        return extracted
                    else:
                        self.llm_logger.debug(f"Attempt {attempt + 1}: Extracted JSON but no decisions found")
                        # Try to find nested decisions in extracted result
                        decisions = self._find_nested_decisions(extracted)
                        if decisions:
                            self.llm_logger.info(f"Found nested decisions on attempt {attempt + 1}")
                            return {"decisions": decisions}
                else:
                    self.llm_logger.debug(f"Attempt {attempt + 1}: Failed to extract valid JSON")
                
            except Exception as e:
                self.llm_logger.warning(f"Parsing attempt {attempt + 1} failed: {e}")
                continue
        
        # All attempts failed, return original result
        self.llm_logger.warning(f"Failed to extract decisions after {max_attempts} attempts, returning raw_content")
        return parsed_response
    
    def _find_nested_decisions(self, data: Dict[str, Any]) -> Optional[Any]:
        """Find decisions field in nested dictionary"""
        if not isinstance(data, dict):
            return None
        
        # Directly find decisions
        if "decisions" in data:
            return data["decisions"]
        
        # Recursively find nested decisions
        for key, value in data.items():
            if isinstance(value, dict):
                result = self._find_nested_decisions(value)
                if result is not None:
                    return result
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        result = self._find_nested_decisions(item)
                        if result is not None:
                            return result
        
        return None

    def _make_cache_key_with_date(self, role: str, cfg: LLMConfig, system_prompt: str, user_prompt: str, trade_date: str = None, retry_attempt: int = 0) -> str:
        """Generate cache key including date and retry attempt"""
        base_params = {
            "role": role,
            "provider": getattr(cfg, "provider", "openai-compatible"),
            "model": cfg.model,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,  # Add max_tokens to date-included cache key
            "seed": cfg.seed,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }
        
        if trade_date:
            # Use date as filename prefix
            base_key = sha256_text(canonical_json(base_params))
            
            # Add retry attempt suffix if > 0
            if retry_attempt > 0:
                return f"{trade_date}_{base_key[:16]}_retry{retry_attempt}"
            else:
                return f"{trade_date}_{base_key[:16]}"
        else:
            # For non-dated keys, still add retry suffix if needed
            base_key = sha256_text(canonical_json(base_params))
            if retry_attempt > 0:
                return f"{base_key}_retry{retry_attempt}"
            else:
                return base_key

    def _cache_payload(self, cache_key: str, payload: Dict[str, Any], role: str, cfg: LLMConfig, system_prompt: str, user_prompt: str, run_id: Optional[str] = None, raw_response: Optional[Dict] = None, retry_attempt: int = 0) -> None:
        """Cache LLM response - Enhanced version, save complete input/output data"""
        if not cfg.cache_enabled or not self.cache_dir:
            return
        
        try:
            # Build complete cache data structure
            enhanced_cache_data = {
                "metadata": {
                    "ts_utc": datetime.utcnow().isoformat(),
                    "role": role,
                    "model": cfg.model,
                    "provider": getattr(cfg, "provider", "openai-compatible"),
                    "base_url": getattr(cfg, "base_url", "N/A"),
                    "temperature": cfg.temperature,
                    "max_tokens": cfg.max_tokens,
                    "seed": cfg.seed,
                    "cache_key": cache_key,
                    "run_id": run_id,
                    "retry_attempt": retry_attempt,  # Add retry attempt info
                    "is_retry": retry_attempt > 0    # Flag to identify retry attempts
                },
                "input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "system_prompt_length": len(system_prompt),
                    "user_prompt_length": len(user_prompt)
                },
                "output": {
                    "parsed_response": payload,  # Parsed JSON response
                    "raw_response": raw_response,  # Raw API response
                    "parsed_response_length": len(str(payload)),
                    "raw_response_length": len(str(raw_response)) if raw_response else 0
                }
            }
            
            # Write enhanced cache file
            self._write_cache(cache_key, enhanced_cache_data, run_id, role)
            
            # Record to index file
            record = {
                "ts_utc": datetime.utcnow().isoformat(),
                "role": role,
                "model": cfg.model,
                "provider": getattr(cfg, "provider", "openai-compatible"),
                "cache_key": cache_key,
                "system_prompt_length": len(system_prompt),
                "user_prompt_length": len(user_prompt),
                "response_length": len(str(payload)),
                "cached": True,
                "retry_attempt": retry_attempt,  # Add retry info to index
                "is_retry": retry_attempt > 0
            }
            self._append_run_index(run_id, record)
        except Exception:
            pass

    @staticmethod
    def _extract_json(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            # Try to parse the entire content directly
            return json.loads(text), None
        except Exception:
            pass
        try:
            # Fallback: extract from first { to last }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                sub = text[start : end + 1]
                return json.loads(sub), None
        except Exception as e:
            return None, str(e)
        return None, "no_json_found"

    def remaining_budget_ok(self, cfg: LLMConfig) -> bool:
        return (
            self._prompt_tokens_used < cfg.budget_prompt_tokens
            and self._completion_tokens_used < cfg.budget_completion_tokens
        )

    def get_cached_payload(self, cache_key: str, run_id: Optional[str] = None, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Publicly read cached content (no TTL validation) - compatible with old and new cache formats"""
        try:
            path = self._cache_path(cache_key, run_id, role)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                
                # Check if it's the new enhanced cache format
                if isinstance(cached_data, dict) and "metadata" in cached_data and "input" in cached_data and "output" in cached_data:
                    # New format: prioritize parsed_response to avoid unnecessary re-parsing
                    parsed_response = cached_data["output"].get("parsed_response", {})
                    
                    # If parsed_response already contains valid decisions, use directly
                    if isinstance(parsed_response, dict) and "decisions" in parsed_response:
                        self.llm_logger.debug("Using valid parsed_response from cache")
                        return parsed_response
                    
                    # If parsed_response is raw_content, try to re-parse from raw_response
                    if isinstance(parsed_response, dict) and "raw_content" in parsed_response:
                        self.llm_logger.debug("Found raw_content in cache, attempting to reparse from raw_response")
                        raw_response = cached_data["output"].get("raw_response", {})
                        if isinstance(raw_response, dict) and "choices" in raw_response:
                            try:
                                # Extract LLM's original text response from raw_response
                                content = raw_response.get("choices", [{}])[0].get("message", {}).get("content", "")
                                if content:
                                    # Use improved JSON extraction logic to re-parse
                                    reparsed = self._extract_json_with_improved_logic(content)
                                    if reparsed and "decisions" in reparsed:
                                        self.llm_logger.debug("Successfully reparsed decisions from raw_response")
                                        return reparsed
                            except Exception as e:
                                self.llm_logger.warning(f"Failed to reparse from raw_response: {e}")
                    
                    # Before returning, if result is still raw_content, try recursive parsing
                    raw_response = cached_data["output"].get("raw_response", {})
                    final_result = self._ensure_decisions_extracted(parsed_response, raw_response)
                    return final_result
                else:
                    # Old format: return directly
                    return cached_data
        except Exception as e:
            self.llm_logger.error(f"Error reading cache: {e}")
            return None

    def get_full_cached_data(self, cache_key: str, run_id: Optional[str] = None, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get complete cached data (including input, output, metadata)"""
        try:
            path = self._cache_path(cache_key, run_id, role)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def generate_json(self, role: str, cfg: LLMConfig, system_prompt: str, user_prompt: str, trade_date: str = None, run_id: Optional[str] = None, retry_attempt: int = 0) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Generate JSON format response"""
        meta = {"role": role, "cached": False, "latency_ms": 0, "usage": {}}
        
        # Record LLM call start
        self.llm_logger.info(f"🤖 {role} - Model: {cfg.model}")
        
        # Check cache
        cache_read = cfg.cache_read_enabled if cfg.cache_read_enabled is not None else cfg.cache_enabled
        if cache_read and self.cache_dir:
            # Prioritize using date-included cache key
            cache_key = self._make_cache_key_with_date(role, cfg, system_prompt, user_prompt, trade_date, retry_attempt)
            cached = self.get_cached_payload(cache_key, run_id, role)
            if cached:
                meta["cached"] = True
                self.llm_logger.info(f"💾 Cache hit - {role}")
                return cached, meta
            else:
                self.llm_logger.debug(f"🔍 Cache miss - {role}")
        
        provider = (cfg.provider or "openai-compatible").lower()
        need_auth = cfg.auth_required if cfg.auth_required is not None else (provider not in {"vllm", "openai-compatible-no-auth", "llama.cpp", "none"})
        
        # Record authentication info
        self.llm_logger.debug(f"🔐 Authentication check - Provider: {provider}, Needs auth: {need_auth}")
        
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if need_auth:
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
                self.llm_logger.debug(f"🔑 Add authentication header")
            else:
                self.llm_logger.error(f"❌ Authentication required but no API key provided - {provider}")
                return None, {**meta, "reason": "no_api_key"}

        body: Dict[str, Any] = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
        }
        if cfg.seed is not None:
            body["seed"] = cfg.seed
        
        backoff = cfg.backoff_factor
        start_ts = time.time()
        
        self.llm_logger.debug(f"📤 Request preparation - {provider}:{cfg.model}")
        
        # Choose call method based on provider
        for attempt in range(cfg.max_retries + 1):
            try:
                self.llm_logger.debug(f"🔄 Request sent - Attempt {attempt + 1}/{cfg.max_retries + 1}")
                
                # Prioritize using OpenAI official client (unless other provider explicitly specified)
                if provider in ["openai", "openai-official"] or provider not in ["vllm", "llama.cpp", "none", "openai-compatible", "openai-compatible-no-auth"]:
                    result = self._call_openai_official(cfg, system_prompt, user_prompt)
                    
                    if result["status_code"] == 200:
                        end_ts = time.time()
                        meta["latency_ms"] = int((end_ts - start_ts) * 1000)
                        
                        response_data = result["data"]
                        content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        
                        # Update token usage statistics
                        usage = response_data.get("usage", {})
                        self._prompt_tokens_used += usage.get("prompt_tokens", 0)
                        self._completion_tokens_used += usage.get("completion_tokens", 0)
                        meta["usage"] = usage
                        
                        self.llm_logger.info(f"✅ {role} success - Latency: {meta['latency_ms']}ms, Token usage: {usage}")
                        
                        # Parse JSON content
                        if not content or not content.strip():
                            self.llm_logger.warning("⚠️ LLM returned empty content")
                            return {"raw_content": ""}, meta
                        
                        content = content.strip()
                        
                        # Check again if content is empty (prevent JSON parsing failure)
                        if not content:
                            self.llm_logger.warning("⚠️ Content is empty after preprocessing, cannot parse JSON")
                            return {"raw_content": ""}, meta
                        
                        # First try to parse JSON directly (compatible with pure JSON responses)
                        try:
                            parsed = json.loads(content)
                            self.llm_logger.debug(f"📊 Direct JSON parsing successful")
                            cache_write = cfg.cache_write_enabled if cfg.cache_write_enabled is not None else cfg.cache_enabled
                            if cache_write and self.cache_dir:
                                cache_key = self._make_cache_key_with_date(role, cfg, system_prompt, user_prompt, trade_date, retry_attempt)
                                self._cache_payload(cache_key, parsed, role, cfg, system_prompt, user_prompt, run_id, response_data, retry_attempt)
                            return parsed, meta
                        except json.JSONDecodeError:
                            # Direct parsing failed, try to extract JSON from <DECISION> tags
                            self.llm_logger.debug(f"📄 Try to extract <DECISION> tag content")
                            self.llm_logger.debug(f"📏 Content length: {len(content)}")
                            
                            # Try to extract JSON
                            extracted_json = self._extract_json_with_improved_logic(content)
                            if extracted_json:
                                self.llm_logger.debug(f"✅ JSON extraction successful")
                                cache_write = cfg.cache_write_enabled if cfg.cache_write_enabled is not None else cfg.cache_enabled
                                if cache_write and self.cache_dir:
                                    cache_key = self._make_cache_key_with_date(role, cfg, system_prompt, user_prompt, trade_date, retry_attempt)
                                    self._cache_payload(cache_key, extracted_json, role, cfg, system_prompt, user_prompt, run_id, response_data, retry_attempt)
                                return extracted_json, meta
                            
                            # Return original content
                            self.llm_logger.warning(f"⚠️ JSON extraction failed, returning original content")
                            cache_write = cfg.cache_write_enabled if cfg.cache_write_enabled is not None else cfg.cache_enabled
                            if cache_write and self.cache_dir:
                                cache_key = self._make_cache_key_with_date(role, cfg, system_prompt, user_prompt, trade_date, retry_attempt)
                                self._cache_payload(cache_key, {"raw_content": content}, role, cfg, system_prompt, user_prompt, run_id, response_data, retry_attempt)
                            return {"raw_content": content}, meta
                    
                    elif result["status_code"] == 401:
                        self.llm_logger.error(f"❌ Authentication error: {result.get('error', 'Unknown')}")
                        return None, {**meta, "reason": f"auth_error: {result.get('error', 'Unknown')}"}
                    
                    elif result["status_code"] == 429:
                        # Rate limit, retry
                        wait_time = (cfg.backoff_factor ** attempt)
                        self.llm_logger.warning(f"⏱️ Rate limit - Wait {wait_time}s (Attempt {attempt + 1})")
                        if attempt < cfg.max_retries:
                            time.sleep(wait_time)
                            continue
                        else:
                            return None, {**meta, "reason": "rate_limit_exceeded"}
                    
                    else:
                        error_msg = result.get("error", f"HTTP {result['status_code']}")
                        wait_time = (cfg.backoff_factor ** attempt)
                        self.llm_logger.error(f"❌ API error {result['status_code']} - Attempt {attempt + 1}: {error_msg[:100]}")
                        
                        if attempt < cfg.max_retries:
                            time.sleep(wait_time)
                            continue
                        else:
                            return None, {**meta, "reason": error_msg}
                
                # Other providers use the original method
                else:
                    if provider == "openai-compatible" or provider == "openai-compatible-no-auth":
                        response = self._call_openai_compatible(cfg, headers, body)
                    elif provider == "vllm":
                        response = self._call_vllm(cfg, headers, body)
                    else:
                        self.llm_logger.error(f"❌ Unknown LLM provider: {provider}")
                        return None, {**meta, "reason": f"unknown_provider_{provider}"}
                
                if response and response.status_code == 200:
                    end_ts = time.time()
                    meta["latency_ms"] = int((end_ts - start_ts) * 1000)
                    
                    self.llm_logger.info(f"✅ {role} success - Latency: {meta['latency_ms']}ms, Cache hit: False")
                    
                    try:
                        response_data = response.json()
                        content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        
                        self.llm_logger.debug(f"📝 Response parsing - Content length: {len(content)}")
                        
                        # Debug print: LLM response (automatically filtered by log level)
                        try:
                            self.llm_logger.debug("=== LLM Response ===")
                            self.llm_logger.debug(f"Status code: {response.status_code}")
                            self.llm_logger.debug(f"Latency: {meta['latency_ms']}ms")
                            self.llm_logger.debug(f"Raw response: {response_data}")
                            self.llm_logger.debug(f"Extracted content: {content}")
                            self.llm_logger.debug("=== LLM Response End ===")
                        except Exception:
                            pass
                        
                        # Preprocess content
                        if not content or not content.strip():
                            self.llm_logger.warning("⚠️ LLM returned empty content")
                            return {"raw_content": ""}, meta
                        
                        content = content.strip()
                        
                        # Check again if content is empty (prevent JSON parsing failure)
                        if not content:
                            self.llm_logger.warning("⚠️ Content is empty after preprocessing, cannot parse JSON")
                            return {"raw_content": ""}, meta
                        
                        # Try to parse JSON
                        try:
                            parsed = json.loads(content)
                            self.llm_logger.debug(f"📊 JSON parsing successful")
                            cache_write = cfg.cache_write_enabled if cfg.cache_write_enabled is not None else cfg.cache_enabled
                            if cache_write and self.cache_dir:
                                cache_key = self._make_cache_key_with_date(role, cfg, system_prompt, user_prompt, trade_date, retry_attempt)
                                self._cache_payload(cache_key, parsed, role, cfg, system_prompt, user_prompt, run_id, response_data, retry_attempt)
                            return parsed, meta
                        except json.JSONDecodeError as e:
                            self.llm_logger.warning(f"⚠️ JSON parsing failed, trying to extract: {str(e)[:50]}")
                            self.llm_logger.debug(f"📄 Original content (first 200 chars): {content[:200]!r}")
                            self.llm_logger.debug(f"📏 Content length: {len(content)}")
                            
                            # Try multiple JSON extraction methods
                            extracted_json = None
                            
                            # Method 1: First try to extract content from <DECISION> tags
                            decision_match = re.search(r'<DECISION>\s*(.*?)\s*</DECISION>', content, re.DOTALL)
                            if decision_match:
                                decision_content = decision_match.group(1).strip()
                                
                                # Clean content: remove possible explanatory text and markdown code blocks
                                clean_content = decision_content
                                
                                # Remove markdown code block markers
                                clean_content = re.sub(r'```json\s*', '', clean_content)
                                clean_content = re.sub(r'```\s*$', '', clean_content)
                                
                                # Remove possible explanatory text (any text before JSON)
                                json_start = clean_content.find('{')
                                json_end = clean_content.rfind('}')
                                if json_start != -1 and json_end != -1 and json_end > json_start:
                                    json_content = clean_content[json_start:json_end+1].strip()
                                    
                                    # Additional cleanup: remove possible comments or explanatory text
                                    # Ensure JSON integrity
                                    try:
                                        # Try direct parsing first
                                        extracted_json = json.loads(json_content)
                                        self.llm_logger.debug(f"🎯 DECISION tag JSON parsing successful")
                                    except json.JSONDecodeError as e:
                                        self.llm_logger.debug(f"⚠️ DECISION tag JSON parsing failed: {str(e)[:50]}")
                                        
                                        # Try to fix common JSON format issues
                                        try:
                                            # Fix possible trailing comma issues
                                            fixed_content = re.sub(r',(\s*[}\]])', r'\1', json_content)
                                            # Fix possible single quote issues
                                            fixed_content = re.sub(r"'([^']*)':", r'"\1":', fixed_content)
                                            fixed_content = re.sub(r":\s*'([^']*)'", r': "\1"', fixed_content)
                                            
                                            extracted_json = json.loads(fixed_content)
                                            self.llm_logger.debug(f"🎯 DECISION tag JSON parsing successful after fixing")
                                        except json.JSONDecodeError:
                                            self.llm_logger.debug("⚠️ JSON fix attempt also failed")
                            
                            # Method 2: If DECISION tag fails, try other patterns
                            if not extracted_json:
                                extracted_json = self._extract_json_with_improved_logic(content)
                            
                            # If extraction succeeds, return result
                            if extracted_json:
                                cache_write = cfg.cache_write_enabled if cfg.cache_write_enabled is not None else cfg.cache_enabled
                                if cache_write and self.cache_dir:
                                    cache_key = self._make_cache_key_with_date(role, cfg, system_prompt, user_prompt, trade_date, retry_attempt)
                                    self._cache_payload(cache_key, extracted_json, role, cfg, system_prompt, user_prompt, run_id, response_data, retry_attempt)
                                return extracted_json, meta
                            
                            # If still fails, return original content
                            self.llm_logger.warning(f"⚠️ All JSON extraction methods failed, returning original content (length: {len(content)})")
                            cache_write = cfg.cache_write_enabled if cfg.cache_write_enabled is not None else cfg.cache_enabled
                            if cache_write and self.cache_dir:
                                cache_key = self._make_cache_key_with_date(role, cfg, system_prompt, user_prompt, trade_date, retry_attempt)
                                self._cache_payload(cache_key, {"raw_content": content}, role, cfg, system_prompt, user_prompt, run_id, response_data, retry_attempt)
                            return {"raw_content": content}, meta
                    
                    except Exception as e:
                        return None, {**meta, "reason": f"parse_error_{str(e)}"}
                
                elif response and response.status_code == 429:
                    # Rate limit
                    wait_time = backoff ** attempt
                    self.llm_logger.warning(f"⏱️ Rate limit - Wait {wait_time}s (Attempt {attempt + 1})")
                    if attempt < cfg.max_retries:
                        time.sleep(wait_time)
                        continue
                    else:
                        self.llm_logger.error(f"❌ Rate limit exceeded maximum retry attempts")
                        return None, {**meta, "reason": "rate_limit_exceeded"}
                
                else:
                    error_msg = f"HTTP {response.status_code if response else 'No response'}"
                    if response and response.text:
                        try:
                            error_data = response.json()
                            error_msg = error_data.get("error", {}).get("message", error_msg)
                        except:
                            error_msg = f"{error_msg}: {response.text[:200]}"
                    
                    wait_time = backoff ** attempt
                    self.llm_logger.error(f"❌ HTTP error {response.status_code if response else 'None'} - Attempt {attempt + 1}: {error_msg[:100]}")
                    
                    if attempt < cfg.max_retries:
                        time.sleep(wait_time)
                        continue
                    else:
                        return None, {**meta, "reason": error_msg}
                        
            except Exception as e:
                wait_time = backoff ** attempt
                self.llm_logger.error(f"❌ Request exception {type(e).__name__} - Attempt {attempt + 1}: {str(e)[:100]}")
                if attempt < cfg.max_retries:
                    time.sleep(wait_time)
                    continue
                else:
                    return None, {**meta, "reason": f"exception_{str(e)}"}
        
        self.llm_logger.error(f"❌ {role} failed - Reason: max_retries_exceeded")
        return None, {**meta, "reason": "max_retries_exceeded"} 

    def _call_openai_official(self, cfg: LLMConfig, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Call API using OpenAI official client"""
        self.llm_logger.debug(f"🔗 Call OpenAI official API - {cfg.model}")
        
        try:
            client = self._get_openai_client(cfg)
            response = client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                timeout=cfg.timeout_sec
            )

            # Convert to standard format
            response_data = {
                "choices": [
                    {
                        "message": {
                            "content": response.choices[0].message.content
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }
            }
            
            self.llm_logger.debug(f"📥 OpenAI API response successful")
            return {"status_code": 200, "data": response_data}
            
        except openai.AuthenticationError as e:
            self.llm_logger.error(f"❌ OpenAI authentication error: {str(e)}")
            return {"status_code": 401, "error": str(e)}
        except openai.RateLimitError as e:
            self.llm_logger.error(f"❌ OpenAI rate limit: {str(e)}")
            return {"status_code": 429, "error": str(e)}
        except openai.APIError as e:
            self.llm_logger.error(f"❌ OpenAI API error: {str(e)}")
            return {"status_code": 500, "error": str(e)}
        except Exception as e:
            self.llm_logger.error(f"❌ OpenAI call exception: {str(e)}")
            return {"status_code": 500, "error": str(e)}

    def _call_openai_compatible(self, cfg: LLMConfig, headers: Dict[str, str], body: Dict[str, Any]) -> httpx.Response:
        """Call OpenAI-compatible API"""
        self.llm_logger.debug(f"🔗 Call OpenAI-compatible API - {body.get('model')}")
        client = self._get_client(cfg.base_url, cfg.timeout_sec)
        response = client.post("/chat/completions", headers=headers, json=body)
        self.llm_logger.debug(f"📥 API response - Status code: {response.status_code}")
        return response

    def _call_vllm(self, cfg: LLMConfig, headers: Dict[str, str], body: Dict[str, Any]) -> httpx.Response:
        """Call vLLM API (using OpenAI-compatible format)
        
        vLLM provides services through OpenAI-compatible API, but with some special handling:
        1. Support auth-free mode (auth_required=false)
        2. Model name mapping (served-model-name)
        3. vLLM-specific error handling
        """
        self.llm_logger.debug(f"🚀 Call vLLM API - Model: {body.get('model')}, URL: {cfg.base_url}")
        
        try:
            # vLLM may not need authentication, check configuration
            if cfg.auth_required is False:
                # Remove Authorization header, vLLM local deployment usually doesn't need it
                headers_copy = headers.copy()
                headers_copy.pop('Authorization', None)
                self.llm_logger.debug("🔓 vLLM auth-free mode, remove Authorization header")
            else:
                headers_copy = headers
            
            # Call OpenAI-compatible interface
            client = self._get_client(cfg.base_url, cfg.timeout_sec)
            response = client.post("/chat/completions", headers=headers_copy, json=body)
            
            # vLLM-specific status check
            if response.status_code == 404:
                self.llm_logger.error(f"❌ vLLM model not found: {body.get('model')} - Please check served-model-name configuration")
            elif response.status_code == 422:
                self.llm_logger.error(f"❌ vLLM parameter error - Please check temperature/max_tokens and other parameters")
            elif response.status_code == 200:
                self.llm_logger.debug(f"✅ vLLM call successful - Status code: {response.status_code}")
            else:
                self.llm_logger.warning(f"⚠️ vLLM response abnormal - Status code: {response.status_code}")
            
            return response
            
        except httpx.ConnectError as e:
            self.llm_logger.error(f"❌ vLLM connection failed: {cfg.base_url} - {str(e)}")
            self.llm_logger.error("💡 Please ensure vLLM service is started: python -m vllm.entrypoints.openai.api_server --model <model_name> --port 8000")
            raise
        except httpx.TimeoutException as e:
            self.llm_logger.error(f"❌ vLLM call timeout ({cfg.timeout_sec} seconds) - {str(e)}")
            raise
        except Exception as e:
            self.llm_logger.error(f"❌ vLLM call exception: {str(e)}")
            raise
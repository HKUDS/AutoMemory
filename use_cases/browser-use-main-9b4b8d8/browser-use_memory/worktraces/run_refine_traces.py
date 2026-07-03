#!/usr/bin/env python3
"""Reusable refine runner for Browser-Use memory traces."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from browser_use.memory import BrowserUseMemory


def unwrap_state(raw: dict[str, Any]) -> dict[str, Any]:
	inner = raw.get('refine_state')
	return inner if isinstance(inner, dict) and 'current_phase' in inner else raw


def load_llm():
	"""Use the same real provider/model configured for Browser-Use memory extraction."""
	load_dotenv(REPO_ROOT / '.env')
	api_key = os.getenv('OPENROUTER_API_KEY')
	if not api_key:
		raise RuntimeError('OPENROUTER_API_KEY is required for API-honest session consolidation traces')
	from browser_use.llm import ChatOpenRouter

	return ChatOpenRouter(model=os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini'), api_key=api_key)


def trace_to_history(turns: list[dict[str, Any]], *, force_success: bool = True) -> dict[str, Any]:
	history: list[dict[str, Any]] = []
	for idx, turn in enumerate(turns):
		metadata = turn.get('metadata') or {}
		if turn.get('role') == 'system':
			continue
		action = metadata.get('action')
		if action is None:
			action = [{'noop': {'note': turn.get('content', '')[:80]}}]
		elif isinstance(action, dict):
			action = [action]
		result = metadata.get('result')
		if result is None:
			result = [{'extracted_content': turn.get('content', '')}]
		elif isinstance(result, dict):
			result = [result]
		state = {
			'url': metadata.get('url') or '',
			'title': metadata.get('title') or '',
			'tabs': metadata.get('tabs') or [],
			'interacted_element': metadata.get('interacted_element') or [None for _ in action],
		}
		history.append(
			{
				'model_output': {
					'evaluation_previous_goal': metadata.get('evaluation') or turn.get('content', '')[:300],
					'memory': metadata.get('memory') or turn.get('content', '')[:300],
					'next_goal': metadata.get('next_goal') or '',
					'action': action,
				},
				'result': result,
				'state': state,
			}
		)
	if force_success and history:
		last_result = history[-1].setdefault('result', [])
		if not any(item.get('is_done') for item in last_result):
			last_result.append(
				{
					'is_done': True,
					'success': True,
					'extracted_content': last_result[-1].get('extracted_content', 'completed') if last_result else 'completed',
				}
			)
	return {'history': history}


def text_blob(value: Any) -> str:
	if isinstance(value, str):
		return value
	return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def match_value(output: str, expected: dict[str, Any]) -> bool:
	value = str(expected.get('value', ''))
	matcher = expected.get('matcher', 'substring')
	if matcher == 'not_substring':
		return value.lower() not in output.lower()
	if matcher == 'not_regex':
		return re.search(value, output, flags=re.IGNORECASE | re.DOTALL) is None
	if matcher == 'regex':
		return re.search(value, output, flags=re.IGNORECASE | re.DOTALL) is not None
	if matcher == 'exact':
		return output.strip() == value.strip()
	return value.lower() in output.lower()


def qa_score(query_outputs: list[tuple[dict[str, Any], str]]) -> dict[str, float]:
	item_hits = 0
	item_total = 0
	query_hits = 0
	for query, output in query_outputs:
		expected = query.get('expected_information') or []
		hits = sum(1 for item in expected if match_value(output, item))
		item_hits += hits
		item_total += len(expected)
		if expected and hits == len(expected):
			query_hits += 1
	return {
		'support_coverage': item_hits / item_total if item_total else 0.0,
		'query_success': query_hits / len(query_outputs) if query_outputs else 0.0,
	}


async def run_qa_trace(trace: dict[str, Any], memory_path: str, data_dir: Path, llm: Any | None) -> dict[str, Any]:
	memory = BrowserUseMemory(data_dir)
	task = trace.get('task') or trace.get('description') or trace['trace_id']
	turns = trace.get('turns') or []

	if memory_path == 'task_progress':
		for turn in turns:
			metadata = turn.get('metadata') or {}
			step_task = metadata.get('task') or task
			step_task_id = metadata.get('task_id') or trace['trace_id']
			await memory.record_step(step_task, trace_to_history([turn], force_success=False)['history'][0], task_id=step_task_id)
	else:
		window: list[dict[str, Any]] = []
		for turn in turns:
			window.append(turn)
			if (turn.get('metadata') or {}).get('retention_flush') is True:
				await memory.flush_window(task, trace_to_history(window, force_success=False), llm=llm, task_id=trace['trace_id'])
				window = []
		if window:
			await memory.flush_window(task, trace_to_history(window, force_success=False), llm=llm, task_id=trace['trace_id'])

	query_outputs: list[tuple[dict[str, Any], str]] = []
	for query in trace.get('recall_queries') or []:
		recall = memory.recall(
			query.get('query', ''),
			current_url=query.get('current_url'),
			include_snapshots=memory_path in {'task_progress', 'e2e'},
		)
		output = memory.format_memory_context(recall)
		if trace.get('include_trace_archive', True):
			output += '\n' + '\n'.join(text_blob(item.model_dump()) for item in memory.read_traces())
		query_outputs.append((query, output))
	combined_output = '\n'.join(output for _, output in query_outputs)
	return {**qa_score(query_outputs), 'output_excerpt': combined_output[:2000]}


async def run_hit_rate_trace(trace: dict[str, Any], data_dir: Path) -> dict[str, Any]:
	memory = BrowserUseMemory(data_dir)
	hits = 0
	total = 0
	for check in trace.get('checks') or []:
		write_input = check.get('write_input') or {}
		step = write_input.get('step_payload') or write_input
		task = write_input.get('task') or trace.get('description') or trace['trace_id']
		await memory.record_step(task, step, task_id=trace['trace_id'])
		read_input = check.get('read_input') or {}
		recall = memory.recall(read_input.get('query', task), current_url=read_input.get('current_url'))
		output = memory.format_memory_context(recall)
		total += 1
		if match_value(output, check.get('expected_output') or {}):
			hits += 1
	return {'hit_rate': hits / total if total else 0.0}


async def run_trace_file(trace_path: Path, memory_path: str, base_data_dir: Path, llm: Any | None) -> dict[str, Any]:
	trace = json.loads(trace_path.read_text(encoding='utf-8'))
	data_dir = base_data_dir / trace['trace_id']
	if data_dir.exists():
		shutil.rmtree(data_dir)
	data_dir.mkdir(parents=True)
	if trace.get('eval_mode', 'qa') == 'hit_rate':
		metrics = await run_hit_rate_trace(trace, data_dir)
	else:
		metrics = await run_qa_trace(trace, memory_path, data_dir, llm)
	return {'trace_id': trace['trace_id'], **metrics}


async def main_async() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument('--state', required=True)
	parser.add_argument('--paths', nargs='*')
	parser.add_argument('--trace-ids', nargs='*')
	parser.add_argument('--output')
	args = parser.parse_args()

	state_path = Path(args.state).resolve()
	state = unwrap_state(json.loads(state_path.read_text(encoding='utf-8')))
	trace_dir = state_path.parent / 'traces'
	selected_paths = set(args.paths or [])
	selected_ids = set(args.trace_ids or [])
	run_rows = []
	needs_llm = False
	for row in state['trace_registry']:
		if selected_paths and row['memory_path'] not in selected_paths:
			continue
		if selected_ids and row['trace_id'] not in selected_ids:
			continue
		if row['memory_path'] in {'session_consolidation', 'e2e'}:
			needs_llm = True
		run_rows.append(row)
	llm = load_llm() if needs_llm else None

	base_data_dir = Path(tempfile.mkdtemp(prefix='browser_use_refine_'))
	results = []
	for row in run_rows:
		results.append(await run_trace_file(trace_dir / f"{row['trace_id']}.json", row['memory_path'], base_data_dir, llm))
	payload = {'results': results, 'data_dir': str(base_data_dir)}
	print(json.dumps(payload, indent=2, ensure_ascii=False))
	if args.output:
		Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
	return 0


def main() -> int:
	return asyncio.run(main_async())


if __name__ == '__main__':
	raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
ROOT_DIR = BENCH_DIR.parent
for path in (ROOT_DIR, BENCH_DIR):
	if str(path) not in sys.path:
		sys.path.insert(0, str(path))

from dotenv import load_dotenv
from failure_reuse_site import serve_failure_reuse_site

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.llm import ChatGoogle, ChatOpenAI, ChatOpenRouter
from browser_use.memory import BrowserUseMemory

REPO_URL = 'https://github.com/browser-use/browser-use'
EVIDENCE_TAGS = (
	'<memory_context>',
	'<retrieved_run_cache>',
	'<cached_page_observations>',
	'<start_sufficiency_check>',
	'<site_memory>',
)


@dataclass(frozen=True)
class BenchTask:
	case: str
	task_id: str
	prompt: str
	start_url: str
	expected_contains: tuple[str, ...] = ()


@dataclass
class RunResult:
	case: str
	mode: str
	round: int
	task_id: str
	task: str
	steps: int
	total_tokens: int
	input_tokens: int
	output_tokens: int
	clicks: int
	extract_search_calls: int
	duration_seconds: float
	correct: bool | None
	retrieved_blocks: list[str]
	final_result_preview: str
	error: str | None
	run_dir: str

	def as_row(self) -> dict[str, Any]:
		return {
			'case': self.case,
			'mode': self.mode,
			'round': self.round,
			'task_id': self.task_id,
			'steps': self.steps,
			'total_tokens': self.total_tokens,
			'input_tokens': self.input_tokens,
			'output_tokens': self.output_tokens,
			'clicks': self.clicks,
			'extract_search_calls': self.extract_search_calls,
			'duration_seconds': f'{self.duration_seconds:.2f}',
			'correct': self.correct,
			'retrieved_blocks': ', '.join(self.retrieved_blocks),
			'final_result_preview': self.final_result_preview,
			'error': self.error or '',
			'run_dir': self.run_dir,
		}


def github_tasks() -> list[BenchTask]:
	# Prompts are plain language; `start_url` keeps full URLs for supplemental memory domain hints only.
	repo = 'browser-use github repo'
	return [
		BenchTask(
			'github',
			'repo-home-summary',
			f'For the {repo}, how many stars and forks does it have, and what is the default branch?',
			REPO_URL,
		),
		BenchTask(
			'github',
			'issues-open-count',
			f'On the Issues page for {repo}, how many issues are open?',
			f'{REPO_URL}/issues',
		),
		BenchTask(
			'github',
			'issue-1-details',
			f'Open the first issue in {repo} and tell me its title, author, and labels.',
			f'{REPO_URL}/issues',
		),
		BenchTask(
			'github',
			'issue-2-details',
			f'Open the second issue in {repo} and tell me its title, author, and labels.',
			f'{REPO_URL}/issues',
		),
		BenchTask(
			'github',
			'issues-search-memory',
			f'In {repo}, search issues for the keyword memory and give the titles of the first three results.',
			f'{REPO_URL}/issues?q=memory',
		),
		BenchTask(
			'github',
			'pulls-open-count',
			f'On the Pull requests page for {repo}, how many pull requests are open?',
			f'{REPO_URL}/pulls',
		),
		BenchTask(
			'github',
			'pull-1-details',
			f'Open the first pull request in {repo} and tell me its title, checks or CI status, and author.',
			f'{REPO_URL}/pulls',
		),
		BenchTask(
			'github',
			'actions-latest-workflow',
			f'On the Actions tab for {repo} on github, what is the latest workflow run called and did it succeed or fail?',
			f'{REPO_URL}/actions',
		),
		BenchTask(
			'github',
			'readme-install-command',
			f'What command does the README give to install the {repo} package?',
			REPO_URL,
		),
		BenchTask(
			'github',
			'examples-directory-file',
			f'In {repo}, find the examples or docs examples area and name one getting-started-style example file.',
			REPO_URL,
		),
	]


def failure_tasks(base_url: str) -> list[BenchTask]:
	dashboard = f'{base_url}/dashboard'
	return [
		BenchTask(
			'failure',
			'account-usage',
			(
				f'Go to {dashboard}. Find the account usage number on the dashboard. '
				'If anything blocks the main content, resolve it. Avoid relying on stale answers; read the live page.'
			),
			dashboard,
			expected_contains=('73,482',),
		)
	]


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='Run supplemental memory benchmark showcases.')
	parser.add_argument('showcase', choices=('github', 'failure', 'all'), help='Showcase to run.')
	parser.add_argument(
		'--mode',
		choices=('memory-off', 'memory-on', 'all'),
		default='all',
		help='Comparison mode to run.',
	)
	parser.add_argument('--provider', choices=('openrouter', 'openai', 'google'), default=os.getenv('BROWSER_USE_BENCH_PROVIDER', 'openrouter'))
	parser.add_argument('--model', default=None, help='Model name. Defaults depend on provider.')
	parser.add_argument('--max-steps', type=int, default=25)
	parser.add_argument('--limit-tasks', type=int, default=None, help='Run only the first N tasks.')
	parser.add_argument('--max-round', type=int, default=1, help='Number of times to run the selected task sequence per mode.')
	parser.add_argument('--results-dir', type=Path, default=Path('bench/results'))
	parser.add_argument(
		'--fresh',
		action='store_true',
		help='Delete everything under --results-dir before this run (remove all prior benchmark outputs and traces).',
	)
	parser.add_argument('--keep-memory', action='store_true', help='Keep an existing memory store instead of deleting it first.')
	parser.add_argument('--headless', dest='headless', action='store_true', default=True)
	parser.add_argument('--headed', dest='headless', action='store_false', help='Run a visible browser.')
	parser.add_argument('--browser-executable-path', type=Path, default=None, help='Chromium/Chrome executable path for local runs.')
	parser.add_argument('--cdp-url', default=None, help='Connect to an already running Chromium/Chrome CDP endpoint.')
	parser.add_argument('--use-cloud', action='store_true', help='Use Browser Use Cloud browser instead of launching local Chromium.')
	parser.add_argument('--failure-site-port', type=int, default=0)
	parser.add_argument(
		'--no-start-sufficiency-llm',
		dest='start_sufficiency_llm',
		action='store_false',
		default=True,
		help='Disable the optional <start_sufficiency_check> LLM evidence block.',
	)
	return parser.parse_args()


def make_llm(args: argparse.Namespace) -> Any:
	if args.provider == 'openrouter':
		return ChatOpenRouter(
			model=args.model or os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini'),
			api_key=os.getenv('OPENROUTER_API_KEY'),
		)
	if args.provider == 'openai':
		return ChatOpenAI(model=args.model or os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'))
	return ChatGoogle(model=args.model or os.getenv('GOOGLE_MODEL', 'gemini-flash-latest'), api_key=os.getenv('GOOGLE_API_KEY'))


def make_memory(root: Path, llm: Any, args: argparse.Namespace) -> BrowserUseMemory:
	root.mkdir(parents=True, exist_ok=True)
	return BrowserUseMemory(root)


def memory_context(memory: BrowserUseMemory, task: BenchTask) -> str:
	recall = memory.recall(task.prompt, current_url=task.start_url)
	return memory.format_memory_context(recall)


def action_name(action: dict[str, Any]) -> str | None:
	for key in action:
		if key not in {'interacted_element', 'result'}:
			return key
	return None


def count_actions(history: Any) -> tuple[int, int]:
	clicks = 0
	extract_search_calls = 0
	for action in history.model_actions():
		name = action_name(action)
		if name == 'click':
			clicks += 1
		if name in {'extract', 'search', 'search_page', 'find_elements', 'find_text'}:
			extract_search_calls += 1
	return clicks, extract_search_calls


def usage_numbers(history: Any) -> tuple[int, int, int]:
	usage = history.usage
	if usage is None:
		return 0, 0, 0
	total = int(getattr(usage, 'total_tokens', 0) or 0)
	input_tokens = int(getattr(usage, 'total_prompt_tokens', 0) or 0)
	output_tokens = int(getattr(usage, 'total_completion_tokens', 0) or 0)
	return total, input_tokens, output_tokens


def correctness(history: Any, expected_contains: tuple[str, ...]) -> bool | None:
	success = history.is_successful()
	if not expected_contains:
		return success
	final = (history.final_result() or '').lower()
	expected_ok = all(item.lower() in final for item in expected_contains)
	if success is False:
		return False
	return expected_ok


def evidence_blocks(text: str) -> list[str]:
	return [tag for tag in EVIDENCE_TAGS if tag in text]


async def run_one(
	task: BenchTask,
	mode: str,
	round_index: int,
	run_index: int,
	llm: Any,
	args: argparse.Namespace,
	results_root: Path,
	memory: BrowserUseMemory | None,
	record: bool = True,
) -> RunResult | None:
	run_slug = f'{mode}_round-{round_index:02d}_{run_index:03d}_{task.task_id}'
	run_dir = results_root / 'runs' / run_slug
	run_dir.mkdir(parents=True, exist_ok=True)
	profile_dir = run_dir / 'profile'
	browser_session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=args.headless,
			executable_path=args.browser_executable_path,
			cdp_url=args.cdp_url,
			use_cloud=args.use_cloud,
			user_data_dir=profile_dir,
			traces_dir=run_dir / 'traces',
			downloads_path=run_dir / 'downloads',
		)
	)

	before_injection = ''
	if memory is not None:
		before_injection = memory_context(memory, task)

	agent = None
	history = None
	error: str | None = None
	try:
		agent = Agent(
			task=task.prompt,
			llm=llm,
			browser_session=browser_session,
			memory=memory,
			use_judge=False,
		)
		history = await agent.run(max_steps=args.max_steps)
	except Exception as exc:
		error = f'{type(exc).__name__}: {exc}'
		if agent is not None and getattr(agent, 'history', None) is not None:
			history = agent.history
	finally:
		await browser_session.stop()

	if history is None:
		if not record:
			return None
		error = error or 'Agent did not return history.'
		return RunResult(
			task.case,
			mode,
			round_index,
			task.task_id,
			task.prompt,
			0,
			0,
			0,
			0,
			0,
			0,
			0,
			False,
			[],
			'',
			error,
			str(run_dir),
		)

	if record:
		if agent is not None and getattr(agent, 'token_cost_service', None) is not None:
			history.usage = await agent.token_cost_service.get_usage_summary()
		history.save_to_file(run_dir / 'history.json')
	else:
		return None

	total_tokens, input_tokens, output_tokens = usage_numbers(history)
	clicks, extract_search_calls = count_actions(history)
	after_injection = memory_context(memory, task) if memory is not None else ''
	(run_dir / 'memory_injection_before.txt').write_text(before_injection, encoding='utf-8')
	(run_dir / 'memory_injection_after.txt').write_text(after_injection, encoding='utf-8')
	injection_text = '\n\n'.join(part for part in (before_injection, after_injection) if part)
	(run_dir / 'memory_injection.txt').write_text(
		injection_text,
		encoding='utf-8',
	)
	final_result = str(history.final_result() or '')
	result = RunResult(
		case=task.case,
		mode=mode,
		round=round_index,
		task_id=task.task_id,
		task=task.prompt,
		steps=history.number_of_steps(),
		total_tokens=total_tokens,
		input_tokens=input_tokens,
		output_tokens=output_tokens,
		clicks=clicks,
		extract_search_calls=extract_search_calls,
		duration_seconds=history.total_duration_seconds(),
		correct=correctness(history, task.expected_contains),
		retrieved_blocks=evidence_blocks(before_injection),
		final_result_preview=final_result[:500],
		error=error,
		run_dir=str(run_dir),
	)
	(run_dir / 'result.json').write_text(json.dumps(result.as_row(), indent=2, ensure_ascii=False), encoding='utf-8')
	return result


async def run_sequence(
	tasks: list[BenchTask],
	mode: str,
	round_index: int,
	llm: Any,
	args: argparse.Namespace,
	results_root: Path,
	memory: BrowserUseMemory | None,
	record: bool = True,
	start_index: int = 1,
) -> list[RunResult]:
	results: list[RunResult] = []
	for offset, task in enumerate(tasks):
		run_index = start_index + offset
		print(f'[{mode} round {round_index}] {task.case}/{task.task_id}')
		result = await run_one(task, mode, round_index, run_index, llm, args, results_root, memory, record=record)
		if result is not None:
			results.append(result)
	return results


async def run_showcase(case: str, tasks: list[BenchTask], args: argparse.Namespace, suite_root: Path) -> list[RunResult]:
	if args.limit_tasks is not None:
		tasks = tasks[: args.limit_tasks]
	max_round = max(1, args.max_round)
	llm = make_llm(args)
	results: list[RunResult] = []
	memory_root = suite_root / 'memory_store'
	if memory_root.exists() and not args.keep_memory:
		shutil.rmtree(memory_root)

	if args.mode in ('memory-on', 'all'):
		memory = make_memory(memory_root / 'on', llm, args)
		for round_index in range(1, max_round + 1):
			results.extend(await run_sequence(tasks, 'memory-on', round_index, llm, args, suite_root, memory=memory))

	if args.mode in ('memory-off', 'all'):
		for round_index in range(1, max_round + 1):
			results.extend(await run_sequence(tasks, 'memory-off', round_index, llm, args, suite_root, memory=None))

	return results


def _retrieved_blocks_for_markdown(blocks: list[str]) -> str:
	"""Strip angle brackets so GitHub Markdown does not treat tags as raw HTML."""
	if not blocks:
		return '-'
	return ', '.join(b.replace('<', '').replace('>', '') for b in blocks).strip() or '-'


def write_outputs(results: list[RunResult], output_dir: Path) -> None:
	output_dir.mkdir(parents=True, exist_ok=True)
	rows = [result.as_row() for result in results]
	fieldnames = [
		'case',
		'mode',
		'round',
		'task_id',
		'steps',
		'total_tokens',
		'input_tokens',
		'output_tokens',
		'clicks',
		'extract_search_calls',
		'duration_seconds',
		'correct',
		'retrieved_blocks',
		'final_result_preview',
		'error',
		'run_dir',
	]
	with (output_dir / 'summary.csv').open('w', encoding='utf-8', newline='') as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)

	# Aggregate by mode and round for summary.md (same showcase run may include memory-on / memory-off).
	from collections import defaultdict

	tot: dict[str, dict[str, float | int]] = defaultdict(
		lambda: {'tasks': 0, 'correct': 0, 'steps': 0, 'total_tokens': 0, 'input_tokens': 0, 'output_tokens': 0, 'clicks': 0, 'extract': 0, 'duration': 0.0}
	)
	round_tot: dict[tuple[str, int], dict[str, float | int]] = defaultdict(
		lambda: {'tasks': 0, 'correct': 0, 'steps': 0, 'total_tokens': 0, 'input_tokens': 0, 'output_tokens': 0, 'clicks': 0, 'extract': 0, 'duration': 0.0}
	)

	def add_totals(bucket: dict[str, float | int], result: RunResult) -> None:
		bucket['tasks'] = int(bucket['tasks']) + 1  # type: ignore[arg-type]
		if result.correct:
			bucket['correct'] = int(bucket['correct']) + 1  # type: ignore[arg-type]
		bucket['steps'] = int(bucket['steps']) + result.steps  # type: ignore[arg-type]
		bucket['total_tokens'] = int(bucket['total_tokens']) + result.total_tokens  # type: ignore[arg-type]
		bucket['input_tokens'] = int(bucket['input_tokens']) + result.input_tokens  # type: ignore[arg-type]
		bucket['output_tokens'] = int(bucket['output_tokens']) + result.output_tokens  # type: ignore[arg-type]
		bucket['clicks'] = int(bucket['clicks']) + result.clicks  # type: ignore[arg-type]
		bucket['extract'] = int(bucket['extract']) + result.extract_search_calls  # type: ignore[arg-type]
		bucket['duration'] = float(bucket['duration']) + result.duration_seconds  # type: ignore[arg-type]

	for result in results:
		add_totals(tot[result.mode], result)
		add_totals(round_tot[(result.mode, result.round)], result)

	mode_order = ('memory-on', 'memory-off')
	agg_lines = [
		'## Totals by mode',
		'',
		'| Mode | Accuracy | Steps Σ | Total tokens Σ | Input Σ | Output Σ | Clicks Σ | Extract/search Σ | Duration Σ |',
		'|---|---:|---:|---:|---:|---:|---:|---:|---:|',
	]
	for mode in mode_order:
		if mode not in tot:
			continue
		b = tot[mode]
		acc = f"{int(b['correct'])}/{int(b['tasks'])}"
		agg_lines.append(
			f"| {mode} | {acc} | {int(b['steps'])} | {int(b['total_tokens']):,} | {int(b['input_tokens']):,} | {int(b['output_tokens']):,} "
			f"| {int(b['clicks'])} | {int(b['extract'])} | {float(b['duration']):.2f}s |"
		)
	for mode in sorted(tot.keys()):
		if mode in mode_order:
			continue
		b = tot[mode]
		acc = f"{int(b['correct'])}/{int(b['tasks'])}"
		agg_lines.append(
			f"| {mode} | {acc} | {int(b['steps'])} | {int(b['total_tokens']):,} | {int(b['input_tokens']):,} | {int(b['output_tokens']):,} "
			f"| {int(b['clicks'])} | {int(b['extract'])} | {float(b['duration']):.2f}s |"
		)
	agg_lines.extend(['', '*(Duration Σ = sum of per-task agent `duration_seconds`.)*', ''])
	round_lines = [
		'## Totals by mode and round',
		'',
		'| Mode | Round | Accuracy | Steps Σ | Total tokens Σ | Input Σ | Output Σ | Clicks Σ | Extract/search Σ | Duration Σ |',
		'|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
	]
	for mode in mode_order:
		for round_key in sorted(k for k in round_tot if k[0] == mode):
			b = round_tot[round_key]
			acc = f"{int(b['correct'])}/{int(b['tasks'])}"
			round_lines.append(
				f"| {mode} | {round_key[1]} | {acc} | {int(b['steps'])} | {int(b['total_tokens']):,} | {int(b['input_tokens']):,} | {int(b['output_tokens']):,} "
				f"| {int(b['clicks'])} | {int(b['extract'])} | {float(b['duration']):.2f}s |"
			)
	round_lines.append('')

	lines = [
		'# Supplemental Memory Showcase Results',
		'',
		*agg_lines,
		*round_lines,
		'## Per-task results',
		'',
		'| Case | Mode | Round | Task | Steps | Total tokens | Input tokens | Clicks | Extract/search calls | Duration | Correct | Retrieved blocks |',
		'|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|',
	]
	for result in results:
		lines.append(
			'| '
			+ ' | '.join(
				[
					result.case,
					result.mode,
					str(result.round),
					result.task_id,
					str(result.steps),
					str(result.total_tokens),
					str(result.input_tokens),
					str(result.clicks),
					str(result.extract_search_calls),
					f'{result.duration_seconds:.2f}s',
					str(result.correct),
					_retrieved_blocks_for_markdown(result.retrieved_blocks),
				]
			)
			+ ' |'
		)
	lines.extend(
		[
			'',
			'## Interpretation',
			'',
			'- GitHub dynamic values should be treated as live page reads; memory evidence is useful as navigation and extraction context.',
			'- Failure reuse should show whether memory-on retrieves prior blockers and successful workarounds learned earlier in the same sequence.',
		]
	)
	(output_dir / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


async def async_main() -> None:
	load_dotenv()
	args = parse_args()
	if args.fresh and args.results_dir.exists():
		shutil.rmtree(args.results_dir)
	stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
	results_root = args.results_dir / stamp
	results_root.mkdir(parents=True, exist_ok=True)

	all_results: list[RunResult] = []
	if args.showcase in ('github', 'all'):
		all_results.extend(await run_showcase('github', github_tasks(), args, results_root / 'github'))
	if args.showcase in ('failure', 'all'):
		with serve_failure_reuse_site(args.failure_site_port) as base_url:
			all_results.extend(await run_showcase('failure', failure_tasks(base_url), args, results_root / 'failure'))

	write_outputs(all_results, results_root)
	print(f'Wrote results to {results_root}')


def main() -> None:
	asyncio.run(async_main())


if __name__ == '__main__':
	main()

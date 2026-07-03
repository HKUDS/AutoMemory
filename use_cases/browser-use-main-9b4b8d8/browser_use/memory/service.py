from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, Field

from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import SystemMessage, UserMessage
from browser_use.memory.store import MemoryStore
from browser_use.memory.views import (
	MemoryCommitResult,
	MemoryExtraction,
	MemoryRecall,
	NavigationShortcut,
	ObtainedInformationCache,
	RouteStep,
	SessionTraceArchive,
	SiteFrictionMemory,
	TaskProgressSnapshot,
	normalize_text,
)

logger = logging.getLogger(__name__)


class LLMMemoryDraft(BaseModel):
	route_steps: list[str] = Field(default_factory=list)
	anchors: list[str] = Field(default_factory=list)
	answer: str | None = None
	source_url: str | None = None
	source_snippet: str | None = None
	success_condition: str | None = None
	site_notes: list[str] = Field(default_factory=list)
	trace_summary: str | None = None


class BrowserUseMemory:
	"""Supplemental cross-session memory for Browser-Use trajectories and facts."""

	def __init__(self, data_dir: str | Path):
		self.store = MemoryStore(data_dir)

	def recall(self, task: str, current_url: str | None = None, include_snapshots: bool = False) -> MemoryRecall:
		domains = self._domains_from_values([task, current_url or ''])
		return MemoryRecall(
			shortcuts=self.store.search_shortcuts(task, domains=domains, limit=3),
			caches=self.store.search_caches(task, domains=domains, limit=5),
			site_notes=self.store.search_site_notes(task, domains=domains, limit=3),
			task_snapshots=self.store.search_task_snapshots(task, limit=1) if include_snapshots else [],
		)

	def format_memory_context(self, recall: MemoryRecall) -> str:
		if recall.is_empty():
			return ''

		lines = [
			'<memory_context>',
			'Prior memory is advisory. Use shortcuts and cached facts to reduce work, but verify source/page state before final reporting unless the user explicitly accepts cached results.',
		]

		if recall.shortcuts:
			lines.append('Relevant prior navigation shortcuts:')
			for idx, shortcut in enumerate(recall.shortcuts, 1):
				route = ' -> '.join(step.summary for step in shortcut.route_steps[:6])
				lines.extend(
					[
						f'{idx}. Intent: {shortcut.task_intent}',
						f'   Domains: {", ".join(shortcut.domain_scope) or "unknown"}',
						f'   Route: {route}',
						f'   Success condition: {shortcut.success_condition}',
					]
				)

		if recall.caches:
			non_volatile = [cache for cache in recall.caches if cache.freshness_policy != 'volatile']
			volatile = [cache for cache in recall.caches if cache.freshness_policy == 'volatile']
			if non_volatile:
				lines.append('Relevant obtained-information cache:')
				for idx, cache in enumerate(non_volatile, 1):
					lines.extend(
						[
							f'{idx}. Query: {cache.query_signature}',
							f'   Cached answer: {cache.answer[:500]}',
							f'   Source: {cache.source_url}',
							f'   Observed: {cache.observed_at}',
							f'   Freshness: {cache.freshness_policy}; verification: {cache.verification_recipe}',
						]
					)
			if volatile:
				lines.append('Volatile information verification routes:')
				for idx, cache in enumerate(volatile, 1):
					lines.extend(
						[
							f'{idx}. Query: {cache.query_signature}',
							f'   Source: {cache.source_url}',
							f'   Freshness: volatile; verify current page before using any prior answer.',
							f'   Verification: {cache.verification_recipe}',
						]
					)

		if recall.site_notes:
			lines.append('Relevant site notes:')
			for note in recall.site_notes:
				lines.append(
					f'- {note.site_pattern}: {note.problem_type} - {note.symptom}'
					+ (f' Recovery: {note.successful_recovery}' if note.successful_recovery else '')
				)

		if recall.task_snapshots:
			lines.append('Resumable task state:')
			for snapshot in recall.task_snapshots:
				completed_items = self._snapshot_display_items(snapshot.completed_items)
				lines.extend(
					[
						f'- Task: {snapshot.original_task}',
						f'  Current goal: {snapshot.current_goal or "unknown"}',
						f'  Completed: {"; ".join(completed_items) or "none recorded"}',
						f'  Pending: {"; ".join(snapshot.pending_items[:5]) or "none recorded"}',
						f'  Files: {"; ".join(snapshot.available_files[:10]) or "none recorded"}',
						f'  Current URL: {snapshot.current_url or "unknown"}',
					]
				)

		lines.append('</memory_context>')
		return '\n'.join(lines)

	async def record_step(self, task: str, step_payload: dict[str, Any], task_id: str | None = None) -> None:
		friction = self._site_friction_from_step(step_payload)
		if friction:
			self.store.upsert_site_note(friction)
		snapshot = self._snapshot_from_history(task, {'history': [step_payload]}, task_id=task_id or task)
		self.store.upsert_task_snapshot(snapshot)

	async def commit_history(
		self,
		task: str,
		history_payload: dict[str, Any],
		llm: BaseChatModel | None = None,
		task_id: str | None = None,
		session_id: str | None = None,
	) -> MemoryCommitResult:
		trace_id = str(uuid4())
		result = MemoryCommitResult(trace_id=trace_id)
		history_payload = history_payload or {'history': []}

		final_result = self._final_result(history_payload)
		success = self._is_successful(history_payload)
		urls = self._urls(history_payload)
		action_names = self._action_names(history_payload)
		trace_summary = self._trace_summary(task, final_result, urls, action_names, success)
		trace = SessionTraceArchive(
			trace_id=trace_id,
			task=task,
			success=success,
			final_result=final_result,
			urls=urls,
			action_names=action_names,
			summary=trace_summary,
			raw_trace_path='',
		)
		self.store.archive_trace(trace, history_payload)
		result.trace_archived = True

		extraction = self._extract_rigorously(
			task=task,
			history_payload=history_payload,
			trace_id=trace_id,
			task_id=task_id or trace_id,
			session_id=session_id,
		)
		if llm is not None and success:
			try:
				llm_extraction = await self._extract_with_llm(
					task, history_payload, trace_id, task_id or trace_id, session_id, llm
				)
				extraction = self._merge_extractions(extraction, llm_extraction)
				result.llm_used = True
			except Exception as e:
				logger.warning('Memory LLM extraction failed; using rigorous extraction: %s', e)
				result.errors.append(f'llm_extraction_failed: {type(e).__name__}: {e}')

		for shortcut in extraction.shortcuts:
			if self._valid_shortcut(shortcut):
				self.store.upsert_shortcut(shortcut)
				result.shortcuts_written += 1
		for cache in extraction.caches:
			if cache.answer and cache.source_url:
				self.store.upsert_cache(cache)
				result.caches_written += 1
		for note in extraction.site_notes:
			if note.site_pattern and note.symptom:
				self.store.upsert_site_note(note)
				result.site_notes_written += 1
		if extraction.task_snapshot:
			self.store.upsert_task_snapshot(extraction.task_snapshot)
			result.task_snapshot_written = True
		return result

	async def flush_window(
		self,
		task: str,
		window_payload: dict[str, Any],
		llm: BaseChatModel | None = None,
		task_id: str | None = None,
		session_id: str | None = None,
	) -> MemoryCommitResult:
		return await self.commit_history(task, window_payload, llm=llm, task_id=task_id, session_id=session_id)

	def read_shortcuts(self) -> list[NavigationShortcut]:
		return self.store.list_shortcuts()

	def read_caches(self) -> list[ObtainedInformationCache]:
		return self.store.list_caches()

	def read_site_notes(self) -> list[SiteFrictionMemory]:
		return self.store.list_site_notes()

	def read_task_snapshots(self) -> list[TaskProgressSnapshot]:
		return self.store.list_task_snapshots()

	def read_traces(self) -> list[SessionTraceArchive]:
		return self.store.list_traces()

	async def _extract_with_llm(
		self,
		task: str,
		history_payload: dict[str, Any],
		trace_id: str,
		task_id: str,
		session_id: str | None,
		llm: BaseChatModel,
	) -> MemoryExtraction:
		compact_payload = self._compact_history_for_llm(history_payload)
		system = SystemMessage(
			content=(
				'Extract durable Browser-Use memory from a completed web automation trace. '
				'Create only source-grounded records useful across sessions. '
				'Navigation shortcuts must use semantic anchors, not raw element indexes alone. '
				'Obtained-information cache entries must include a source URL and verification recipe. '
				'Return a compact draft. The application will convert it into full memory records.'
			)
		)
		user = UserMessage(
			content=json.dumps(
				{
					'task': task,
					'trace_id': trace_id,
					'task_id': task_id,
					'session_id': session_id,
					'trace': compact_payload,
				},
				ensure_ascii=False,
			)
		)
		try:
			response = await llm.ainvoke([system, user], output_format=LLMMemoryDraft)
			draft = response.completion
		except Exception:
			# Some OpenRouter models do not support provider-enforced JSON schema.
			# Retry as plain JSON text while still using the real provider.
			json_system = SystemMessage(
				content=(
					system.content
					+ '\nReturn only JSON matching this schema. Keep strings concise. Do not wrap it in markdown fences.\n'
					+ json.dumps(LLMMemoryDraft.model_json_schema(), ensure_ascii=False)
				)
			)
			text_response = await llm.ainvoke([json_system, user], output_format=None)
			draft = LLMMemoryDraft.model_validate_json(self._strip_json_fences(text_response.completion))
		return self._draft_to_extraction(task, history_payload, trace_id, task_id, session_id, draft)

	def _extract_rigorously(
		self,
		task: str,
		history_payload: dict[str, Any],
		trace_id: str,
		task_id: str,
		session_id: str | None,
	) -> MemoryExtraction:
		final_result = self._final_result(history_payload)
		success = self._is_successful(history_payload)
		urls = self._urls(history_payload)
		domains = self._trusted_domains(history_payload, [*urls, task])
		route_steps = self._route_steps(history_payload)
		site_notes = [note for step in history_payload.get('history', []) if (note := self._site_friction_from_step(step))]
		snapshot = self._snapshot_from_history(task, history_payload, task_id=task_id, session_id=session_id)

		useful_final_result = self._is_useful_memory_text(final_result) and not self._is_preliminary_or_contradicted(
			final_result or ''
		)
		shortcuts: list[NavigationShortcut] = []
		caches: list[ObtainedInformationCache] = []
		final_freshness = self._freshness_policy(task, final_result or '') if final_result else 'slow-changing'
		if final_result and useful_final_result and route_steps:
			shortcuts.append(
				NavigationShortcut(
					task_intent=task,
					domain_scope=domains,
					start_conditions={'start_url': urls[0] if urls else None},
					route_steps=route_steps,
					anchors=[step.anchor_text for step in route_steps if step.anchor_text],
					page_fingerprints=[url for url in urls if url],
					success_condition=(
						self._volatile_success_condition(urls)
						if final_freshness == 'volatile'
						else self._success_condition(final_result)
					),
					source_trace_id=trace_id,
					success_count=1 if success else 0,
				)
			)
		if final_result and useful_final_result:
			source_url = self._best_source_url(urls)
			caches.append(
				ObtainedInformationCache(
					query_signature=task,
					answer=final_result[:1200],
					source_url=source_url,
					source_quote_or_snippet=final_result[:500],
					freshness_policy=self._freshness_policy(task, final_result),
					confidence=0.8,
					verification_recipe=f'Revisit {source_url} and verify the answer appears in the current page or extracted content.',
					source_trace_id=trace_id,
				)
			)
		seen_cache_answers = {normalize_text(cache.answer) for cache in caches}
		for source_url, content in self._useful_extracted_contents(history_payload):
			if not self._is_durable_fact_text(content):
				continue
			if self._is_preliminary_or_contradicted(content):
				continue
			key = normalize_text(content)
			if key in seen_cache_answers:
				continue
			seen_cache_answers.add(key)
			caches.append(
				ObtainedInformationCache(
					query_signature=task,
					answer=content[:1200],
					source_url=source_url or self._best_source_url(urls),
					source_quote_or_snippet=content[:500],
					freshness_policy=self._freshness_policy(task, content),
					confidence=0.75,
					verification_recipe=(
						f'Revisit {source_url or self._best_source_url(urls)} and verify the answer appears in the current page or extracted content.'
					),
					source_trace_id=trace_id,
				)
			)

		return MemoryExtraction(
			shortcuts=shortcuts,
			caches=caches,
			site_notes=site_notes,
			task_snapshot=snapshot,
			trace_summary=self._trace_summary(task, final_result, urls, self._action_names(history_payload), success),
		)

	def _merge_extractions(self, base: MemoryExtraction, extra: MemoryExtraction) -> MemoryExtraction:
		return MemoryExtraction(
			shortcuts=[*base.shortcuts, *extra.shortcuts],
			caches=[*base.caches, *extra.caches],
			site_notes=[*base.site_notes, *extra.site_notes],
			task_snapshot=extra.task_snapshot or base.task_snapshot,
			trace_summary=extra.trace_summary or base.trace_summary,
		)

	def _draft_to_extraction(
		self,
		task: str,
		history_payload: dict[str, Any],
		trace_id: str,
		task_id: str,
		session_id: str | None,
		draft: LLMMemoryDraft,
	) -> MemoryExtraction:
		urls = self._urls(history_payload)
		domains = self._trusted_domains(history_payload, [task, draft.source_url or self._best_source_url(urls)])
		route_steps = [
			RouteStep(action_name='prior_step', summary=step, anchor_text=None)
			for step in draft.route_steps
			if step.strip() and self._is_useful_memory_text(step)
		] or self._route_steps(history_payload)
		anchors = [anchor for anchor in draft.anchors if anchor.strip() and self._is_useful_memory_text(anchor)]
		for step in route_steps:
			if step.anchor_text:
				anchors.append(step.anchor_text)

		shortcuts: list[NavigationShortcut] = []
		draft_freshness = self._freshness_policy(task, draft.answer or draft.success_condition or '')
		if route_steps and domains and (draft.success_condition or draft.answer):
			shortcuts.append(
				NavigationShortcut(
					task_intent=task,
					domain_scope=domains,
					start_conditions={'start_url': urls[0] if urls else None},
					route_steps=route_steps,
					anchors=anchors,
					page_fingerprints=[url for url in urls if url],
					success_condition=(
						self._volatile_success_condition(urls)
						if draft_freshness == 'volatile'
						else draft.success_condition or self._success_condition(draft.answer or '')
					),
					source_trace_id=trace_id,
				)
			)

		caches: list[ObtainedInformationCache] = []
		if draft.answer and (draft.source_url or urls):
			source_url = draft.source_url or self._best_source_url(urls)
			caches.append(
				ObtainedInformationCache(
					query_signature=task,
					answer=draft.answer[:1200],
					source_url=source_url,
					source_quote_or_snippet=(draft.source_snippet or draft.answer)[:500],
					freshness_policy=self._freshness_policy(task, draft.answer),
					confidence=0.85,
					verification_recipe=f'Revisit {source_url} and verify the cached answer against current page content.',
					source_trace_id=trace_id,
				)
			)

		site_notes = [
			SiteFrictionMemory(
				site_pattern=domains[0] if domains else 'unknown',
				problem_type='other',
				symptom=note[:1000],
			)
			for note in draft.site_notes
			if note.strip() and self._is_useful_memory_text(note)
		]
		return MemoryExtraction(
			shortcuts=shortcuts,
			caches=caches,
			site_notes=site_notes,
			task_snapshot=self._snapshot_from_history(task, history_payload, task_id=task_id, session_id=session_id),
			trace_summary=draft.trace_summary,
		)

	@staticmethod
	def _strip_json_fences(text: str) -> str:
		text = text.strip()
		if text.startswith('```'):
			text = re.sub(r'^```(?:json)?\s*', '', text)
			text = re.sub(r'\s*```$', '', text)
		return text.strip()

	def _route_steps(self, history_payload: dict[str, Any]) -> list[RouteStep]:
		steps: list[RouteStep] = []
		history = history_payload.get('history', [])
		for idx, item in enumerate(history):
			model_output = item.get('model_output') or {}
			actions = model_output.get('action') or []
			before_url = (history[idx - 1].get('state') or {}).get('url') if idx > 0 else None
			after_url = (item.get('state') or {}).get('url')
			interacted = (item.get('state') or {}).get('interacted_element') or []
			for action_idx, action in enumerate(actions):
				name, params = self._action_name_and_params(action)
				if not name or name in {'done', 'noop'}:
					continue
				anchor = self._anchor_from_interacted(interacted[action_idx] if action_idx < len(interacted) else None)
				summary = self._action_summary(name, params, anchor)
				if not self._is_useful_memory_text(summary):
					continue
				steps.append(
					RouteStep(
						action_name=name,
						params=params,
						summary=summary,
						anchor_text=anchor,
						url_before=before_url,
						url_after=after_url,
					)
				)
		return steps

	def _snapshot_from_history(
		self,
		task: str,
		history_payload: dict[str, Any],
		task_id: str,
		session_id: str | None = None,
	) -> TaskProgressSnapshot:
		history = history_payload.get('history', [])
		last = history[-1] if history else {}
		model_output = last.get('model_output') or {}
		state = last.get('state') or {}
		results = last.get('result') or []
		completed: list[str] = []
		pending: list[str] = []
		for item in history:
			output = item.get('model_output') or {}
			for result in item.get('result') or []:
				content = result.get('extracted_content') or result.get('long_term_memory')
				if isinstance(content, str) and self._is_useful_memory_text(content):
					completed.append(content[:300])
			if output.get('evaluation_previous_goal'):
				completed.append(str(output['evaluation_previous_goal'])[:300])
			if output.get('next_goal'):
				pending = [str(output['next_goal'])[:300]]
		archived = self._is_successful(history_payload) is True
		if archived:
			pending = []
		available_files: list[str] = []
		for item in history:
			for result in item.get('result') or []:
				for value in (result.get('extracted_content'), result.get('long_term_memory')):
					if isinstance(value, str):
						for path in self._file_paths_from_text(value):
							if path not in available_files:
								available_files.append(path)
		blocking = None
		for result in results:
			if result.get('error'):
				blocking = str(result['error'])[:500]
		return TaskProgressSnapshot(
			task_id=task_id,
			session_id=session_id,
			original_task=task,
			current_goal=(model_output.get('memory') if archived else model_output.get('next_goal') or model_output.get('memory')),
			completed_items=completed[-10:],
			pending_items=pending,
			current_url=state.get('url'),
			tabs=state.get('tabs') or [],
			available_files=available_files,
			last_successful_action=(self._action_names({'history': [last]}) or [None])[-1],
			blocking_issue=blocking,
			archived=archived,
		)

	def _site_friction_from_step(self, step_payload: dict[str, Any]) -> SiteFrictionMemory | None:
		state = step_payload.get('state') or {}
		site = self._domain_from_url(state.get('url') or '')
		results = step_payload.get('result') or []
		errors = [str(result.get('error')) for result in results if result.get('error')]
		long_term_warnings = [str(result.get('long_term_memory')) for result in results if result.get('long_term_memory')]
		if not errors and not long_term_warnings:
			return None
		symptom = '\n'.join([*errors, *long_term_warnings])[:1000]
		problem_type = self._problem_type(symptom)
		successful_recovery = self._successful_recovery_from_text(symptom)
		return SiteFrictionMemory(
			site_pattern=site or 'unknown',
			problem_type=problem_type,
			symptom=symptom,
			failed_actions=self._action_names({'history': [step_payload]}),
			successful_recovery=successful_recovery,
		)

	def _compact_history_for_llm(self, history_payload: dict[str, Any]) -> dict[str, Any]:
		history = history_payload.get('history', [])
		compact_steps: list[dict[str, Any]] = []
		for item in history[-30:]:
			model_output = item.get('model_output') or {}
			state = item.get('state') or {}
			compact_steps.append(
				{
					'url': state.get('url'),
					'title': state.get('title'),
					'memory': model_output.get('memory'),
					'evaluation': model_output.get('evaluation_previous_goal'),
					'next_goal': model_output.get('next_goal'),
					'actions': model_output.get('action') or [],
					'results': item.get('result') or [],
				}
			)
		return {
			'success': self._is_successful(history_payload),
			'final_result': self._final_result(history_payload),
			'urls': self._urls(history_payload),
			'action_names': self._action_names(history_payload),
			'steps': compact_steps,
		}

	@staticmethod
	def _valid_shortcut(shortcut: NavigationShortcut) -> bool:
		if not shortcut.success_condition or not shortcut.domain_scope or not shortcut.route_steps:
			return False
		click_like = [s for s in shortcut.route_steps if s.action_name in {'click', 'input', 'select_dropdown_option'}]
		return not click_like or any(step.anchor_text or step.summary for step in click_like)

	@staticmethod
	def _action_name_and_params(action: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
		for key, value in action.items():
			if key == 'interacted_element':
				continue
			if value is not None:
				return key, value if isinstance(value, dict) else {'value': value}
		return None, {}

	@staticmethod
	def _action_summary(name: str, params: dict[str, Any], anchor: str | None = None) -> str:
		if name == 'navigate':
			return f'navigate {params.get("url", "")}'.strip()
		if name == 'search':
			return f'search {params.get("query", "")}'.strip()
		if name == 'click':
			return f'click {anchor or params.get("index") or "target"}'
		if name == 'input':
			return f'input into {anchor or params.get("index") or "field"}'
		return f'{name} {anchor or ""}'.strip()

	@staticmethod
	def _anchor_from_interacted(interacted: Any) -> str | None:
		if not interacted:
			return None
		if isinstance(interacted, str):
			return interacted[:300]
		try:
			text = json.dumps(interacted, ensure_ascii=False)
		except TypeError:
			text = str(interacted)
		for key in ('text', 'aria_label', 'role', 'tag_name', 'xpath'):
			if isinstance(interacted, dict) and interacted.get(key):
				return str(interacted[key])[:300]
		return text[:300] if text and text != 'null' else None

	@staticmethod
	def _final_result(history_payload: dict[str, Any]) -> str | None:
		for item in reversed(history_payload.get('history', [])):
			for result in reversed(item.get('result') or []):
				if result.get('extracted_content'):
					return str(result['extracted_content'])
		return None

	@staticmethod
	def _is_successful(history_payload: dict[str, Any]) -> bool | None:
		for item in reversed(history_payload.get('history', [])):
			for result in reversed(item.get('result') or []):
				if result.get('is_done') is True:
					return result.get('success')
		return None

	@staticmethod
	def _urls(history_payload: dict[str, Any]) -> list[str]:
		urls: list[str] = []
		for item in history_payload.get('history', []):
			url = (item.get('state') or {}).get('url')
			if url and url not in urls:
				urls.append(url)
		return urls

	def _action_names(self, history_payload: dict[str, Any]) -> list[str]:
		names: list[str] = []
		for item in history_payload.get('history', []):
			for action in (item.get('model_output') or {}).get('action') or []:
				name, _ = self._action_name_and_params(action)
				if name:
					names.append(name)
		return names

	@staticmethod
	def _domains_from_values(values: list[str | None]) -> list[str]:
		domains: list[str] = []
		for value in values:
			if not value:
				continue
			for match in re.findall(r'https?://[^\s<>"\']+|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}', value):
				domain = BrowserUseMemory._domain_from_url(match)
				if domain and domain not in domains:
					domains.append(domain)
		return domains

	@staticmethod
	def _domain_from_url(value: str) -> str:
		if not value:
			return ''
		url = value if '://' in value else f'https://{value}'
		parsed = urlparse(url)
		return parsed.netloc.lower().removeprefix('www.')

	@staticmethod
	def _best_source_url(urls: list[str]) -> str:
		for url in reversed(urls):
			if url and not url.startswith('about:'):
				return url
		return urls[-1] if urls else 'unknown'

	@staticmethod
	def _success_condition(final_result: str) -> str:
		return f'Verify the page or extracted content supports: {final_result[:500]}'

	@staticmethod
	def _volatile_success_condition(urls: list[str]) -> str:
		source_url = BrowserUseMemory._best_source_url(urls)
		return f'Verify current status directly at {source_url}; do not reuse prior volatile answer as final.'

	@staticmethod
	def _freshness_policy(task: str, answer: str) -> str:
		text = normalize_text(f'{task} {answer}')
		if any(word in text for word in ['today', 'latest', 'current', 'price', 'weather', 'score']):
			return 'volatile'
		if any(word in text for word in ['license', 'copyright', 'founder', 'repo', 'documentation']):
			return 'slow-changing'
		return 'slow-changing'

	@staticmethod
	def _is_useful_memory_text(value: str | None) -> bool:
		if not value:
			return False
		text = normalize_text(value)
		noise_markers = (
			'warmup noise',
			'background planning step',
			'post-answer noise',
			'pre-flush noise',
			'irrelevant search refinement',
			'generic search refinement',
			'unrelated blog sidebar',
			'newsletter signup',
			'no durable observation',
			'without extracting a durable fact',
			'copied no source-grounded answer',
		)
		return len(text) >= 12 and not any(marker in text for marker in noise_markers)

	@staticmethod
	def _is_durable_fact_text(value: str) -> bool:
		text = normalize_text(value)
		durable_markers = (
			'=',
			'/tmp/',
			' endpoint ',
			' state ',
			' states ',
			' confirmed ',
			' accepted ',
			' saved at ',
			' under the ',
			' according to ',
			' priority support',
		)
		non_fact_prefixes = ('navigated to ', 'clicked ', 'opened ')
		return any(marker in text for marker in durable_markers) and not any(
			text.startswith(prefix) for prefix in non_fact_prefixes
		)

	@staticmethod
	def _is_preliminary_or_contradicted(value: str) -> bool:
		text = normalize_text(value)
		markers = (
			'unverified',
			'preliminary',
			'rumor',
			'forum claim',
			'stale',
			'should not be used',
			'contradicted',
			'discard',
		)
		return any(marker in text for marker in markers)

	@staticmethod
	def _file_paths_from_text(value: str) -> list[str]:
		return re.findall(r'(?:/[\w.\-]+)+(?:\.[A-Za-z0-9]+)?', value)

	def _useful_extracted_contents(self, history_payload: dict[str, Any]) -> list[tuple[str, str]]:
		contents: list[tuple[str, str]] = []
		for item in history_payload.get('history', []):
			source_url = (item.get('state') or {}).get('url') or ''
			for result in item.get('result') or []:
				for value in (result.get('extracted_content'), result.get('long_term_memory')):
					if isinstance(value, str) and self._is_useful_memory_text(value):
						contents.append((source_url, value))
		return contents

	def _trusted_domains(self, history_payload: dict[str, Any], fallback_values: list[str | None]) -> list[str]:
		values = [
			source_url
			for source_url, content in self._useful_extracted_contents(history_payload)
			if self._is_durable_fact_text(content) and not self._is_preliminary_or_contradicted(content)
		]
		return self._domains_from_values([*values, *fallback_values])

	@staticmethod
	def _snapshot_display_items(items: list[str]) -> list[str]:
		if len(items) <= 14:
			return items
		head = items[:5]
		tail = items[-9:]
		seen: set[str] = set()
		result: list[str] = []
		for item in [*head, *tail]:
			if item and item not in seen:
				seen.add(item)
				result.append(item)
		return result

	@staticmethod
	def _successful_recovery_from_text(value: str) -> str | None:
		match = re.search(r'successful recovery:\s*(.+?)(?:\n|$)', value, flags=re.IGNORECASE | re.DOTALL)
		if not match:
			return None
		return match.group(1).strip().rstrip('.')[:500]

	@staticmethod
	def _problem_type(symptom: str) -> str:
		text = normalize_text(symptom)
		if 'captcha' in text or 'cloudflare' in text:
			return 'captcha'
		if 'login' in text or 'redirect' in text:
			return 'login_redirect'
		if 'pdf' in text:
			return 'pdf_viewer'
		if 'blocked' in text or '403' in text or 'access denied' in text:
			return 'blocked'
		if 'timeout' in text or 'readiness' in text:
			return 'readiness_timeout'
		if any(
			marker in text
			for marker in [
				'selector',
				'index',
				'xpath',
				'dom reordered',
				'reordered',
				'stale element',
				'became stale',
				'virtualized',
				'raw element',
				'aria label',
			]
		):
			return 'selector_instability'
		if 'empty' in text or 'blank' in text:
			return 'empty_dom'
		if 'click' in text:
			return 'failed_click'
		return 'other'

	@staticmethod
	def _trace_summary(
		task: str,
		final_result: str | None,
		urls: list[str],
		action_names: list[str],
		success: bool | None,
	) -> str:
		status = 'successful' if success else 'failed' if success is False else 'incomplete'
		return (
			f'Task {status}: {task}. '
			f'Actions: {", ".join(action_names[:20]) or "none"}. '
			f'URLs: {", ".join(urls[:10]) or "none"}. '
			f'Final result: {(final_result or "")[:700]}'
		)

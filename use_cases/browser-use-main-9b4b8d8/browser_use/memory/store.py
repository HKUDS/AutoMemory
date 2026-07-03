from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from browser_use.memory.views import (
	NavigationShortcut,
	ObtainedInformationCache,
	SessionTraceArchive,
	SiteFrictionMemory,
	TaskProgressSnapshot,
	normalize_text,
	utc_now,
)

T = TypeVar('T', bound=BaseModel)


class MemoryStore:
	"""SQLite persistence for Browser-Use supplemental memory records."""

	def __init__(self, data_dir: str | Path):
		self.data_dir = Path(data_dir).expanduser().resolve()
		self.data_dir.mkdir(parents=True, exist_ok=True)
		self.db_path = self.data_dir / 'browser_use_memory.sqlite3'
		self.traces_dir = self.data_dir / 'traces'
		self.traces_dir.mkdir(parents=True, exist_ok=True)
		self._init_db()

	def _connect(self) -> sqlite3.Connection:
		conn = sqlite3.connect(self.db_path)
		conn.row_factory = sqlite3.Row
		return conn

	def _init_db(self) -> None:
		with self._connect() as conn:
			conn.executescript(
				"""
				CREATE TABLE IF NOT EXISTS memories (
					kind TEXT NOT NULL,
					id TEXT NOT NULL,
					dedupe_key TEXT NOT NULL,
					search_text TEXT NOT NULL,
					data TEXT NOT NULL,
					created_at TEXT NOT NULL,
					updated_at TEXT NOT NULL,
					PRIMARY KEY(kind, id)
				);
				CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedupe
					ON memories(kind, dedupe_key);
				CREATE INDEX IF NOT EXISTS idx_memories_kind_search
					ON memories(kind, search_text);
				"""
			)

	def upsert_shortcut(self, record: NavigationShortcut) -> NavigationShortcut:
		key = self._shortcut_key(record)
		existing = self._get_by_dedupe('shortcut', key, NavigationShortcut)
		if existing:
			record.shortcut_id = existing.shortcut_id
			record.created_at = existing.created_at
			record.success_count = existing.success_count + max(record.success_count, 1)
			record.failure_count = existing.failure_count + record.failure_count
			record.anchors = self._merge_strings(existing.anchors, record.anchors)
			record.page_fingerprints = self._merge_strings(existing.page_fingerprints, record.page_fingerprints)
			if len(record.route_steps) < len(existing.route_steps):
				record.route_steps = existing.route_steps
		self._upsert('shortcut', record.shortcut_id, key, self._shortcut_search_text(record), record)
		return record

	def upsert_cache(self, record: ObtainedInformationCache) -> ObtainedInformationCache:
		key = self._cache_key(record)
		existing = self._get_by_dedupe('cache', key, ObtainedInformationCache)
		if existing:
			record.cache_id = existing.cache_id
			record.observed_at = utc_now()
			record.confidence = max(existing.confidence, record.confidence)
		self._upsert('cache', record.cache_id, key, self._cache_search_text(record), record)
		return record

	def upsert_site_note(self, record: SiteFrictionMemory) -> SiteFrictionMemory:
		key = self._site_note_key(record)
		existing = self._get_by_dedupe('site_note', key, SiteFrictionMemory)
		if existing:
			record.friction_id = existing.friction_id
			record.last_seen_at = utc_now()
			record.success_count = existing.success_count + record.success_count
			record.failure_count = existing.failure_count + max(record.failure_count, 1)
			record.failed_actions = self._merge_strings(existing.failed_actions, record.failed_actions)
			record.successful_recovery = record.successful_recovery or existing.successful_recovery
		self._upsert('site_note', record.friction_id, key, self._site_note_search_text(record), record)
		return record

	def upsert_task_snapshot(self, record: TaskProgressSnapshot) -> TaskProgressSnapshot:
		key = normalize_text(record.task_id or record.original_task)
		existing = self._get_by_dedupe('task_snapshot', key, TaskProgressSnapshot)
		if existing:
			record.snapshot_id = existing.snapshot_id
			record.created_at = existing.created_at
			record.completed_items = self._merge_strings(existing.completed_items, record.completed_items)[-25:]
			record.pending_items = [] if record.archived else record.pending_items or existing.pending_items
			record.available_files = self._merge_strings(existing.available_files, record.available_files)
			record.current_url = record.current_url or existing.current_url
			record.tabs = record.tabs or existing.tabs
			record.blocking_issue = record.blocking_issue or existing.blocking_issue
			record.last_successful_action = record.last_successful_action or existing.last_successful_action
		record.updated_at = utc_now()
		self._upsert('task_snapshot', record.snapshot_id, key, self._snapshot_search_text(record), record)
		return record

	def archive_trace(self, record: SessionTraceArchive, raw_trace: dict[str, Any]) -> SessionTraceArchive:
		raw_path = self.traces_dir / f'{record.trace_id}.json'
		raw_path.write_text(json.dumps(raw_trace, indent=2, ensure_ascii=False), encoding='utf-8')
		record.raw_trace_path = str(raw_path)
		self._upsert('trace', record.trace_id, record.trace_id, self._trace_search_text(record), record)
		return record

	def list_shortcuts(self) -> list[NavigationShortcut]:
		return self._list_kind('shortcut', NavigationShortcut)

	def list_caches(self) -> list[ObtainedInformationCache]:
		return self._list_kind('cache', ObtainedInformationCache)

	def list_site_notes(self) -> list[SiteFrictionMemory]:
		return self._list_kind('site_note', SiteFrictionMemory)

	def list_task_snapshots(self) -> list[TaskProgressSnapshot]:
		return self._list_kind('task_snapshot', TaskProgressSnapshot)

	def list_traces(self) -> list[SessionTraceArchive]:
		return self._list_kind('trace', SessionTraceArchive)

	def search_shortcuts(self, query: str, domains: Iterable[str] = (), limit: int = 3) -> list[NavigationShortcut]:
		return self._rank(self.list_shortcuts(), query, domains, self._shortcut_search_text, limit)

	def search_caches(self, query: str, domains: Iterable[str] = (), limit: int = 5) -> list[ObtainedInformationCache]:
		return self._rank(self.list_caches(), query, domains, self._cache_search_text, limit)

	def search_site_notes(self, query: str = '', domains: Iterable[str] = (), limit: int = 3) -> list[SiteFrictionMemory]:
		domain_terms = [d for d in domains if d]
		records = self.list_site_notes()
		if domain_terms:
			records = [r for r in records if any(d in r.site_pattern or r.site_pattern in d for d in domain_terms)]
		query_terms = self._term_set(query)
		scored: list[tuple[float, SiteFrictionMemory]] = []
		for record in records:
			text = normalize_text(self._site_note_search_text(record))
			text_terms = self._term_set(text)
			score = float(len(query_terms & text_terms))
			score += float(record.success_count - record.failure_count) * 0.1
			scored.append((score, record))
		scored.sort(key=lambda item: (item[0], item[1].last_seen_at), reverse=True)
		return [record for _, record in scored[:limit]]

	def search_task_snapshots(self, query: str, limit: int = 1) -> list[TaskProgressSnapshot]:
		records = self.list_task_snapshots()
		ranked = self._rank(records, query, (), self._snapshot_search_text, limit)
		if ranked:
			return ranked
		if self._looks_like_resume_query(query):
			return sorted(records, key=lambda r: r.updated_at, reverse=True)[:limit]
		return []

	def _upsert(self, kind: str, record_id: str, dedupe_key: str, search_text: str, record: BaseModel) -> None:
		now = utc_now()
		with self._connect() as conn:
			conn.execute(
				"""
				INSERT INTO memories(kind, id, dedupe_key, search_text, data, created_at, updated_at)
				VALUES (?, ?, ?, ?, ?, ?, ?)
				ON CONFLICT(kind, dedupe_key) DO UPDATE SET
					id=excluded.id,
					search_text=excluded.search_text,
					data=excluded.data,
					updated_at=excluded.updated_at
				""",
				(kind, record_id, dedupe_key, normalize_text(search_text), record.model_dump_json(), now, now),
			)

	def _get_by_dedupe(self, kind: str, dedupe_key: str, model: type[T]) -> T | None:
		with self._connect() as conn:
			row = conn.execute(
				'SELECT data FROM memories WHERE kind = ? AND dedupe_key = ?',
				(kind, dedupe_key),
			).fetchone()
		if row is None:
			return None
		return model.model_validate_json(row['data'])

	def _list_kind(self, kind: str, model: type[T]) -> list[T]:
		with self._connect() as conn:
			rows = conn.execute('SELECT data FROM memories WHERE kind = ?', (kind,)).fetchall()
		return [model.model_validate_json(row['data']) for row in rows]

	def _rank(
		self,
		records: list[T],
		query: str,
		domains: Iterable[str],
		search_text_func,
		limit: int,
	) -> list[T]:
		query_terms = self._term_set(query)
		domain_terms = {normalize_text(d) for d in domains if d}
		scored: list[tuple[float, T]] = []
		for record in records:
			text = normalize_text(search_text_func(record))
			text_terms = self._term_set(text)
			score = float(len(query_terms & text_terms))
			if domain_terms and any(domain in text for domain in domain_terms):
				score += 5.0
			score += float(getattr(record, 'success_count', 0)) * 0.2
			score -= float(getattr(record, 'failure_count', 0)) * 0.1
			if score > 0:
				scored.append((score, record))
		scored.sort(key=lambda item: item[0], reverse=True)
		return [record for _, record in scored[:limit]]

	@staticmethod
	def _looks_like_resume_query(query: str) -> bool:
		text = normalize_text(query)
		return any(
			marker in text
			for marker in [
				'resume',
				'continue',
				'pick up',
				'where should the browser',
				'what document',
				'downloaded',
				'next action',
			]
		)

	@staticmethod
	def _term_set(value: str) -> set[str]:
		return {term.strip('.,:;?!()[]{}"\'') for term in normalize_text(value).split() if term.strip('.,:;?!()[]{}"\'')}

	@staticmethod
	def _merge_strings(left: list[str], right: list[str]) -> list[str]:
		seen: set[str] = set()
		result: list[str] = []
		for value in [*left, *right]:
			if value and value not in seen:
				seen.add(value)
				result.append(value)
		return result

	@staticmethod
	def _shortcut_key(record: NavigationShortcut) -> str:
		return normalize_text('|'.join([record.task_intent, ','.join(record.domain_scope), record.success_condition]))

	@staticmethod
	def _cache_key(record: ObtainedInformationCache) -> str:
		return normalize_text('|'.join([record.query_signature, record.source_url, record.answer]))

	@staticmethod
	def _site_note_key(record: SiteFrictionMemory) -> str:
		return normalize_text('|'.join([record.site_pattern, record.problem_type, record.symptom[:160]]))

	@staticmethod
	def _shortcut_search_text(record: NavigationShortcut) -> str:
		return ' '.join(
			[
				record.task_intent,
				' '.join(record.domain_scope),
				' '.join(record.anchors),
				' '.join(step.summary for step in record.route_steps),
				record.success_condition,
			]
		)

	@staticmethod
	def _cache_search_text(record: ObtainedInformationCache) -> str:
		return ' '.join([record.query_signature, record.answer, record.source_url, record.source_quote_or_snippet])

	@staticmethod
	def _site_note_search_text(record: SiteFrictionMemory) -> str:
		return ' '.join(
			[
				record.site_pattern,
				record.problem_type,
				record.symptom,
				' '.join(record.failed_actions),
				record.successful_recovery or '',
			]
		)

	@staticmethod
	def _snapshot_search_text(record: TaskProgressSnapshot) -> str:
		return ' '.join(
			[
				record.original_task,
				record.task_id,
				record.session_id or '',
				record.current_goal or '',
				' '.join(record.completed_items),
				' '.join(record.pending_items),
				record.current_url or '',
				' '.join(record.available_files),
				record.blocking_issue or '',
			]
		)

	@staticmethod
	def _trace_search_text(record: SessionTraceArchive) -> str:
		return ' '.join([record.task, record.summary, record.final_result or '', ' '.join(record.urls)])

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now() -> str:
	return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
	return re.sub(r'\s+', ' ', value.strip().lower())


class RouteStep(BaseModel):
	action_name: str
	summary: str
	params: dict[str, Any] = Field(default_factory=dict)
	anchor_text: str | None = None
	url_before: str | None = None
	url_after: str | None = None


class NavigationShortcut(BaseModel):
	shortcut_id: str = Field(default_factory=lambda: str(uuid4()))
	task_intent: str
	domain_scope: list[str] = Field(default_factory=list)
	start_conditions: dict[str, Any] = Field(default_factory=dict)
	route_steps: list[RouteStep] = Field(default_factory=list)
	anchors: list[str] = Field(default_factory=list)
	page_fingerprints: list[str] = Field(default_factory=list)
	success_condition: str
	source_trace_id: str
	created_at: str = Field(default_factory=utc_now)
	last_verified_at: str = Field(default_factory=utc_now)
	success_count: int = 1
	failure_count: int = 0
	sensitivity_scope: Literal['global', 'profile', 'user', 'private'] = 'profile'

	@field_validator('domain_scope', 'anchors', 'page_fingerprints')
	@classmethod
	def _dedup_strings(cls, values: list[str]) -> list[str]:
		seen: set[str] = set()
		result: list[str] = []
		for value in values:
			clean = value.strip()
			if clean and clean not in seen:
				seen.add(clean)
				result.append(clean)
		return result


class ObtainedInformationCache(BaseModel):
	cache_id: str = Field(default_factory=lambda: str(uuid4()))
	query_signature: str
	answer: str
	source_url: str
	source_quote_or_snippet: str
	observed_at: str = Field(default_factory=utc_now)
	freshness_policy: Literal['static', 'slow-changing', 'volatile', 'task-specified'] = 'slow-changing'
	confidence: float = Field(default=0.7, ge=0.0, le=1.0)
	verification_recipe: str
	source_trace_id: str
	sensitivity_scope: Literal['global', 'profile', 'user', 'private'] = 'profile'


class SiteFrictionMemory(BaseModel):
	friction_id: str = Field(default_factory=lambda: str(uuid4()))
	site_pattern: str
	problem_type: Literal[
		'readiness_timeout',
		'captcha',
		'blocked',
		'failed_click',
		'selector_instability',
		'empty_dom',
		'pdf_viewer',
		'login_redirect',
		'other',
	]
	symptom: str
	failed_actions: list[str] = Field(default_factory=list)
	successful_recovery: str | None = None
	applicability_conditions: str | None = None
	last_seen_at: str = Field(default_factory=utc_now)
	success_count: int = 0
	failure_count: int = 1


class TaskProgressSnapshot(BaseModel):
	snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
	task_id: str
	session_id: str | None = None
	original_task: str
	current_goal: str | None = None
	completed_items: list[str] = Field(default_factory=list)
	pending_items: list[str] = Field(default_factory=list)
	current_url: str | None = None
	tabs: list[dict[str, Any]] = Field(default_factory=list)
	available_files: list[str] = Field(default_factory=list)
	last_successful_action: str | None = None
	blocking_issue: str | None = None
	created_at: str = Field(default_factory=utc_now)
	updated_at: str = Field(default_factory=utc_now)
	archived: bool = False


class SessionTraceArchive(BaseModel):
	trace_id: str = Field(default_factory=lambda: str(uuid4()))
	task: str
	success: bool | None = None
	final_result: str | None = None
	urls: list[str] = Field(default_factory=list)
	action_names: list[str] = Field(default_factory=list)
	summary: str
	raw_trace_path: str
	created_at: str = Field(default_factory=utc_now)
	sensitive_data_redacted: bool = False


class MemoryExtraction(BaseModel):
	shortcuts: list[NavigationShortcut] = Field(default_factory=list)
	caches: list[ObtainedInformationCache] = Field(default_factory=list)
	site_notes: list[SiteFrictionMemory] = Field(default_factory=list)
	task_snapshot: TaskProgressSnapshot | None = None
	trace_summary: str | None = None


class MemoryRecall(BaseModel):
	shortcuts: list[NavigationShortcut] = Field(default_factory=list)
	caches: list[ObtainedInformationCache] = Field(default_factory=list)
	site_notes: list[SiteFrictionMemory] = Field(default_factory=list)
	task_snapshots: list[TaskProgressSnapshot] = Field(default_factory=list)

	def is_empty(self) -> bool:
		return not (self.shortcuts or self.caches or self.site_notes or self.task_snapshots)


class MemoryCommitResult(BaseModel):
	trace_id: str
	shortcuts_written: int = 0
	caches_written: int = 0
	site_notes_written: int = 0
	task_snapshot_written: bool = False
	trace_archived: bool = False
	llm_used: bool = False
	errors: list[str] = Field(default_factory=list)

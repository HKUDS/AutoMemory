import os

import pytest
from dotenv import load_dotenv

from browser_use.agent.prompts import AgentMessagePrompt
from browser_use.browser.views import BrowserStateSummary
from browser_use.dom.views import SerializedDOMState
from browser_use.llm import ChatOpenRouter
from browser_use.memory import BrowserUseMemory


def sample_history(answer: str = 'The browser-use repository uses the MIT License.') -> dict:
	return {
		'history': [
			{
				'model_output': {
					'evaluation_previous_goal': 'Starting.',
					'memory': 'Need to open the browser-use repository.',
					'next_goal': 'Navigate to GitHub.',
					'action': [{'navigate': {'url': 'https://github.com/browser-use/browser-use', 'new_tab': False}}],
				},
				'result': [{'extracted_content': 'Navigated to https://github.com/browser-use/browser-use'}],
				'state': {
					'url': 'https://github.com/browser-use/browser-use',
					'title': 'GitHub - browser-use/browser-use',
					'tabs': [],
					'interacted_element': [None],
				},
			},
			{
				'model_output': {
					'evaluation_previous_goal': 'Repository loaded and LICENSE is visible.',
					'memory': 'LICENSE file is visible in the repository root.',
					'next_goal': 'Open LICENSE.',
					'action': [{'click': {'index': 300}}],
				},
				'result': [{'extracted_content': 'Clicked LICENSE'}],
				'state': {
					'url': 'https://github.com/browser-use/browser-use',
					'title': 'GitHub - browser-use/browser-use',
					'tabs': [],
					'interacted_element': [{'text': 'LICENSE', 'aria_label': 'LICENSE', 'role': 'link'}],
				},
			},
			{
				'model_output': {
					'evaluation_previous_goal': 'License file confirms MIT License.',
					'memory': 'The repo license is MIT.',
					'next_goal': 'Report the answer.',
					'action': [{'done': {'text': answer, 'success': True}}],
				},
				'result': [{'is_done': True, 'success': True, 'extracted_content': answer}],
				'state': {
					'url': 'https://github.com/browser-use/browser-use/blob/main/LICENSE',
					'title': 'LICENSE',
					'tabs': [],
					'interacted_element': [None],
				},
			},
		]
	}


@pytest.mark.asyncio
async def test_memory_round_trip_for_all_components(tmp_path):
	memory = BrowserUseMemory(tmp_path)

	result = await memory.commit_history(
		'Find license of the browser-use repo',
		sample_history(),
		task_id='license-task',
		session_id='session-1',
	)

	assert result.trace_archived
	assert result.shortcuts_written == 1
	assert result.caches_written == 1
	assert result.task_snapshot_written
	assert memory.read_shortcuts()[0].route_steps[1].anchor_text == 'LICENSE'
	assert memory.read_caches()[0].answer.startswith('The browser-use repository uses')
	assert memory.read_task_snapshots()[0].current_url.endswith('/LICENSE')
	assert memory.read_traces()[0].raw_trace_path


@pytest.mark.asyncio
async def test_memory_deduplicates_and_updates_existing_records(tmp_path):
	memory = BrowserUseMemory(tmp_path)
	await memory.commit_history('Find license of the browser-use repo', sample_history(), task_id='same-task')
	await memory.commit_history('Find license of the browser-use repo', sample_history(), task_id='same-task')

	shortcuts = memory.read_shortcuts()
	caches = memory.read_caches()
	snapshots = memory.read_task_snapshots()

	assert len(shortcuts) == 1
	assert shortcuts[0].success_count >= 2
	assert len(caches) == 1
	assert len(snapshots) == 1


@pytest.mark.asyncio
async def test_memory_lifecycle_reinit_and_recall(tmp_path):
	first = BrowserUseMemory(tmp_path)
	await first.commit_history('Find license of the browser-use repo', sample_history(), task_id='license-task')

	second = BrowserUseMemory(tmp_path)
	recall = second.recall('What is the browser-use repo license?', current_url='https://github.com/browser-use/browser-use')
	context = second.format_memory_context(recall)

	assert recall.shortcuts
	assert recall.caches
	assert '<memory_context>' in context
	assert 'MIT License' in context
	assert 'LICENSE' in context


def test_memory_context_is_injected_into_agent_prompt(tmp_path):
	memory = BrowserUseMemory(tmp_path)
	recall = memory.recall('anything')
	context = memory.format_memory_context(recall)
	assert context == ''

	state = BrowserStateSummary(
		dom_state=SerializedDOMState(_root=None, selector_map={}),
		url='https://example.com',
		title='Example',
		tabs=[],
	)
	prompt = AgentMessagePrompt(
		browser_state_summary=state,
		file_system=None,
		task='Task',
		memory_context='<memory_context>\nCached answer: MIT License\n</memory_context>',
	).get_user_message(use_vision=False)

	assert '<memory_context>' in prompt.text
	assert 'Cached answer: MIT License' in prompt.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_memory_llm_extraction_uses_real_openrouter_provider(tmp_path):
	load_dotenv()
	api_key = os.getenv('OPENROUTER_API_KEY')
	if not api_key:
		pytest.skip('OPENROUTER_API_KEY is required for real LLM memory extraction test')

	llm = ChatOpenRouter(
		model=os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini'),
		api_key=api_key,
	)
	memory = BrowserUseMemory(tmp_path)
	result = await memory.commit_history(
		'Find license of the browser-use repo',
		sample_history(),
		llm=llm,
		task_id='llm-license-task',
		session_id='session-llm',
	)

	assert result.trace_archived
	assert result.llm_used
	assert memory.read_shortcuts()
	assert memory.read_caches()

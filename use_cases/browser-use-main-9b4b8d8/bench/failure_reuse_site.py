from __future__ import annotations

import contextlib
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Supplemental Memory Failure Reuse Dashboard</title>
<style>
body {
	font-family: Arial, sans-serif;
	margin: 0;
	background: #f6f8fb;
	color: #172033;
}
header {
	background: #172033;
	color: white;
	padding: 18px 32px;
}
main {
	max-width: 960px;
	margin: 0 auto;
	padding: 32px;
}
.card {
	background: white;
	border: 1px solid #d8dee9;
	border-radius: 12px;
	box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
	margin-bottom: 24px;
	padding: 24px;
}
.row {
	align-items: center;
	display: flex;
	gap: 12px;
}
input {
	border: 1px solid #aeb7c4;
	border-radius: 8px;
	font-size: 16px;
	padding: 10px 12px;
	width: 320px;
}
button {
	border: 0;
	border-radius: 8px;
	cursor: pointer;
	font-size: 15px;
	padding: 10px 14px;
}
#fake-search {
	background: #eceff4;
	color: #687386;
}
#real-search, #details-tab, #accept-cookies {
	background: #2158d4;
	color: white;
}
#toast {
	color: #8a4b00;
	font-weight: 600;
	margin-top: 12px;
	min-height: 22px;
}
#results, #usage-panel {
	display: none;
}
#details-card {
	margin-top: 700px;
}
#cookie-modal {
	align-items: center;
	background: rgba(23, 32, 51, 0.72);
	bottom: 0;
	display: flex;
	justify-content: center;
	left: 0;
	position: fixed;
	right: 0;
	top: 0;
	z-index: 10;
}
#cookie-modal .dialog {
	background: white;
	border-radius: 16px;
	max-width: 460px;
	padding: 26px;
}
.muted {
	color: #5d687a;
}
</style>
</head>
<body>
<header>
	<h1>Acme Cloud Account Dashboard</h1>
	<p>Internal account analytics</p>
</header>
<main>
	<section class="card">
		<h2>Lookup</h2>
		<p class="muted">Find account details. The obvious Search button is intentionally a dead end for this benchmark.</p>
		<div class="row">
			<label for="account-query">Account query</label>
			<input id="account-query" name="account-query" placeholder="Type usage and press Enter">
			<button id="fake-search" type="button">Search</button>
			<button id="real-search" type="button" aria-label="Run account lookup">&gt;</button>
		</div>
		<div id="toast" role="status"></div>
	</section>

	<section id="results" class="card">
		<h2>Search results</h2>
		<p>Account: Team Browser Ops</p>
		<p>Plan: Growth</p>
		<p class="muted">Open the details tab below to see current usage.</p>
	</section>

	<section id="details-card" class="card">
		<h2>Details</h2>
		<p class="muted">The details tab is below the fold and only becomes ready shortly after lookup.</p>
		<button id="details-tab" type="button">Open usage details</button>
		<div id="usage-panel" aria-live="polite">
			<h3>Account Usage</h3>
			<p id="usage-number">Current account usage: 73,482 API calls</p>
		</div>
	</section>
</main>

<div id="cookie-modal" role="dialog" aria-modal="true" aria-label="Cookie notice">
	<div class="dialog">
		<h2>Cookie notice</h2>
		<p>This modal blocks the dashboard until it is dismissed.</p>
		<button id="accept-cookies" type="button">Accept cookies</button>
	</div>
</div>

<script>
(function() {
	const modal = document.getElementById('cookie-modal');
	const accept = document.getElementById('accept-cookies');
	const input = document.getElementById('account-query');
	const fake = document.getElementById('fake-search');
	const real = document.getElementById('real-search');
	const toast = document.getElementById('toast');
	const results = document.getElementById('results');
	const details = document.getElementById('details-tab');
	const usage = document.getElementById('usage-panel');
	let detailsReady = false;

	function showToast(message) {
		toast.textContent = message;
	}

	function runLookup() {
		if (modal.style.display !== 'none') {
			showToast('Dismiss the cookie modal before searching.');
			return;
		}
		if (input.value.trim().toLowerCase() !== 'usage') {
			showToast('Enter usage, then press Enter or the small > button.');
			return;
		}
		results.style.display = 'block';
		showToast('Lookup complete. Scroll down and wait for details to be ready.');
		detailsReady = false;
		setTimeout(function() {
			detailsReady = true;
			showToast('Details are ready. Open usage details.');
		}, 1400);
	}

	accept.addEventListener('click', function() {
		modal.style.display = 'none';
		input.focus();
		showToast('Cookie modal dismissed. Use the input and small > button.');
	});
	fake.addEventListener('click', function() {
		showToast('The visible Search button is disabled for this dashboard. Use Enter or the small > button.');
	});
	real.addEventListener('click', runLookup);
	input.addEventListener('keydown', function(event) {
		if (event.key === 'Enter') {
			runLookup();
		}
	});
	details.addEventListener('click', function() {
		if (!detailsReady) {
			showToast('Details are still loading. Wait briefly, scroll to the details card, then click again.');
			return;
		}
		usage.style.display = 'block';
		showToast('Usage details opened.');
	});
})();
</script>
</body>
</html>
"""


class _FailureReuseHandler(BaseHTTPRequestHandler):
	def do_GET(self) -> None:
		if self.path in ('/', '/dashboard'):
			self._send_html(_DASHBOARD_HTML)
			return
		self.send_response(404)
		self.end_headers()
		self.wfile.write(b'not found')

	def log_message(self, format: str, *args: object) -> None:
		return

	def _send_html(self, body: str) -> None:
		payload = body.encode('utf-8')
		self.send_response(200)
		self.send_header('Content-Type', 'text/html; charset=utf-8')
		self.send_header('Content-Length', str(len(payload)))
		self.end_headers()
		self.wfile.write(payload)


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
		sock.bind(('127.0.0.1', 0))
		return int(sock.getsockname()[1])


@contextlib.contextmanager
def serve_failure_reuse_site(port: int = 0) -> Iterator[str]:
	selected_port = port or _free_port()
	server = ThreadingHTTPServer(('127.0.0.1', selected_port), _FailureReuseHandler)
	thread = threading.Thread(target=server.serve_forever, name='failure-reuse-site', daemon=True)
	thread.start()
	try:
		yield f'http://127.0.0.1:{selected_port}'
	finally:
		server.shutdown()
		server.server_close()
		thread.join(timeout=2)

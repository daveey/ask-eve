// workers/eve-inbox/src/admin_ui.mjs
// Self-contained single-page Admin Console for /admin/ (protected by Cloudflare Access Google SSO).

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function renderAdminHtml({ adminEmail }) {
  const safeAdmin = escapeHtml(adminEmail || "Authenticated Admin");
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="robots" content="noindex, nofollow" />
  <title>Ask Eve — Private Inbox, @askevepodcast X Queue &amp; Reading List Console</title>
  <style>
    :root {
      --bg: #0E0D0B;
      --bg-elevated: #171512;
      --bg-card: #1E1B17;
      --ink: #F3EDE2;
      --ink-muted: #A89F91;
      --ink-faint: #6E665B;
      --accent: #D97736;
      --accent-hover: #E98A49;
      --eve: #7FB3A6;
      --pass-bg: rgba(46, 160, 67, 0.16);
      --pass-border: #2EA043;
      --pass-ink: #7EE787;
      --block-bg: rgba(248, 81, 73, 0.16);
      --block-border: #F85149;
      --block-ink: #FF9492;
      --x-bg: rgba(56, 189, 248, 0.14);
      --x-border: rgba(56, 189, 248, 0.45);
      --x-ink: #7DD3FC;
      --rule: #2A2520;
      --font-sans: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
      --font-serif: "Newsreader", Georgia, serif;
      --font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: var(--font-sans);
      line-height: 1.55;
      padding: 0 0 4rem;
    }
    header {
      border-bottom: 1px solid var(--rule);
      background: var(--bg-elevated);
      padding: 1.1rem 1.5rem;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.85rem;
    }
    .brand h1 {
      font-family: var(--font-serif);
      font-size: 1.35rem;
      margin: 0;
      font-weight: 600;
      letter-spacing: -0.01em;
    }
    .pill {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      padding: 0.22rem 0.6rem;
      border-radius: 999px;
      background: rgba(127, 179, 166, 0.14);
      color: var(--eve);
      border: 1px solid rgba(127, 179, 166, 0.35);
    }
    .pill-x {
      background: var(--x-bg);
      color: var(--x-ink);
      border: 1px solid var(--x-border);
    }
    .auth-status {
      font-family: var(--font-mono);
      font-size: 0.78rem;
      color: var(--ink-muted);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem;
    }
    main {
      max-width: 1180px;
      margin: 1.5rem auto;
      padding: 0 1.25rem;
      display: grid;
      gap: 2rem;
    }
    .section-head {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: 0.75rem;
      border-bottom: 1px solid var(--rule);
      padding-bottom: 0.55rem;
      margin-bottom: 1rem;
    }
    .section-head h2 {
      font-family: var(--font-serif);
      font-size: 1.3rem;
      margin: 0;
    }
    .section-head p {
      margin: 0;
      font-size: 0.86rem;
      color: var(--ink-muted);
    }
    .queue-grid {
      display: grid;
      gap: 1.25rem;
    }
    .card {
      background: var(--bg-card);
      border: 1px solid var(--rule);
      border-radius: 10px;
      padding: 1.25rem;
      display: grid;
      gap: 1rem;
    }
    .card-top {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: flex-start;
      gap: 0.75rem;
      border-bottom: 1px solid var(--rule);
      padding-bottom: 0.85rem;
    }
    .card-title {
      margin: 0 0 0.25rem;
      font-size: 1.1rem;
      font-weight: 600;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem;
    }
    .meta-line {
      font-family: var(--font-mono);
      font-size: 0.78rem;
      color: var(--ink-muted);
    }
    .badges {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
    }
    .badge {
      font-family: var(--font-mono);
      font-size: 0.72rem;
      font-weight: 600;
      padding: 0.22rem 0.58rem;
      border-radius: 6px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .badge-pass {
      background: var(--pass-bg);
      border: 1px solid var(--pass-border);
      color: var(--pass-ink);
    }
    .badge-block {
      background: var(--block-bg);
      border: 1px solid var(--block-border);
      color: var(--block-ink);
    }
    .badge-status {
      background: rgba(217, 119, 54, 0.14);
      border: 1px solid rgba(217, 119, 54, 0.4);
      color: var(--accent);
    }
    .badge-channel-email {
      background: rgba(127, 179, 166, 0.14);
      border: 1px solid rgba(127, 179, 166, 0.38);
      color: var(--eve);
    }
    .badge-channel-x {
      background: var(--x-bg);
      border: 1px solid var(--x-border);
      color: var(--x-ink);
    }
    .cols {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.15rem;
    }
    @media (max-width: 820px) {
      .cols { grid-template-columns: 1fr; }
    }
    .panel {
      background: var(--bg-elevated);
      border: 1px solid var(--rule);
      border-radius: 8px;
      padding: 0.95rem;
      display: grid;
      gap: 0.65rem;
      align-content: start;
    }
    .panel h4 {
      margin: 0;
      font-size: 0.82rem;
      font-family: var(--font-mono);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--eve);
    }
    label {
      display: block;
      font-size: 0.78rem;
      font-family: var(--font-mono);
      color: var(--ink-muted);
      margin-bottom: 0.2rem;
    }
    input[type="text"], textarea {
      width: 100%;
      background: #110F0D;
      color: var(--ink);
      border: 1px solid var(--rule);
      border-radius: 6px;
      padding: 0.55rem 0.65rem;
      font-family: var(--font-sans);
      font-size: 0.9rem;
    }
    textarea {
      min-height: 118px;
      resize: vertical;
      font-family: var(--font-mono);
      font-size: 0.82rem;
      line-height: 1.45;
    }
    .leak-alert {
      background: var(--block-bg);
      border: 1px solid var(--block-border);
      color: var(--block-ink);
      border-radius: 6px;
      padding: 0.65rem 0.8rem;
      font-size: 0.82rem;
      font-family: var(--font-mono);
    }
    .leak-alert ul {
      margin: 0.35rem 0 0;
      padding-left: 1.2rem;
    }
    .leak-ok {
      background: var(--pass-bg);
      border: 1px solid var(--pass-border);
      color: var(--pass-ink);
      border-radius: 6px;
      padding: 0.5rem 0.75rem;
      font-size: 0.79rem;
      font-family: var(--font-mono);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.55rem;
      align-items: center;
      margin-top: 0.25rem;
    }
    button, .btn-link {
      cursor: pointer;
      border-radius: 6px;
      border: 1px solid var(--rule);
      padding: 0.5rem 0.9rem;
      font-size: 0.84rem;
      font-weight: 600;
      background: #26221D;
      color: var(--ink);
      transition: background 0.15s ease, opacity 0.15s ease;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
    }
    button:hover:not(:disabled), .btn-link:hover {
      background: #332D26;
    }
    button.btn-primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #0E0D0B;
    }
    button.btn-primary:hover:not(:disabled) {
      background: var(--accent-hover);
    }
    button.btn-eve {
      background: rgba(127, 179, 166, 0.18);
      border-color: var(--eve);
      color: var(--eve);
    }
    button.btn-x, .btn-link.btn-x {
      background: var(--x-bg);
      border-color: var(--x-border);
      color: var(--x-ink);
    }
    button:disabled {
      opacity: 0.38;
      cursor: not-allowed;
    }
    .reading-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
      background: var(--bg-card);
      border: 1px solid var(--rule);
      border-radius: 8px;
      overflow: hidden;
    }
    .reading-table th, .reading-table td {
      text-align: left;
      padding: 0.65rem 0.85rem;
      border-bottom: 1px solid var(--rule);
    }
    .reading-table th {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      text-transform: uppercase;
      color: var(--ink-muted);
      background: var(--bg-elevated);
    }
    .char-counter {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      color: var(--ink-muted);
    }
    .char-counter.over {
      color: var(--block-ink);
      font-weight: 700;
    }
    .toast {
      position: fixed;
      bottom: 1.25rem;
      right: 1.25rem;
      background: var(--bg-elevated);
      border: 1px solid var(--eve);
      color: var(--ink);
      padding: 0.7rem 1rem;
      border-radius: 8px;
      font-size: 0.85rem;
      display: none;
      z-index: 100;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>Ask Eve — Private Inbox &amp; @askevepodcast Console</h1>
      <span class="pill">Zero-Auto-Send Gate Active</span>
      <span class="pill pill-x" id="x-conn-pill">@askevepodcast: Ready</span>
    </div>
    <div class="auth-status">
      <span>Cloudflare Access SSO: <strong>${safeAdmin}</strong></span>
      <a class="btn-link btn-x" href="/admin/x/oauth/start">Connect @askevepodcast (OAuth 2.0 PKCE)</a>
      <button type="button" class="btn-x" onclick="syncXMentions()">Sync @askevepodcast Mentions</button>
    </div>
  </header>

  <main>
    <section aria-labelledby="queue-heading">
      <div class="section-head">
        <div>
          <h2 id="queue-heading">Private Document &amp; @askevepodcast Mention Queue (<span id="queue-count">0</span>)</h2>
          <p>Emails to <code>eve@askevepodcast.com</code> / <code>reading@askevepodcast.com</code> and public X mentions of <code>@askevepodcast</code> land here. Nothing is sent or posted until you click Send/Post.</p>
        </div>
      </div>
      <div id="queue-list" class="queue-grid">
        <div class="card"><p class="meta-line">Loading private queue…</p></div>
      </div>
    </section>

    <section aria-labelledby="x-drafts-heading">
      <div class="section-head">
        <div>
          <h2 id="x-drafts-heading">X Drafts — <code>@askevepodcast</code> (<span id="x-drafts-count">0</span>)</h2>
          <p>Standalone posts &amp; episode announcements for <code>@askevepodcast</code>. Every post is gated by <code>leakCheck()</code> and the 280-character limit before <code>Approve &amp; Post to @askevepodcast</code> unlocks.</p>
        </div>
      </div>

      <div class="card" style="margin-bottom: 1.25rem;">
        <div class="panel">
          <h4>Compose New @askevepodcast Post</h4>
          <div>
            <label>Post Body (&le; 280 chars) <span class="char-counter" id="new-x-counter">0 / 280 chars</span></label>
            <textarea id="new-x-body" rows="3" placeholder="Draft a new @askevepodcast post or episode announcement…" oninput="updateCharCounter('new-x-body','new-x-counter')"></textarea>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;">
            <div>
              <label>Optional Reply-To Tweet ID</label>
              <input type="text" id="new-x-reply-id" placeholder="e.g. 184920192019201" />
            </div>
            <div>
              <label>Optional Episode Slug</label>
              <input type="text" id="new-x-ep-slug" placeholder="e.g. first-encounter" />
            </div>
          </div>
          <div class="actions">
            <button type="button" class="btn-x" onclick="createXDraft()">Run Leak Check &amp; Save X Draft</button>
          </div>
        </div>
      </div>

      <div id="x-drafts-list" class="queue-grid">
        <div class="card"><p class="meta-line">Loading @askevepodcast X drafts…</p></div>
      </div>
    </section>

    <section aria-labelledby="reading-heading">
      <div class="section-head">
        <div>
          <h2 id="reading-heading">Public Reading List (<code>/eve/#reading-list</code>) (<span id="reading-count">0</span>)</h2>
          <p>Only items explicitly promoted from the private queue (or added by David) appear on the public website.</p>
        </div>
      </div>
      <table class="reading-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Title</th>
            <th>Author / Source</th>
            <th>Via</th>
            <th>Eve's Public Notes</th>
          </tr>
        </thead>
        <tbody id="reading-tbody">
          <tr><td colspan="5" class="meta-line">Loading reading list…</td></tr>
        </tbody>
      </table>
    </section>
  </main>

  <div id="toast" class="toast" role="status" aria-live="polite"></div>

  <script>
    function esc(s) {
      return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function showToast(msg) {
      const el = document.getElementById("toast");
      el.textContent = msg;
      el.style.display = "block";
      setTimeout(() => { el.style.display = "none"; }, 4000);
    }

    function updateCharCounter(textareaId, counterId) {
      const ta = document.getElementById(textareaId);
      const counter = document.getElementById(counterId);
      if (!ta || !counter) return;
      const len = (ta.value || "").length;
      counter.textContent = len + " / 280 chars";
      counter.className = len > 280 ? "char-counter over" : "char-counter";
    }

    async function loadQueue() {
      const resp = await fetch("/admin/api/queue", { credentials: "same-origin" });
      if (!resp.ok) {
        document.getElementById("queue-list").innerHTML =
          '<div class="card"><div class="leak-alert">Failed to load queue (HTTP ' + resp.status + ').</div></div>';
        return;
      }
      const data = await resp.json();
      if (data.x_status) {
        const pill = document.getElementById("x-conn-pill");
        if (pill) {
          pill.textContent = data.x_status.connected
            ? "@askevepodcast: Connected (" + data.x_status.mode + ")"
            : "@askevepodcast: OAuth Ready";
        }
      }
      renderQueue(data.items || []);
      renderXDrafts(data.x_drafts || []);
      renderReadingList(data.reading_list || []);
    }

    function renderQueue(items) {
      document.getElementById("queue-count").textContent = String(items.length);
      const container = document.getElementById("queue-list");
      if (!items.length) {
        container.innerHTML = '<div class="card"><p class="meta-line">No inbound emails or @askevepodcast mentions in queue yet.</p></div>';
        return;
      }
      container.innerHTML = items.map((item) => {
        const channel = item.channel || "email";
        const isX = channel === "x_mention" || channel === "x_post";
        const findings = (() => {
          try { return JSON.parse(item.leak_check_findings || "[]"); } catch { return []; }
        })();
        const urls = (() => {
          try { return JSON.parse(item.extracted_urls || "[]"); } catch { return []; }
        })();
        const bodyLen = String(item.draft_body_text || "").length;
        const passed = Number(item.leak_check_passed) === 1 && item.draft_status !== "blocked_by_leak_check" && (!isX || bodyLen <= 280);
        const alreadySent = item.draft_status === "sent";
        const leakBadge = passed
          ? '<span class="badge badge-pass">Leak Check: PASS</span>'
          : '<span class="badge badge-block">BLOCKED BY LEAK CHECK (' + findings.length + ')</span>';
        const channelBadge = isX
          ? '<span class="badge badge-channel-x">X Mention @askevepodcast</span>'
          : '<span class="badge badge-channel-email">Inbound Email</span>';

        const leakBanner = passed
          ? '<div class="leak-ok">leakCheck() passed — SHA-256: ' + esc((item.leak_check_sha256 || "").slice(0, 16)) + '…' + (isX ? ' (' + bodyLen + '/280 chars)' : '') + '</div>'
          : '<div class="leak-alert"><strong>Outbound Send Blocked by Leak Check:</strong><ul>' +
            findings.map((f) => '<li><code>' + esc(f.rule) + '</code>: ' + esc(f.detail) + ' (<code>' + esc(f.match) + '</code>)</li>').join("") +
            '</ul></div>';

        const sendBtnLabel = alreadySent
          ? (isX ? "Posted to @askevepodcast ✓" : "Reply Sent ✓")
          : (isX ? "Approve &amp; Post to @askevepodcast" : "Send Reply Now");

        return \`
          <article class="card" data-item-id="\${esc(item.id)}">
            <div class="card-top">
              <div>
                <h3 class="card-title">\${channelBadge} <span>\${esc(item.doc_title || item.subject || "(Untitled)")}</span></h3>
                <div class="meta-line">
                  From: <strong>\${esc(item.from_name ? item.from_name + " <" + item.from_addr + ">" : item.from_addr)}</strong>
                  &middot; To: \${esc(item.to_addr)}
                  &middot; Received: \${esc(item.received_at)}
                  \${item.source_tweet_id ? ' &middot; Tweet ID: <code>' + esc(item.source_tweet_id) + '</code>' : ''}
                </div>
                \${urls.length ? '<div class="meta-line">Links: ' + urls.map((u) => '<a href="' + esc(u) + '" target="_blank" rel="noopener" style="color:var(--eve)">' + esc(u) + '</a>').join(", ") + '</div>' : ""}
              </div>
              <div class="badges">
                <span class="badge badge-status">\${esc(item.status)}</span>
                \${leakBadge}
              </div>
            </div>

            <div class="cols">
              <div class="panel">
                <h4>1. Inbound Content &amp; Eve's Triage</h4>
                <div>
                  <label>Document / Argument Title</label>
                  <input type="text" id="title-\${esc(item.id)}" value="\${esc(item.doc_title || item.subject || "")}" />
                </div>
                <div>
                  <label>Eve's Triage Summary</label>
                  <textarea id="summary-\${esc(item.id)}">\${esc(item.doc_summary || "")}</textarea>
                </div>
                <div>
                  <label>Eve's Initial Take &amp; On-Air Questions</label>
                  <textarea id="take-\${esc(item.id)}">\${esc(item.eve_initial_take || "")}</textarea>
                </div>
                <div class="actions">
                  <button type="button" onclick="saveTriage('\${esc(item.id)}', false)">Save Triage Notes</button>
                  <button type="button" class="btn-eve" onclick="promoteItem('\${esc(item.id)}')">Promote to Public Reading List</button>
                </div>
              </div>

              <div class="panel">
                <h4>2. \${isX ? "Leak-Checked Draft X Reply (@askevepodcast)" : "Leak-Checked Draft Email Reply"}</h4>
                \${leakBanner}
                <div>
                  <label>\${isX ? "Reply To Handle" : "To"}</label>
                  <input type="text" id="draft-to-\${esc(item.draft_id)}" value="\${esc(item.draft_to_addr || item.from_addr)}" />
                </div>
                <div>
                  <label>\${isX ? "Thread / Reference" : "Subject"}</label>
                  <input type="text" id="draft-subj-\${esc(item.draft_id)}" value="\${esc(item.draft_subject || "")}" />
                </div>
                <div>
                  <label>Draft Reply Body \${isX ? '<span class="char-counter ' + (bodyLen > 280 ? 'over' : '') + '" id="counter-' + esc(item.draft_id) + '">' + bodyLen + ' / 280 chars</span>' : '(Plain Text)'}</label>
                  <textarea id="draft-body-\${esc(item.draft_id)}" \${isX ? 'oninput="updateCharCounter(\\'draft-body-' + esc(item.draft_id) + '\\',\\'counter-' + esc(item.draft_id) + '\\')"' : ''}>\${esc(item.draft_body_text || "")}</textarea>
                </div>
                <div class="actions">
                  <button type="button" onclick="updateDraft('\${esc(item.draft_id)}')">Save &amp; Re-Run Leak Check</button>
                  <button
                    type="button"
                    class="\${isX ? 'btn-x' : 'btn-primary'}"
                    id="send-btn-\${esc(item.draft_id)}"
                    \${(!passed || alreadySent) ? "disabled" : ""}
                    onclick="sendDraft('\${esc(item.draft_id)}', '\${esc(item.leak_check_sha256 || "")}', \${isX})"
                  >
                    \${sendBtnLabel}
                  </button>
                </div>
              </div>
            </div>
          </article>
        \`;
      }).join("");
    }

    const RADAR_CLIP_MAP = {
      "xd_radar_sacks": "/assets/clips/clip-sacks-three-tests.mp4",
      "xd_radar_andreessen": "/assets/clips/clip-andreessen-deal-break.mp4",
      "xd_radar_thiel": "/assets/clips/clip-thiel-girard.mp4",
      "xd_radar_musk": "/assets/clips/clip-musk-usaid-chainsaw.mp4",
      "xd_radar_pelosi": "/assets/clips/clip-pelosi-succession.mp4"
    };

    function renderXDrafts(xDrafts) {
      document.getElementById("x-drafts-count").textContent = String(xDrafts.length);
      const container = document.getElementById("x-drafts-list");
      if (!xDrafts.length) {
        container.innerHTML = '<div class="card"><p class="meta-line">No @askevepodcast X drafts yet.</p></div>';
        return;
      }
      container.innerHTML = xDrafts.map((xd) => {
        const findings = (() => {
          try { return JSON.parse(xd.leak_check_findings_json || "[]"); } catch { return []; }
        })();
        const len = String(xd.body || "").length;
        const passed = Number(xd.leak_check_passed) === 1 && xd.status !== "blocked_leak" && len <= 280;
        const posted = xd.status === "approved_sent";
        const leakBadge = passed
          ? '<span class="badge badge-pass">Leak Check: PASS</span>'
          : '<span class="badge badge-block">BLOCKED (' + findings.length + ')</span>';
        return \`
          <article class="card" data-xdraft-id="\${esc(xd.id)}">
            <div class="card-top">
              <div>
                <h3 class="card-title">
                  <span class="badge badge-channel-x">@askevepodcast Post</span>
                  <span>\${esc(xd.episode_slug ? "Episode: " + xd.episode_slug : xd.id)}</span>
                </h3>
                <div class="meta-line">
                  Created: \${esc(xd.created_at)}
                  \${xd.reply_to_tweet_id ? ' &middot; In reply to: <a href="https://x.com/i/web/status/' + esc(xd.reply_to_tweet_id) + '" target="_blank" rel="noopener" style="color:var(--x-ink)"><code>' + esc(xd.reply_to_tweet_id) + '</code></a>' : ''}
                  \${RADAR_CLIP_MAP[xd.id] ? ' &middot; Audiogram Clip: <a href="' + esc(RADAR_CLIP_MAP[xd.id]) + '" target="_blank" rel="noopener" download style="color:var(--eve)">' + esc(RADAR_CLIP_MAP[xd.id]) + '</a>' : ''}
                  \${xd.tweet_id ? ' &middot; Posted Tweet ID: <code>' + esc(xd.tweet_id) + '</code>' : ''}
                </div>
              </div>
              <div class="badges">
                <span class="badge badge-status">\${esc(xd.status)}</span>
                \${leakBadge}
              </div>
            </div>
            <div class="panel">
              <div>
                <label>Post Text <span class="char-counter \${len > 280 ? 'over' : ''}" id="xcounter-\${esc(xd.id)}">\${len} / 280 chars</span></label>
                <textarea id="xbody-\${esc(xd.id)}" rows="3" oninput="updateCharCounter('xbody-\${esc(xd.id)}','xcounter-\${esc(xd.id)}')">\${esc(xd.body)}</textarea>
              </div>
              <div class="actions">
                <button type="button" onclick="updateXDraft('\${esc(xd.id)}')">Save &amp; Re-Run Leak Check</button>
                <button
                  type="button"
                  class="btn-primary"
                  \${(!passed || posted) ? "disabled" : ""}
                  onclick="postXDraft('\${esc(xd.id)}', '\${esc(xd.leak_check_sha256 || "")}')"
                >
                  \${posted ? "Posted to @askevepodcast ✓" : "Approve &amp; Post to @askevepodcast"}
                </button>
              </div>
            </div>
          </article>
        \`;
      }).join("");
    }

    function renderReadingList(list) {
      document.getElementById("reading-count").textContent = String(list.length);
      const tbody = document.getElementById("reading-tbody");
      if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="meta-line">No public reading list entries yet.</td></tr>';
        return;
      }
      tbody.innerHTML = list.map((r) => \`
        <tr>
          <td><span class="badge badge-status">\${esc(r.status)}</span></td>
          <td>\${r.url ? '<a href="' + esc(r.url) + '" target="_blank" rel="noopener" style="color:var(--ink)"><strong>' + esc(r.title) + '</strong></a>' : '<strong>' + esc(r.title) + '</strong>'}</td>
          <td>\${esc(r.author_or_source || "—")}</td>
          <td>\${esc(r.submitted_via || "David")}</td>
          <td>\${esc(r.eve_notes || "")}</td>
        </tr>
      \`).join("");
    }

    async function syncXMentions() {
      showToast("Syncing recent @askevepodcast mentions from X…");
      const resp = await fetch("/admin/api/x/sync-mentions", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast("Sync notice: " + (data.error || resp.status));
        return;
      }
      showToast("Synced " + (data.synced_count || 0) + " new @askevepodcast mention(s).");
      await loadQueue();
    }

    async function createXDraft() {
      const body = document.getElementById("new-x-body").value;
      const reply_to_tweet_id = document.getElementById("new-x-reply-id").value;
      const episode_slug = document.getElementById("new-x-ep-slug").value;
      const resp = await fetch("/admin/api/x/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body, reply_to_tweet_id, episode_slug })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast("Error: " + (data.error || resp.status));
        return;
      }
      document.getElementById("new-x-body").value = "";
      showToast(data.leak_check_passed ? "X draft saved & passed leakCheck()." : "X draft saved — BLOCKED by leakCheck().");
      await loadQueue();
    }

    async function updateXDraft(id) {
      const body = document.getElementById("xbody-" + id).value;
      const resp = await fetch("/admin/api/x/draft/" + encodeURIComponent(id) + "/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body })
      });
      const data = await resp.json().catch(() => ({}));
      showToast(data.leak_check_passed ? "Leak check PASSED." : "BLOCKED by leak check.");
      await loadQueue();
    }

    async function postXDraft(id, expectedSha) {
      if (!confirm("Post this leak-checked update publicly to @askevepodcast now?")) return;
      const resp = await fetch("/admin/api/x/draft/" + encodeURIComponent(id) + "/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, expected_sha256: expectedSha })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast("Post blocked: " + (data.error || resp.status));
      } else {
        showToast("Posted to @askevepodcast (tweet " + data.tweet_id + ").");
      }
      await loadQueue();
    }

    async function saveTriage(itemId, regenerate) {
      const doc_title = document.getElementById("title-" + itemId).value;
      const doc_summary = document.getElementById("summary-" + itemId).value;
      const eve_initial_take = document.getElementById("take-" + itemId).value;
      const resp = await fetch("/admin/api/item/" + encodeURIComponent(itemId) + "/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_title, doc_summary, eve_initial_take, regenerate })
      });
      if (resp.ok) {
        showToast("Saved triage notes.");
        await loadQueue();
      }
    }

    async function promoteItem(itemId) {
      const title = document.getElementById("title-" + itemId).value;
      const eve_notes = document.getElementById("take-" + itemId).value;
      const resp = await fetch("/admin/api/item/" + encodeURIComponent(itemId) + "/promote-to-reading-list", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, eve_notes, status: "reading" })
      });
      const data = await resp.json();
      if (!resp.ok) {
        showToast("Blocked by leak check: " + JSON.stringify(data.findings || data.error));
        return;
      }
      showToast("Promoted to public reading list!");
      await loadQueue();
    }

    async function updateDraft(draftId) {
      const to_addr = document.getElementById("draft-to-" + draftId).value;
      const subject = document.getElementById("draft-subj-" + draftId).value;
      const body_text = document.getElementById("draft-body-" + draftId).value;
      const resp = await fetch("/admin/api/draft/" + encodeURIComponent(draftId) + "/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to_addr, subject, body_text })
      });
      const data = await resp.json();
      showToast(data.leak_check_passed ? "Draft saved — Leak check PASSED." : "Draft saved — BLOCKED by leak check.");
      await loadQueue();
    }

    async function sendDraft(draftId, expectedSha, isX) {
      const promptMsg = isX
        ? "Post this leak-checked reply publicly to @askevepodcast on X now?"
        : "Send this leak-checked reply from eve@askevepodcast.com now?";
      if (!confirm(promptMsg)) return;
      const resp = await fetch("/admin/api/draft/" + encodeURIComponent(draftId) + "/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, expected_sha256: expectedSha })
      });
      const data = await resp.json();
      if (!resp.ok) {
        showToast("Send blocked: " + (data.error || resp.status));
      } else {
        showToast(isX ? "Reply posted to @askevepodcast!" : "Reply sent (" + data.message_id + ")");
      }
      await loadQueue();
    }

    loadQueue();
  </script>
</body>
</html>`;
}

// ── Auth helpers ────────────────────────────────────────────────────
function getToken()  { return localStorage.getItem('wa_auth_token')  || ''; }
function getRole()   { return localStorage.getItem('wa_auth_role')   || ''; }
function getEmail()  { return localStorage.getItem('wa_auth_email')  || ''; }
function getUserId() { return localStorage.getItem('wa_auth_userid') || ''; }

function clearAuth() {
  ['wa_auth_token','wa_auth_role','wa_auth_email','wa_auth_userid'].forEach(k => localStorage.removeItem(k));
}
function clearToken() { clearAuth(); }

function setAuthFromResponse(d) {
  localStorage.setItem('wa_auth_token',  d.token);
  localStorage.setItem('wa_auth_role',   d.role);
  localStorage.setItem('wa_auth_email',  d.email);
  if (d.userId) localStorage.setItem('wa_auth_userid', d.userId);
}

// ── Login overlay ────────────────────────────────────────────────────
function hideLogin() {
  document.getElementById('login-overlay').style.display = 'none';
  const app = document.getElementById('app');
  app.removeAttribute('hidden');
}

function showLogin(msg) {
  document.getElementById('login-overlay').style.display = 'flex';
  document.getElementById('app').hidden = true;
  document.getElementById('login-error').textContent = msg || '';
}

function switchAuthTab(tab) {
  const isLogin = tab === 'login';
  document.getElementById('panel-login').hidden    = !isLogin;
  document.getElementById('panel-register').hidden =  isLogin;
  document.getElementById('login-error').textContent = '';
  document.getElementById('tab-login').className    = isLogin ? 'active' : '';
  document.getElementById('tab-register').className = isLogin ? '' : 'active';
}

async function submitLogin() {
  const email    = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const r = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const d = await r.json();
  if (r.ok) {
    setAuthFromResponse(d);
    hideLogin();
    onAfterLogin(d);
    navigate('numbers');
    refreshAll();
  } else {
    document.getElementById('login-error').textContent = d.error || 'Sign in failed.';
  }
}

async function submitRegister() {
  const email    = document.getElementById('reg-email').value.trim();
  const password = document.getElementById('reg-password').value;
  const confirm  = document.getElementById('reg-password2').value;
  if (password !== confirm) {
    document.getElementById('login-error').textContent = 'Passwords do not match.'; return;
  }
  const r = await fetch('/api/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const d = await r.json();
  if (r.ok) {
    setAuthFromResponse(d);
    hideLogin();
    onAfterLogin(d);
    navigate('numbers');
    refreshAll();
  } else {
    document.getElementById('login-error').textContent = d.error || 'Registration failed.';
  }
}

async function logout() {
  await fetch('/api/auth/logout', { method: 'POST', headers: { 'X-Auth-Token': getToken() } });
  clearAuth();
  showLogin();
}

function onAfterLogin(user) {
  const email = user.email || getEmail();
  const role  = user.role  || getRole();

  // Sidebar user footer
  const emailEl  = document.getElementById('user-email');
  const avatarEl = document.getElementById('user-avatar');
  if (emailEl)  emailEl.textContent  = email;
  if (avatarEl) avatarEl.textContent = email ? email[0].toUpperCase() : 'U';

  // Admin nav item
  const adminNav = document.getElementById('nav-admin');
  if (adminNav) adminNav.hidden = (role !== 'admin');
}

// ── DOMContentLoaded ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  ['login-email','login-password'].forEach(id =>
    document.getElementById(id)?.addEventListener('keydown', e => { if (e.key === 'Enter') submitLogin(); }));
  ['reg-email','reg-password','reg-password2'].forEach(id =>
    document.getElementById(id)?.addEventListener('keydown', e => { if (e.key === 'Enter') submitRegister(); }));

  if (getToken()) {
    hideLogin();
    onAfterLogin({ email: getEmail(), role: getRole() });
    navigate('numbers');
    refreshAll();
  } else {
    showLogin();
  }
});

// ── Router ───────────────────────────────────────────────────────────
let _currentPage    = 'numbers';
let _selectedConvId = null;
let _inboxData      = [];

function navigate(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + page)?.classList.add('active');
  document.getElementById('nav-'  + page)?.classList.add('active');
  _currentPage = page;
  if (page === 'inbox')  renderInboxConversations();
  if (page === 'admin')  refreshUsers();
  if (page === 'numbers') refreshNumbers();
  if (page === 'campaign') { refreshDashboard(); refreshNumbers(); }
}

// ── Authenticated fetch helpers ──────────────────────────────────────
async function jget(url) {
  const r = await fetch(url, { headers: { 'X-Auth-Token': getToken() } });
  if (r.status === 401) { clearToken(); showLogin('Session expired. Please log in again.'); return {}; }
  return r.json();
}

async function jpost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Auth-Token': getToken() },
    body: JSON.stringify(body || {}),
  });
  if (r.status === 401) { clearToken(); showLogin('Session expired. Please log in again.'); return {}; }
  return r.json();
}

// ── Change password (self) ───────────────────────────────────────────
async function submitChangePassword() {
  const currentPw = document.getElementById('cpw-current').value;
  const newPw     = document.getElementById('cpw-new').value;
  const confirmPw = document.getElementById('cpw-confirm').value;
  const errEl     = document.getElementById('cpw-error');
  errEl.textContent = '';
  if (newPw !== confirmPw) { errEl.textContent = 'New passwords do not match.'; return; }
  if (newPw.length < 8)    { errEl.textContent = 'Password must be at least 8 characters.'; return; }

  const verifyR = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: getEmail(), password: currentPw }),
  });
  if (!verifyR.ok) { errEl.textContent = 'Current password is incorrect.'; return; }

  const userId = getUserId() || (await jget('/api/auth/me')).userId;
  const r = await jpost(`/api/users/${userId}/password`, { password: newPw });
  if (r.ok) {
    document.getElementById('change-pw-modal').style.display = 'none';
    ['cpw-current','cpw-new','cpw-confirm'].forEach(id => document.getElementById(id).value = '');
    alert('Password changed successfully.');
  } else {
    errEl.textContent = r.error || 'Failed to change password.';
  }
}

// ── User management (admin) ──────────────────────────────────────────
async function refreshUsers() {
  const users = await jget('/api/users');
  if (!Array.isArray(users)) return;
  const myEmail = getEmail();

  const sel = document.getElementById('reset-user-select');
  if (sel) {
    sel.innerHTML = '<option value="">— select user —</option>' +
      users.map(u => `<option value="${u.userId}">${u.email} (${u.role})</option>`).join('');
  }

  const rows = users.map(u => {
    const isMe = u.email === myEmail;
    const roleCell = isMe
      ? `<td><em>${u.role}</em></td>`
      : `<td><select onchange="changeUserRole('${u.userId}', this.value)" style="padding:.2rem">
           <option value="user"  ${u.role==='user'  ? 'selected':''}>User</option>
           <option value="admin" ${u.role==='admin' ? 'selected':''}>Admin</option>
         </select></td>`;
    const actionCell = isMe
      ? `<td><em>you</em></td>`
      : `<td><button onclick="deleteUser('${u.userId}','${u.email}')" style="color:#b00;padding:.2rem .6rem">Remove</button></td>`;
    return `<tr>
      <td>${u.email}</td>
      ${roleCell}
      <td>${new Date(u.createdAt).toLocaleDateString()}</td>
      ${actionCell}
    </tr>`;
  }).join('');

  document.getElementById('users-list').innerHTML = `
    <table>
      <tr><th>Email</th><th>Role</th><th>Added</th><th></th></tr>
      ${rows}
    </table>`;
}

async function createUser() {
  const email    = document.getElementById('new-user-email').value.trim();
  const password = document.getElementById('new-user-password').value;
  const role     = document.getElementById('new-user-role').value;
  const r = await jpost('/api/users', { email, password, role });
  const el = document.getElementById('user-result');
  if (r.error) { el.innerHTML = `<p style="color:#b00">${r.error}</p>`; }
  else {
    el.innerHTML = `<p class="hint">&#10003; ${r.email} added as ${r.role}. Share their temporary password: <strong>${password}</strong></p>`;
    document.getElementById('new-user-email').value = '';
    document.getElementById('new-user-password').value = '';
    refreshUsers();
  }
}

async function deleteUser(userId, email) {
  if (!confirm(`Remove ${email}? They will be signed out immediately and lose access.`)) return;
  const r = await fetch(`/api/users/${userId}`, { method: 'DELETE', headers: { 'X-Auth-Token': getToken() } });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  refreshUsers();
}

async function changeUserRole(userId, newRole) {
  const r = await jpost(`/api/users/${userId}/role`, { role: newRole });
  if (r.error) { alert(r.error); refreshUsers(); }
}

async function adminResetPassword() {
  const userId = document.getElementById('reset-user-select').value;
  const newPw  = document.getElementById('reset-user-password').value;
  const el     = document.getElementById('reset-result');
  if (!userId)          { el.innerHTML = '<p style="color:#b00">Select a user first.</p>'; return; }
  if (newPw.length < 8) { el.innerHTML = '<p style="color:#b00">Password must be at least 8 characters.</p>'; return; }
  const r = await jpost(`/api/users/${userId}/password`, { password: newPw });
  if (r.ok) {
    el.innerHTML = `<p class="hint">&#10003; Password reset. Share the new password: <strong>${newPw}</strong></p>`;
    document.getElementById('reset-user-password').value = '';
  } else {
    el.innerHTML = `<p style="color:#b00">${r.error || 'Reset failed.'}</p>`;
  }
}

// ── Settings ─────────────────────────────────────────────────────────
async function refreshSettings() {
  const s = await jget('/api/settings');
  const minEl = document.getElementById('delay-min');
  const maxEl = document.getElementById('delay-max');
  const crmEl = document.getElementById('crm-webhook-url');
  if (minEl) minEl.value = s.delayMinSeconds;
  if (maxEl) maxEl.value = s.delayMaxSeconds;
  if (crmEl) crmEl.value = s.crmWebhookUrl || '';
}

async function saveSettings() {
  const delayMinSeconds = parseInt(document.getElementById('delay-min').value, 10);
  const delayMaxSeconds = parseInt(document.getElementById('delay-max').value, 10);
  const crmWebhookUrl = (document.getElementById('crm-webhook-url').value || '').trim();
  await jpost('/api/settings', { delayMinSeconds, delayMaxSeconds, crmWebhookUrl });
  document.getElementById('settings-result').innerHTML = '<p class="hint">Settings saved.</p>';
  refreshSettings();
}

// ── Templates ─────────────────────────────────────────────────────────
function insertVar(textareaId, text) {
  const el = document.getElementById(textareaId);
  if (!el) return;
  const start = el.selectionStart || 0;
  const end   = el.selectionEnd   || 0;
  el.value = el.value.slice(0, start) + text + el.value.slice(end);
  el.focus();
  el.selectionStart = el.selectionEnd = start + text.length;
}

async function refreshTemplates() {
  const t = await jget('/api/templates');
  const wrap = document.getElementById('template-editor');
  if (!wrap) return;
  if (!t || !t.initial) return;
  wrap.innerHTML = t.initial.map(tpl => `
    <div class="template-row">
      <div class="template-row-header">
        <label><strong>${tpl.label}</strong> <span class="tpl-id">${tpl.id}</span>${tpl.isOverridden ? ' <span class="badge">edited</span>' : ''}</label>
      </div>
      <div class="var-buttons">
        <button type="button" onclick="insertVar('tpl-${tpl.id}', '{{Owner_Name}}')">+ Owner Name</button>
        <button type="button" onclick="insertVar('tpl-${tpl.id}', '{{Unit_Number}}')">+ Unit Number</button>
        <button type="button" onclick="insertVar('tpl-${tpl.id}', '{{Agent_Name}}')">+ Agent Name</button>
        <button type="button" onclick="insertVar('tpl-${tpl.id}', '{{Company_Name}}')">+ Company Name</button>
      </div>
      <textarea id="tpl-${tpl.id}" rows="3" style="width:100%;box-sizing:border-box">${tpl.text}</textarea>

      <div class="tpl-image-row">
        <span class="tpl-image-label">📎 Attach media (optional — sent as the message image/document with text as caption)</span>
        <div class="tpl-image-controls">
          ${tpl.hasImage ? `
            ${tpl.mediaType === 'pdf'
              ? `<div class="tpl-pdf-preview">📄 PDF attached</div>`
              : `<img src="${tpl.imageUrl}?v=${Date.now()}" class="tpl-image-preview" alt="Template image" />`
            }
            <button type="button" class="btn-ghost btn-sm" onclick="removeTemplateImage('${tpl.id}')">✕ Remove</button>
          ` : `
            <label class="btn-ghost btn-sm tpl-upload-label">
              ⬆ Upload image or PDF
              <input type="file" accept="image/*,.pdf,.bmp,.heic,.heif,.avif,.tiff,.tif" style="display:none" onchange="uploadTemplateImage('${tpl.id}', this)">
            </label>
          `}
        </div>
      </div>
    </div>
  `).join('');
}

async function uploadTemplateImage(templateId, input) {
  const file = input.files[0];
  if (!file) return;

  // Client-side size guard (WhatsApp limit is 16 MB for media)
  if (file.size > 16 * 1024 * 1024) {
    alert('File is too large. WhatsApp supports up to 16 MB for media files.');
    input.value = '';
    return;
  }

  const fd = new FormData();
  fd.append('image', file);
  const r = await fetch(`/api/templates/${templateId}/image`, {
    method: 'POST', body: fd, headers: { 'X-Auth-Token': getToken() }
  });
  const d = await r.json();
  if (d.error) {
    alert(`Could not attach file:\n\n${d.error}`);
    input.value = '';
    return;
  }
  refreshTemplates();
}

async function removeTemplateImage(templateId) {
  if (!confirm('Remove the image from this template?')) return;
  await fetch(`/api/templates/${templateId}/image`, {
    method: 'DELETE', headers: { 'X-Auth-Token': getToken() }
  });
  refreshTemplates();
}

async function saveTemplates() {
  const ids = ['A', 'B', 'C', 'D', 'E'];
  const overrides = {};
  ids.forEach(id => {
    const el = document.getElementById('tpl-' + id);
    if (el) overrides[id] = el.value;
  });
  await jpost('/api/templates/initial', { overrides });
  document.getElementById('templates-result').innerHTML = '<p class="hint">Templates saved.</p>';
  refreshTemplates();
}

// ── Numbers ───────────────────────────────────────────────────────────
function showAddNumber() {
  const f = document.getElementById('add-number-form');
  if (f) f.style.display = '';
}
function hideAddNumber() {
  const f = document.getElementById('add-number-form');
  if (f) f.style.display = 'none';
}

function statusText(n) {
  if (n.status === 'connected')        return 'Connected';
  if (n.status === 'awaiting_qr_scan') return 'Scan QR';
  if (n.status === 'error')            return 'Error';
  return n.status || 'Unknown';
}

async function addNumber() {
  const label = document.getElementById('new-number-label').value.trim() || undefined;
  await jpost('/api/numbers', { label });
  document.getElementById('new-number-label').value = '';
  hideAddNumber();
  refreshNumbers();
}

async function refreshNumbers() {
  const numbers = await jget('/api/numbers');
  if (!Array.isArray(numbers)) return;

  const listEl  = document.getElementById('numbers-list');
  const checkEl = null; // number checkboxes removed — assignment is automatic

  if (!numbers.length) {
    if (listEl) listEl.innerHTML = `
      <div class="card" style="text-align:center;padding:40px">
        <div style="font-size:36px;margin-bottom:10px">📱</div>
        <p style="font-weight:500;color:var(--navy);margin-bottom:6px">No numbers yet</p>
        <p class="hint">Add a number and scan its QR code with WhatsApp &gt; Linked Devices &gt; Link a Device.</p>
      </div>`;
  } else {
    if (listEl) listEl.innerHTML = numbers.map(n => `
      <div class="number-card status-${n.status}">
        <div class="number-status-dot"></div>
        <div class="number-info">
          <div class="number-label">${n.label}</div>
          <div class="number-detail">
            Sent today: ${n.sentToday}/${n.dailyCap}
            ${n.pauseReason ? ' · ' + n.pauseReason : ''}
            · Reply rate: ${replyRateLabel(n.replyRate)}
          </div>
        </div>
        <span class="number-status-pill">${statusText(n)}${n.paused ? ' · Paused' : ''}</span>
        ${n.qr ? `<div class="qr-wrap"><img src="${n.qr}" alt="QR code"/><p class="hint" style="margin:6px 0 0;text-align:center;font-size:11px">Scan with WhatsApp &rarr; Linked Devices</p></div>` : ''}
        <div class="number-actions">
          ${n.paused
            ? `<button onclick="resumeNumber(${n.id})">Resume</button>`
            : `<button onclick="pauseNumber(${n.id})">Pause</button>`}
          <button class="danger-text" onclick="deleteNumber(${n.id}, '${escHtml(n.label)}')">Delete</button>
        </div>
      </div>`).join('');
  }

  if (checkEl) {
    checkEl.innerHTML = numbers.map(n =>
      `<label><input type="checkbox" class="num-check" value="${n.id}"> ${n.label}</label>`
    ).join('') || '<span class="hint">Add a number first.</span>';
  }

  // Connected-numbers badge in nav (optional; update if element exists)
  const connected = numbers.filter(n => n.status === 'connected' && !n.paused).length;
  const navBadge = document.getElementById('badge-numbers');
  if (navBadge) { navBadge.textContent = connected; navBadge.hidden = connected === 0; }
}

function replyRateLabel(r) {
  if (!r || r.status === 'insufficient_data') return `not enough data (${r ? r.total : 0} contacts)`;
  const pct   = (r.replyRate * 100).toFixed(1) + '%';
  const words = { healthy: 'healthy', warning: 'below target', critical: 'critical' };
  return `${pct} (${r.replied}/${r.total}) — ${words[r.status] || r.status}`;
}

async function pauseNumber(id)  { await jpost(`/api/numbers/${id}/pause`,  {}); refreshNumbers(); }
async function resumeNumber(id) { await jpost(`/api/numbers/${id}/resume`, {}); refreshNumbers(); }

async function deleteNumber(id, label) {
  if (!confirm(`Delete number "${label}"? This disconnects its session. Contacts already queued will still be sent by whichever number is next in rotation.`)) return;
  await fetch(`/api/numbers/${id}`, { method: 'DELETE', headers: { 'X-Auth-Token': getToken() } });
  refreshNumbers();
}

// ── Contacts import ───────────────────────────────────────────────────
function handleDrop(e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    document.getElementById('import-file').files = dt.files;
    importFile();
  }
}

async function importFile() {
  const fileInput    = document.getElementById('import-file');
  const campaignName = document.getElementById('campaign-name').value;
  if (!fileInput.files[0]) return alert('Choose a file first');
  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  if (campaignName) fd.append('campaignName', campaignName);
  const r      = await fetch('/api/contacts/import', { method: 'POST', body: fd, headers: { 'X-Auth-Token': getToken() } });
  const result = await r.json();
  document.getElementById('import-result').innerHTML = result.error
    ? `<p style="color:red">${result.error}</p>`
    : `<p class="hint">Campaign "${result.campaignName}": ${result.added} added, ${result.mergedAsMultiUnit} merged as multi-unit, ${result.invalid} invalid numbers, ${result.duplicateSkipped} duplicates skipped, ${result.suppressedOptedOut} suppressed (previously opted out).</p>`;
  refreshAll();
}

// ── Campaign start/stop ───────────────────────────────────────────────
async function startCampaign() {
  // Pre-flight: check that all template media files are readable
  const validation = await jget('/api/templates/validate-images');
  if (validation.issues && validation.issues.length > 0) {
    const names = { A: 'Direct & professional', B: 'Question-first', C: 'Market-context', D: 'Short & informal', E: 'Courtesy-led' };
    const lines = validation.issues.map(i => `• Template ${i.templateId} (${names[i.templateId] || i.templateId}): ${i.reason}`).join('\n');
    document.getElementById('media-issue-body').textContent =
      `The following template media files have problems and cannot be sent:\n\n${lines}\n\nYou can start without images, or cancel to fix the files in Settings first.`;
    document.getElementById('media-issue-modal').style.display = 'flex';
    return;
  }
  await _doStartCampaign();
}

async function startCampaignNoImages() {
  document.getElementById('media-issue-modal').style.display = 'none';
  await _doStartCampaign();
}

async function _doStartCampaign() {
  const campaignName = (document.getElementById('launch-campaign-name')?.value || '').trim() || undefined;
  const result = await jpost('/api/campaigns/start', campaignName ? { campaignName } : {});
  const el = document.getElementById('assign-result');
  if (el) {
    const numbersMsg = result.started?.length
      ? `Rotating across ${result.started.length} connected number(s).`
      : 'No connected numbers found — connect a number first.';
    const queuedMsg = result.queued > 0 ? ` ${result.queued} contact(s) queued.` : ' No new contacts to queue.';
    el.innerHTML = `<p class="hint">${numbersMsg}${queuedMsg}</p>`;
  }
}

async function stopCampaign() {
  await jpost('/api/campaigns/stop', {});
  const el = document.getElementById('assign-result');
  if (el) el.innerHTML = '<p class="hint">All sending stopped.</p>';
}

// ── Dashboard ─────────────────────────────────────────────────────────
async function refreshDashboard() {
  const d = await jget('/api/dashboard');
  if (!d || d.error) return;

  const stats = [
    ['Total Contacts',     d.totalContacts],
    ['Valid Numbers',      d.validNumbers],
    ['Invalid Numbers',    d.invalidNumbers],
    ['Multi-Unit Owners',  d.multiUnitOwners],
    ['Messages Sent',      d.messagesSent],
    ['Messages Failed',    d.messagesFailed],
    ['Replies Received',   d.repliesReceived],
    ['Interested Selling', d.interestedSelling],
    ['Interested Renting', d.interestedRenting],
    ['Not Interested',     d.notInterested],
    ['Opted Out',          d.optedOut],
  ];

  const cc      = d.currentCampaign;
  const ccStats = cc ? [
    ['Total Contacts',     cc.totalContacts],
    ['Valid Numbers',      cc.validNumbers],
    ['Invalid Numbers',    cc.invalidNumbers],
    ['Messages Sent',      cc.messagesSent],
    ['Messages Failed',    cc.messagesFailed],
    ['Replies Received',   cc.repliesReceived],
    ['Interested Selling', cc.interestedSelling],
    ['Interested Renting', cc.interestedRenting],
    ['Not Interested',     cc.notInterested],
    ['Opted Out',          cc.optedOut],
  ] : [];

  const ccHtml = cc
    ? `<h3 style="font-size:13px;font-weight:600;color:var(--navy);margin-bottom:10px">Current Campaign: ${cc.name}</h3>
       <div class="stats-grid">${ccStats.map(([label, num]) => `<div class="stat-card"><div class="stat-value">${num ?? 0}</div><div class="stat-label">${label}</div></div>`).join('')}</div>`
    : `<h3 style="font-size:13px;font-weight:600;color:var(--navy);margin-bottom:8px">Current Campaign</h3><p class="hint">No campaign launched yet.</p>`;

  document.getElementById('dashboard').innerHTML = `
    ${ccHtml}
    <h3 style="font-size:13px;font-weight:600;color:var(--navy);margin:18px 0 10px">Overall (All Campaigns)</h3>
    <div class="stats-grid">${stats.map(([label, num]) => `<div class="stat-card"><div class="stat-value">${num ?? 0}</div><div class="stat-label">${label}</div></div>`).join('')}</div>
    <h3 style="font-size:13px;font-weight:600;color:var(--navy);margin:18px 0 10px">By Number</h3>
    <table>
      <tr><th>Number</th><th>Status</th><th>Sends</th><th>Delivered</th><th>Opt-outs</th><th>Today's cap</th><th>Reply rate</th></tr>
      ${(d.byNumber || []).map(n => `<tr>
        <td>${n.label}</td>
        <td>${n.status}${n.paused ? ' (paused)' : ''}</td>
        <td>${n.sends}</td>
        <td>${n.delivered}</td>
        <td>${n.optOuts}</td>
        <td>${n.sentToday}/${n.dailyCap}</td>
        <td>${replyRateLabel(n.replyRate)}</td>
      </tr>`).join('')}
    </table>`;
}

// ── Contacts ──────────────────────────────────────────────────────────
async function clearInvalidContacts() {
  const contacts = await jget('/api/contacts');
  const invalid  = contacts.filter(c => c.invalidNumber || !c.phoneE164).length;
  if (invalid === 0) { alert('No invalid contacts to remove.'); return; }
  if (!confirm(`Remove ${invalid} invalid/empty-phone contact(s)? This cannot be undone.`)) return;
  const r = await fetch('/api/contacts/clear-invalid', { method: 'POST', headers: { 'X-Auth-Token': getToken() } });
  const d = await r.json();
  alert(`Removed ${d.removed} contact(s).`);
  refreshContacts();
  refreshDashboard();
}

async function clearAllContacts() {
  const contacts = await jget('/api/contacts');
  const total    = contacts.length;
  if (total === 0) { alert('No contacts to remove.'); return; }
  if (!confirm(`Remove ALL ${total} contacts? This cannot be undone.`)) return;
  if (!confirm('Are you sure? This will delete every contact from the database.')) return;
  const r = await fetch('/api/contacts/clear-all', { method: 'POST', headers: { 'X-Auth-Token': getToken() } });
  const d = await r.json();
  alert(`Removed ${d.removed} contact(s).`);
  refreshContacts();
  refreshDashboard();
}

async function refreshContacts() {
  const contacts     = await jget('/api/contacts');
  if (!Array.isArray(contacts)) return;
  const invalidCount = contacts.filter(c => c.invalidNumber || !c.phoneE164).length;

  // Update count badge
  const countEl = document.getElementById('contacts-count');
  if (countEl) countEl.textContent = contacts.length;

  const rows = contacts.slice(0, 200).map(c => {
    const pillClass = c.optedOut          ? 'pill-optout'
                    : c.messageStatus === 'sent'   ? 'pill-sent'
                    : c.messageStatus === 'failed' ? 'pill-failed'
                    : 'pill-pending';
    const statusLabel = c.invalidNumber ? 'Invalid' : c.optedOut ? 'Opted out' : (c.messageStatus || '—');
    const isFailed    = c.messageStatus === 'failed';
    const errorTip    = isFailed && c.lastSendError ? c.lastSendError.replace(/"/g, '&quot;') : '';
    const statusCell  = isFailed
      ? `<td><span class="status-pill pill-failed" title="${errorTip}" style="cursor:help">&#9888; failed</span></td>`
      : `<td><span class="status-pill ${pillClass}">${statusLabel}</span></td>`;
    const crmIntent = c.crmLeadIntent;
    const crmCell   = crmIntent
      ? `<td style="color:var(--success);font-weight:600" title="Sent to CRM at ${c.crmLeadSentAt || ''}">&#10003; ${crmIntent}</td>`
      : '<td>—</td>';
    return `<tr>
      <td>${c.contactId}</td>
      <td>${c.ownerName}</td>
      <td>${c.phoneE164 || c.phoneRaw}</td>
      <td>${c.unitNumber}</td>
      ${statusCell}
      <td>${c.salesAgentId || '—'}</td>
      ${crmCell}
      <td>${c.assignedNumberId || '—'}</td>
    </tr>`;
  }).join('');

  document.getElementById('contacts-table').innerHTML = `
    <p class="hint">Showing up to 200 of ${contacts.length} contacts.
      ${invalidCount > 0 ? `<strong>${invalidCount} invalid</strong> — ` : ''}
      <button onclick="clearInvalidContacts()" style="margin-right:8px">Remove Invalid</button>
      <button onclick="clearAllContacts()" style="color:#b00">Clear All</button>
    </p>
    <table>
      <tr><th>ID</th><th>Owner</th><th>Phone</th><th>Unit</th><th>Status</th><th>Agent ID</th><th>CRM Lead</th><th>Number</th></tr>
      ${rows}
    </table>`;
}

// ── Inbox ─────────────────────────────────────────────────────────────
async function refreshInbox() {
  const threads = await jget('/api/inbox');
  if (!threads || threads.error) return;
  _inboxData = Array.isArray(threads) ? threads : [];

  // Update unread badge — total unread messages across all threads
  const totalUnread = _inboxData.reduce((sum, t) => sum + (t.unreadCount || 0), 0);
  const badge = document.getElementById('badge-inbox');
  if (badge) { badge.textContent = totalUnread; badge.hidden = totalUnread === 0; }

  // Only render conversation list if on the inbox page
  if (_currentPage === 'inbox') renderInboxConversations();
  if (_currentPage === 'inbox' && _selectedConvId) renderInboxThread(_selectedConvId);
}

function renderInboxConversations() {
  const el = document.getElementById('inbox-conversations');
  if (!el) return;

  if (!_inboxData.length) {
    el.innerHTML = `<div class="inbox-empty-hint">
      <p>No conversations yet.</p>
      <p class="hint">When contacts reply to your outreach, their messages will appear here.</p>
    </div>`;
    return;
  }

  el.innerHTML = _inboxData.map(t => {
    const initials = (t.contactName || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
    const lastMsg  = t.messages?.at(-1);
    const preview  = lastMsg ? (lastMsg.direction === 'outbound' ? '↗ ' : '') + lastMsg.body.slice(0, 50) : '';
    return `<div class="conv-item${t.hasUnreplied ? ' unread' : ''}${t.contactId === _selectedConvId ? ' active' : ''}"
              onclick="openConversation('${t.contactId}')">
      <div class="conv-avatar">${initials}</div>
      <div class="conv-body">
        <div class="conv-name">${t.contactName || 'Unknown'}</div>
        <div class="conv-preview">${escHtml(preview)}</div>
      </div>
      <div class="conv-meta">
        <div class="conv-time">${fmtTime(t.lastMessageAt)}</div>
        ${t.unreadCount > 0 ? `<div class="conv-unread-dot">${t.unreadCount}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

function openConversation(contactId) {
  _selectedConvId = contactId;
  renderInboxConversations(); // re-render to update active highlight
  renderInboxThread(contactId);
}

function renderInboxThread(contactId) {
  const thread = document.getElementById('inbox-thread');
  if (!thread) return;
  const conv = _inboxData.find(t => t.contactId === contactId);
  if (!conv) return;
  const msgs    = conv.messages || [];
  const inputId = 'reply-' + contactId;

  thread.innerHTML = `
    <div class="thread-header">
      <div class="thread-avatar">${(conv.contactName || '?')[0].toUpperCase()}</div>
      <div>
        <div class="thread-name">${conv.contactName || 'Unknown'}</div>
        <div class="thread-detail">${conv.phone || ''} · Unit ${conv.unitNumber || '—'} · Agent: ${conv.salesAgentId || '—'}</div>
      </div>
    </div>
    <div class="thread-messages" id="thread-msgs">
      ${msgs.map((m, i) => {
        const isOut = m.direction === 'outbound';
        let tick = '';
        if (isOut) {
          if (m.status === 'failed') {
            tick = `<span class="msg-tick tick-failed" title="Send failed"><svg viewBox="0 0 16 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="2" x2="14" y2="10"/><line x1="14" y1="2" x2="2" y2="10"/></svg></span>`;
          } else {
            // Double blue ticks if any inbound message came after this one
            const wasRead = msgs.slice(i + 1).some(n => n.direction === 'inbound');
            if (wasRead) {
              tick = `<span class="msg-tick tick-read" title="Read"><svg viewBox="0 0 18 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1,6 5,10 12,1"/><polyline points="6,6 10,10 17,1"/></svg></span>`;
            } else {
              tick = `<span class="msg-tick tick-sent" title="Sent"><svg viewBox="0 0 18 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1,6 5,10 12,1"/><polyline points="6,6 10,10 17,1"/></svg></span>`;
            }
          }
        }
        return `
        <div class="msg msg-${isOut ? 'out' : 'in'}">
          <div class="msg-bubble">${escHtml(m.body)}</div>
          <div class="msg-time">${fmtTime(m.createdAt)}${m.manualReply ? ' · agent' : ''}${tick}</div>
        </div>`;
      }).join('')}
    </div>
    <div class="thread-reply-bar">
      <textarea id="${inputId}" placeholder="Type a reply…" rows="2"
        onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendReply('${contactId}',${conv.numberId || 'null'},'${inputId}')}"></textarea>
      <button class="primary" onclick="sendReply('${contactId}',${conv.numberId || 'null'},'${inputId}')">Send</button>
    </div>`;

  document.getElementById('thread-msgs')?.scrollTo(0, 999999);
}

function escHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function sendReply(contactId, numberId, inputId) {
  const input = document.getElementById(inputId);
  const text  = (input?.value || '').trim();
  if (!text) return;
  if (input) input.disabled = true;
  const r = await jpost('/api/inbox/reply', { contactId, numberId, text });
  if (r.ok) {
    if (input) input.value = '';
    await refreshInbox();
    renderInboxThread(_selectedConvId);
  } else {
    alert('Failed to send: ' + (r.error || 'unknown error'));
  }
  if (input) input.disabled = false;
}

// ── Time formatter ────────────────────────────────────────────────────
function fmtTime(iso) {
  if (!iso) return '';
  const d    = new Date(iso);
  const now  = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' }) + ' ' +
         d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── Refresh all ───────────────────────────────────────────────────────
function refreshAll() {
  refreshNumbers();
  refreshDashboard();
  refreshContacts();
  refreshInbox();
  refreshSettings();
  refreshTemplates();
}

setInterval(refreshAll, 8000);

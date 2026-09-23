async function jget(url) { const r = await fetch(url); return r.json(); }
async function jpost(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  return r.json();
}

async function addNumber() {
  const label = document.getElementById('new-number-label').value || undefined;
  await jpost('/api/numbers', { label });
  document.getElementById('new-number-label').value = '';
  refreshNumbers();
}

async function refreshNumbers() {
  const numbers = await jget('/api/numbers');
  const listEl = document.getElementById('numbers-list');
  const checkEl = document.getElementById('number-checkboxes');
  if (numbers.length === 0) {
    listEl.innerHTML = '<p class="hint">No numbers yet. Add one below, then scan the QR code with that WhatsApp number\'s phone (WhatsApp &gt; Linked Devices &gt; Link a Device).</p>';
  } else {
    listEl.innerHTML = numbers.map(n => `
      <div class="number-row">
        <div style="flex:1">
          <strong>${n.label}</strong> <span class="badge ${n.status}">${n.status}${n.paused ? ' / paused' : ''}</span><br/>
          <span class="hint">Sent today: ${n.sentToday} / ${n.dailyCap} (warm-up cap) ${n.pauseReason ? ' — ' + n.pauseReason : ''}</span><br/>
          <span class="hint">Reply rate: ${replyRateLabel(n.replyRate)}</span>
        </div>
        ${n.qr ? `<div class="qr-box"><img src="${n.qr}" /></div>` : ''}
        <div>
          ${n.paused ? `<button onclick="resumeNumber(${n.id})">Resume</button>` : `<button onclick="pauseNumber(${n.id})">Pause</button>`}
        </div>
      </div>`).join('');
  }
  checkEl.innerHTML = numbers.map(n => `<label style="margin-right:12px"><input type="checkbox" class="num-check" value="${n.id}"> ${n.label}</label>`).join('') || '<span class="hint">Add a number first.</span>';
}

function replyRateLabel(r) {
  if (!r || r.status === 'insufficient_data') return `not enough data yet (${r ? r.total : 0}/${'?'} contacts past grace period)`;
  const pct = (r.replyRate * 100).toFixed(1) + '%';
  const words = { healthy: 'healthy', warning: 'below target', critical: 'critical (auto-pause zone)' };
  return `${pct} (${r.replied}/${r.total}) — ${words[r.status] || r.status}`;
}

async function pauseNumber(id) { await jpost(`/api/numbers/${id}/pause`, {}); refreshNumbers(); }
async function resumeNumber(id) { await jpost(`/api/numbers/${id}/resume`, {}); refreshNumbers(); }

async function importFile() {
  const fileInput = document.getElementById('import-file');
  const campaignName = document.getElementById('campaign-name').value;
  if (!fileInput.files[0]) return alert('Choose a file first');
  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  if (campaignName) fd.append('campaignName', campaignName);
  const r = await fetch('/api/contacts/import', { method: 'POST', body: fd });
  const result = await r.json();
  document.getElementById('import-result').innerHTML = result.error
    ? `<p style="color:red">${result.error}</p>`
    : `<p class="hint">Campaign "${result.campaignName}": ${result.added} added, ${result.mergedAsMultiUnit} merged as multi-unit, ${result.invalid} invalid numbers, ${result.duplicateSkipped} duplicates skipped, ${result.suppressedOptedOut} suppressed (previously opted out).</p>`;
  refreshAll();
}

function selectedNumberIds() {
  return [...document.querySelectorAll('.num-check:checked')].map(el => Number(el.value));
}

async function assignContacts() {
  const numberIds = selectedNumberIds();
  if (!numberIds.length) return alert('Select at least one number');
  const campaignName = document.getElementById('assign-campaign-name').value || undefined;
  const result = await jpost('/api/campaigns/assign', { campaignName, numberIds });
  document.getElementById('assign-result').innerHTML = `<p class="hint">Assigned ${result.assigned} of ${result.totalEligible} eligible contacts across ${numberIds.length} number(s).</p>`;
  refreshAll();
}

async function startCampaign() {
  const numberIds = selectedNumberIds();
  await jpost('/api/campaigns/start', { numberIds });
  document.getElementById('assign-result').innerHTML += `<p class="hint">Sending started. Numbers will respect business hours, warm-up caps, and delays automatically.</p>`;
}

async function stopCampaign() {
  await jpost('/api/campaigns/stop', {});
  document.getElementById('assign-result').innerHTML += `<p class="hint">All sending stopped.</p>`;
}

async function refreshDashboard() {
  const d = await jget('/api/dashboard');
  const stats = [
    ['Total Contacts', d.totalContacts], ['Valid Numbers', d.validNumbers], ['Invalid Numbers', d.invalidNumbers],
    ['Multi-Unit Owners', d.multiUnitOwners], ['Messages Sent', d.messagesSent], ['Messages Failed', d.messagesFailed],
    ['Replies Received', d.repliesReceived], ['Interested Selling', d.interestedSelling], ['Interested Renting', d.interestedRenting],
    ['Not Interested', d.notInterested], ['Opted Out', d.optedOut],
  ];
  document.getElementById('dashboard').innerHTML = `
    <div class="stat-grid">${stats.map(([label, num]) => `<div class="stat"><div class="num">${num}</div><div class="label">${label}</div></div>`).join('')}</div>
    <h3 style="font-size:13px;margin-top:16px">By Number</h3>
    <table><tr><th>Number</th><th>Status</th><th>Sends</th><th>Delivered</th><th>Opt-outs</th><th>Today's cap</th><th>Reply rate</th></tr>
    ${d.byNumber.map(n => `<tr><td>${n.label}</td><td>${n.status}${n.paused ? ' (paused)' : ''}</td><td>${n.sends}</td><td>${n.delivered}</td><td>${n.optOuts}</td><td>${n.sentToday}/${n.dailyCap}</td><td>${replyRateLabel(n.replyRate)}</td></tr>`).join('')}
    </table>`;
}

async function refreshContacts() {
  const contacts = await jget('/api/contacts');
  const rows = contacts.slice(0, 200).map(c => `
    <tr>
      <td>${c.contactId}</td><td>${c.ownerName}</td><td>${c.phoneE164 || c.phoneRaw}</td><td>${c.unitNumber}</td>
      <td>${c.invalidNumber ? 'Invalid' : c.optedOut ? 'Opted out' : c.messageStatus}</td>
      <td>${c.selling || ''}</td><td>${c.renting || ''}</td><td>${c.assignedNumberId || ''}</td>
    </tr>`).join('');
  document.getElementById('contacts-table').innerHTML = `
    <p class="hint">Showing up to 200 of ${contacts.length} contacts.</p>
    <table><tr><th>ID</th><th>Owner</th><th>Phone</th><th>Unit</th><th>Status</th><th>Selling</th><th>Renting</th><th>Number</th></tr>${rows}</table>`;
}

async function refreshInbox() {
  const msgs = await jget('/api/inbox');
  document.getElementById('inbox').innerHTML = msgs.length
    ? `<table><tr><th>Time</th><th>Contact</th><th>Message</th></tr>${msgs.map(m => `<tr><td>${new Date(m.createdAt).toLocaleString()}</td><td>${m.contactId || 'unknown'}</td><td>${m.body}</td></tr>`).join('')}</table>`
    : '<p class="hint">No replies yet.</p>';
}

function refreshAll() {
  refreshNumbers();
  refreshDashboard();
  refreshContacts();
  refreshInbox();
}

refreshAll();
setInterval(refreshAll, 8000);

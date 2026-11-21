(() => {
  const $ = (id) => document.getElementById(id);
  let analysisId = null;
  let pollTimer = null;
  let reportData = null;

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));

  const api = async (url, options) => {
    const res = await fetch(url, options);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return res.json();
  };

  function badge(value, extra='') {
    const v = String(value ?? 'UNKNOWN');
    return `<span class="badge ${esc(v)} ${extra}">${esc(v)}</span>`;
  }

  function table(headers, rows) {
    if (!rows.length) return '<p class="msg">No records.</p>';
    return '<table><thead><tr>' + headers.map(h => `<th>${esc(h)}</th>`).join('') +
      '</tr></thead><tbody>' + rows.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') +
      '</tr>').join('') + '</tbody></table>';
  }

  async function start(source, name) {
    $('input-msg').textContent = '';
    $('start-btn').disabled = true;
    try {
      const run = await api('/analysis/start', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({source, name: name || null, wait: false})
      });
      analysisId = run.id;
      $('progress-card').hidden = false;
      $('results-card').hidden = true;
      $('run-id').textContent = run.id;
      poll();
      clearInterval(pollTimer);
      pollTimer = setInterval(poll, 1500);
    } catch (e) {
      $('input-msg').textContent = e.message;
    } finally {
      $('start-btn').disabled = false;
    }
  }

  $('start-btn').onclick = () => {
    const source = $('source').value.trim();
    if (!source) { $('input-msg').textContent = 'Enter a repository path or URL.'; return; }
    start(source, $('name').value.trim());
  };

  $('upload-btn').onclick = async () => {
    const file = $('upload').files[0];
    if (!file) { $('input-msg').textContent = 'Choose an archive first.'; return; }
    const form = new FormData();
    form.append('file', file);
    if ($('name').value.trim()) form.append('name', $('name').value.trim());
    $('input-msg').textContent = 'Uploading...';
    try {
      const repo = await api('/repositories/upload', {method:'POST', body:form});
      $('source').value = repo.path;
      $('input-msg').textContent = `Uploaded ${repo.name}. Click Start analysis.`;
    } catch (e) {
      $('input-msg').textContent = e.message;
    }
  };

  async function poll() {
    if (!analysisId) return;
    try {
      const s = await api(`/analysis/${analysisId}/status`);
      $('progress-bar').style.width = `${s.progress}%`;
      $('stage').textContent = s.current_stage;
      $('status-badge').textContent = s.status;
      $('status-badge').className = 'badge ' + s.status;
      $('live-log').textContent = (s.logs || []).slice(-12).reverse()
        .map(l => `[${l.stage}] ${l.message}`).join('\n');
      if (s.status === 'COMPLETED' || s.status === 'FAILED') {
        clearInterval(pollTimer);
        await loadResults();
      }
    } catch (e) { /* keep polling */ }
  }

  async function loadResults() {
    const [status, components, tests, issues, rca, evidence] = await Promise.all([
      api(`/analysis/${analysisId}/status`),
      api(`/analysis/${analysisId}/components`),
      api(`/analysis/${analysisId}/tests`),
      api(`/analysis/${analysisId}/issues`),
      api(`/analysis/${analysisId}/rca`),
      api(`/analysis/${analysisId}/evidence`),
    ]);
    reportData = {status, components, tests, issues, rca, evidence};
    $('results-card').hidden = false;
    const s = status.summary || {};
    $('summary').innerHTML = Object.entries(s).filter(([k]) => typeof s[k] !== 'object')
      .map(([k,v]) => `<div><strong>${esc(v)}</strong><span>${esc(k.replace(/_/g,' '))}</span></div>`).join('');

    $('md-link').href = `/analysis/${analysisId}/report/download?format=markdown`;
    $('json-link').href = `/analysis/${analysisId}/report/download?format=json`;

    $('tab-components').innerHTML = table(['Name','Type','Path','Language'],
      components.map(c => [esc(c.name), esc(c.type), esc(c.path), esc(c.language)]));

    $('tab-tests').innerHTML = table(['Test','Component','Status','Duration','Error'],
      tests.map(t => [esc(t.name), esc(t.component), badge(t.status),
        esc(t.duration), esc((t.error_message||'').slice(0,200))]));

    $('tab-issues').innerHTML = table(['ID','Type','Severity','Status','Source','File','Message'],
      issues.map(f => [esc(f.id), esc(f.type), badge(f.severity), badge(f.status),
        badge(f.source), esc(f.location), esc(f.message.slice(0,220))]));

    $('tab-rca').innerHTML = (rca.rca_chains || []).map(c => `
      <div class="rca">
        <h3>${esc(c.id)} · ${badge(c.status)} · confidence ${esc(c.confidence)} · verified ${esc(c.verified)}</h3>
        <p><b>Symptom:</b> ${esc(c.symptom)}</p>
        <p><b>Immediate cause:</b> ${esc(c.immediate_cause)}</p>
        <p><b>Root cause:</b> ${esc(c.root_cause)}</p>
        <p><b>Contributing:</b> ${esc((c.contributing_factors||[]).join('; ') || 'none')}</p>
        <p><b>Impact:</b> ${esc((c.downstream_impact||[]).join('; ') || 'none')}</p>
        <p><b>Evidence:</b> ${esc((c.evidence_ids||[]).join(', ') || 'see findings')}</p>
      </div>`).join('') || '<p>No RCA chains.</p>';

    const nodes = {};
    (issues || []).forEach(f => nodes[f.id] = f);
    const graphLines = [];
    (rca.issue_groups || []).forEach(g => {
      const root = nodes[g.root_issue_id];
      if (!root) return;
      graphLines.push(`<div class="graph-node">${esc(root.id)} · ${esc(root.type)} · ${esc(root.location)} ${badge(root.status)}</div>`);
      (g.dependent_issue_ids || []).forEach(id => {
        const d = nodes[id];
        graphLines.push(`<div class="graph-edge">└─ causes → ${esc(id)} · ${esc(d ? d.type : '')} ${badge(d ? d.status : '')}</div>`);
      });
      (g.duplicate_issue_ids || []).forEach(id =>
        graphLines.push(`<div class="graph-edge">└─ duplicate → ${esc(id)}</div>`));
    });
    $('tab-graph').innerHTML = graphLines.join('') || '<p>No causal relationships found.</p>';

    $('tab-fixes').innerHTML = (rca.fixes || []).map((f,i) => {
      const v = (rca.verifications||[]).find(x => x.fix_id === f.id);
      const r = (rca.regressions||[]).find(x => x.fix_id === f.id);
      return `<div class="rca">
        <h3>${i+1}. ${esc(f.id)} · priority ${esc(f.priority)} · ${esc(f.affected_file||f.affected_component)}</h3>
        <p><b>Change:</b> ${esc(f.required_change)}</p>
        <p><b>Why:</b> ${esc(f.why_this_fix_works)}</p>
        <p><b>Side effects:</b> ${esc(f.potential_side_effects)}</p>
        <p><b>Verification:</b> ${v ? badge(v.outcome) + ' ' + esc(v.details) : 'not run'}</p>
        <p><b>Regression:</b> ${r ? esc(r.details) : 'not run'}</p>
      </div>`;
    }).join('') || '<p>No fix recommendations.</p>';

    const gaps = (status.summary && status.summary.missing_test_coverage) || [];
    const probes = tests.filter(t => t.framework === 'generated-probe').length;
    $('tab-coverage').innerHTML = `
      <p><strong>Generated boundary probes executed:</strong> ${probes}</p>
      ${gaps.length
        ? '<ul>' + gaps.map(g => `<li>${esc(g)}</li>`).join('') + '</ul>'
        : '<p class="msg">No obvious coverage gaps detected.</p>'}
    `;

    $('tab-logs').innerHTML = table(['Stage','Level','Message'],
      (status.logs||[]).map(l => [esc(l.stage), badge(l.level), esc(l.message.slice(0,300))]));
  }

  document.querySelectorAll('.tabs button').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      $('tab-' + btn.dataset.tab).classList.add('active');
    };
  });
})();

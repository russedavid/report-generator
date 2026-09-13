(() => {
  const $ = id => document.getElementById(id);
  let listing, current, position = 0, verdict = null, dirty = false;
  const node = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  function choose(value) {
    verdict = value; dirty = true;
    $('pass').setAttribute('aria-pressed', String(value === 'pass'));
    $('fail').setAttribute('aria-pressed', String(value === 'fail'));
  }
  function renderReport(output) {
    $('report').replaceChildren();
    let report;
    try { report = JSON.parse(output); }
    catch { $('report').append(node('pre', output || 'No model output was returned.')); return; }
    const names = {equipment_id:'Equipment',priority:'Priority',next_service_date:'Next service date',
      observations:'Observations',completed_work:'Completed work',parts_used:'Parts used',
      proposed_actions:'Proposed actions',uncertainties:'Uncertainties'};
    const keys = [...Object.keys(names).filter(key => key in report), ...Object.keys(report).filter(key => !(key in names))];
    for (const key of keys) {
      const value = report[key];
      const section = node('section'); section.append(node('h3', names[key] || key));
      const citation = ids => node('span', ' [' + (ids || []).join(', ') + ']', 'citation');
      if (Array.isArray(value)) {
        if (!value.length) section.append(node('p', 'No entries.'));
        const list = node('ul');
        value.forEach(item => {
          const li = node('li', item.text || JSON.stringify(item));
          if (item.basis) li.append(node('small', ' — ' + item.basis, 'muted'));
          li.append(citation(item.source_ids)); list.append(li);
        }); section.append(list);
      } else if (value && typeof value === 'object' && 'state' in value) {
        const paragraph = node('p', value.state + (value.value !== null && value.value !== undefined ? ': ' + value.value : ''));
        paragraph.append(citation(value.source_ids)); section.append(paragraph);
        if (value.items) value.items.forEach(part => {
          const p = node('p', part.part_number + ' · quantity ' + (part.quantity ?? 'unknown'));
          p.append(citation(part.source_ids)); section.append(p);
        });
      } else section.append(node('p', typeof value === 'string' ? value : JSON.stringify(value)));
      $('report').append(section);
    }
  }
  async function show(next) {
    if (!listing?.ready.length) {
      $('position').textContent = 'Waiting for the first trace. Refresh when a generation finishes.';
      $('save').disabled = true; return;
    }
    position = (next + listing.ready.length) % listing.ready.length;
    const response = await fetch('/trace/' + encodeURIComponent(listing.ready[position]));
    if (!response.ok) throw new Error('This trace is not ready yet.');
    current = await response.json();
    $('position').textContent = 'Trace ' + (position + 1) + ' of ' + listing.ready.length + ' available · ' + listing.planned + ' planned';
    $('sources').replaceChildren();
    for (const source of current.input_sources) {
      const card = node('article', undefined, 'source');
      card.append(node('h3', source.id), node('p', source.type, 'muted'));
      const metadata = ['recorded_at','uploaded_at','applies_to_revision'].filter(k => source[k]).map(k => k + ': ' + source[k]).join(' · ');
      if (metadata) card.append(node('p', metadata, 'muted'));
      card.append(node('div', source.text, 'source-text')); $('sources').append(card);
    }
    renderReport(current.raw_output);
    $('trace').textContent = JSON.stringify({request:current.request,http_status:current.http_status,
      structural_status:current.structural_status,validation_error:current.validation_error,
      provider_error:current.provider_error,
      retrieval:current.retrieval,release_id:current.release_id,transition:current.transition,
      elapsed_seconds:current.elapsed_seconds,usage:current.usage},null,2);
    const prior = listing.labels[current.trace_id];
    $('critique').value = prior?.critique || '';
    choose(prior?.verdict || null); dirty = false;
    $('save').disabled = false; $('status').textContent = prior ? 'Previously reviewed. Saving records a new revision.' : '';
    localStorage.setItem('frontline-review-position-' + listing.run_id, current.trace_id);
    window.scrollTo(0, 0);
  }
  async function refresh(keep = true) {
    const selected = keep ? current?.trace_id : null;
    listing = await (await fetch('/items?criteria_version=' + encodeURIComponent($('criteria').value) +
      '&reviewer=' + encodeURIComponent($('reviewer').value))).json();
    $('assessment-link').hidden = !listing.assistant_assessment_url;
    $('progress-text').textContent = (listing.test_mode ? 'UI test — ' : '') + listing.human_reviewed + ' human reviews · ' + listing.ready.length + '/' + listing.planned + ' traces ready';
    $('progress').max = listing.planned; $('progress').value = listing.human_reviewed;
    const linkedTrace = !keep ? new URLSearchParams(location.search).get('trace') : null;
    const remembered = selected || linkedTrace || localStorage.getItem('frontline-review-position-' + listing.run_id);
    position = Math.max(0, listing.ready.indexOf(remembered));
    await show(position);
  }
  async function save() {
    if (!current) return;
    $('save').disabled = true;
    const response = await fetch('/label', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({trace_id:current.trace_id,output_sha256:current.output_sha256,
        reviewer:$('reviewer').value,reviewer_kind:'human',criteria_version:$('criteria').value,
        verdict,critique:$('critique').value})});
    const result = await response.json();
    if (!response.ok) { $('save').disabled=false; throw new Error(result.error || 'Save failed'); }
    dirty=false; localStorage.setItem('frontline-reviewer', $('reviewer').value);
    const previous = current.trace_id;
    await refresh();
    const next = listing.ready.findIndex(id => !listing.labels[id]);
    await show(next >= 0 ? next : listing.ready.indexOf(previous));
    $('status').textContent='Saved.';
  }
  function guarded(callback) {
    return () => Promise.resolve().then(callback).catch(error => {
      $('status').textContent = error.message; $('status').classList.add('error');
    });
  }
  function navigate(delta) {
    if (dirty && !confirm('Leave this unsaved review?')) return;
    return show(position + delta);
  }
  const selectVerdict = value => { choose(value); $('critique').focus(); };
  $('pass').onclick = () => selectVerdict('pass'); $('fail').onclick = () => selectVerdict('fail');
  $('previous').onclick = guarded(() => navigate(-1)); $('next').onclick = guarded(() => navigate(1));
  $('refresh').onclick = guarded(() => {
    if (dirty && !confirm('Refresh and discard this unsaved review?')) return;
    return refresh();
  });
  $('save').onclick = guarded(save);
  $('critique').oninput = () => { dirty=true; };
  $('reviewer').onchange = guarded(() => {
    localStorage.setItem('frontline-reviewer', $('reviewer').value);
    if (!dirty) return refresh();
  });
  $('criteria').onchange = guarded(() => {
    localStorage.setItem('frontline-review-criteria', $('criteria').value);
    if (!dirty) return refresh();
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault(); guarded(save)(); return;
    }
    if (['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)) return;
    const actions={p:()=>selectVerdict('pass'),f:()=>selectVerdict('fail'),n:()=>navigate(1),b:()=>navigate(-1)};
    if (actions[event.key.toLowerCase()]) { event.preventDefault(); guarded(actions[event.key.toLowerCase()])(); }
  });
  window.addEventListener('beforeunload', event => { if (dirty) {event.preventDefault();event.returnValue='';} });
  $('reviewer').value=localStorage.getItem('frontline-reviewer') || '';
  $('criteria').value=localStorage.getItem('frontline-review-criteria') || 'open-v1';
  guarded(() => refresh(false))();
})();

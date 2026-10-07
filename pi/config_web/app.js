'use strict';
const $ = id => document.getElementById(id);
let authenticated = false, busy = false, setupToken = new URLSearchParams(location.hash.slice(1)).get('setup');
let profileSignature = '', btSignature = '', wifiSignature = '', appLoaded = false, lastJob = '', servicesSignature = '';
if (setupToken) history.replaceState(null, '', location.pathname);
window.addEventListener('hashchange', () => { const token = new URLSearchParams(location.hash.slice(1)).get('setup'); if (token) { setupToken = token; history.replaceState(null, '', location.pathname); showLogin(); } });
const node = (tag, text, className) => { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; };
function notice(message, error = false) { $('notice').textContent = message; $('notice').className = error ? 'error' : ''; $('notice').hidden = !message; }
async function api(path, data) {
  const response = await fetch('/api/' + path, data === undefined ? {cache: 'no-store'} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Nano-Config': '1'}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) { if (response.status === 401 && path !== 'login') setAuth(false); throw new Error(result.error || 'Request failed'); }
  return result;
}
function setAuth(value) {
  authenticated = value; $('sign-in').hidden = value; $('sign-out').hidden = !value; $('locked').hidden = value;
  document.querySelectorAll('.management').forEach(b => b.disabled = !value || busy);
  if (!value) { profileSignature = btSignature = wifiSignature = servicesSignature = ''; appLoaded = false; $('profile-grid').replaceChildren(); $('app-grid').replaceChildren(); $('bt-devices').replaceChildren(); $('wifi-networks').replaceChildren(); $('wifi-saved').replaceChildren(); $('pairing').hidden = true; $('operation').hidden = true; }
}
async function action(path, data = {}, confirmation) {
  if (!authenticated) return showLogin();
  if (confirmation && !confirm(confirmation)) return;
  try { const result = await api(path, data); notice(result.message || 'Operation started.'); await refresh(); }
  catch (error) { notice(error.message, true); }
}
function button(text, callback, className = '') { const b = node('button', text, 'management ' + className); b.disabled = !authenticated || busy; b.addEventListener('click', callback); return b; }
function showLogin() {
  $('login-title').textContent = setupToken ? 'Set your household password.' : 'Unlock controls.';
  $('login-help').textContent = setupToken ? 'Choose a password to share with your household. This setup link works once.' : 'Use the shared household password.';
  $('confirm-label').hidden = !setupToken; $('confirm-password').required = Boolean(setupToken);
  $('login-password').autocomplete = setupToken ? 'new-password' : 'current-password';
  $('login-submit').textContent = setupToken ? 'Set password' : 'Unlock'; $('login-error').textContent = '';
  if (!$('login-dialog').open) $('login-dialog').showModal(); $('login-password').focus();
}
$('sign-in').onclick = $('unlock-link').onclick = showLogin;
$('login-cancel').onclick = () => $('login-dialog').close();
$('login-form').onsubmit = async e => {
  e.preventDefault(); const password = $('login-password').value;
  if (setupToken && password !== $('confirm-password').value) { $('login-error').textContent = 'The passwords do not match.'; return; }
  $('login-submit').disabled = true;
  try { await api(setupToken ? 'setup' : 'login', setupToken ? {token: setupToken, password} : {password}); setupToken = null; $('login-form').reset(); $('login-dialog').close(); setAuth(true); notice('Controls unlocked.'); await refresh(); }
  catch (error) { $('login-error').textContent = error.message; }
  finally { $('login-submit').disabled = false; }
};
$('sign-out').onclick = async () => { try { await api('logout', {}); setAuth(false); notice('Controls locked.'); } catch (e) { notice(e.message, true); } };
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { document.querySelectorAll('nav button').forEach(n => n.classList.toggle('active', n === b)); document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === b.dataset.tab)); });
document.querySelectorAll('[data-player]').forEach(b => b.onclick = () => action('player/control', {command: b.dataset.player}));
$('reload-home').onclick = $('reload-apps').onclick = () => action('apps/reload', {}, 'Close any custom app on the Nano first. Reload its installed app icons now?');
const selectedApps = () => [...$('app-grid').querySelectorAll('input:checked')].map(i => i.value);
$('save-apps').onclick = () => action('apps/save', {selected: selectedApps()});
$('install-apps').onclick = async () => {
  if (!authenticated) return showLogin();
  if (!confirm('Close any custom app on the Nano first. Build and install these selected apps? Existing app data will be kept.')) return;
  await action('apps/install', {selected: selectedApps()});
};
$('add-profile').onclick = () => { if (!authenticated) return showLogin(); const name = prompt('Name this Spotify profile:', ''); if (name !== null) action('profiles/add', {name}, 'Adding an account stops playback while you log in. Continue?'); };
$('cancel-pairing').onclick = () => action('profiles/cancel');
function profiles(data) {
  const signature = JSON.stringify(data) + busy;
  if (signature !== profileSignature) {
    profileSignature = signature; $('profile-grid').replaceChildren();
    for (const [index, profile] of data.profiles.entries()) {
      const active = profile.id === data.active, card = node('article', undefined, 'profile' + (active ? ' selected' : ''));
      card.append(node('div', profile.name.slice(0, 1).toUpperCase(), 'avatar c' + index % 5), node('h3', profile.name), node('p', profile.username));
      const actions = node('div', undefined, 'actions');
      const select = button(active && !data.pending ? 'Active ✓' : 'Listen', () => action('profiles/activate', {id: profile.id}, 'Switch to ' + profile.name + '? Current playback will stop.'), 'small primary');
      select.dataset.unavailable = active && !data.pending ? '1' : ''; select.disabled = !authenticated || busy || active && !data.pending;
      const rename = button('Rename', () => { const name = prompt('Profile name:', profile.name); if (name !== null) action('profiles/rename', {id: profile.id, name}); }, 'small');
      const remove = button('Delete', () => action('profiles/delete', {id: profile.id}, 'Delete ' + profile.name + ' from this Pi?' + (active ? ' This also stops playback and signs it out.' : '')), 'small danger');
      rename.dataset.unavailable = remove.dataset.unavailable = data.pending ? '1' : ''; rename.disabled = remove.disabled = busy || data.pending; actions.append(select, rename, remove); card.append(actions); $('profile-grid').append(card);
    }
    if (!data.profiles.length) $('profile-grid').append(node('p', 'Add a Spotify account to get started.', 'muted'));
  }
  $('pairing').hidden = !data.pending; $('add-profile').disabled = busy || data.pending;
  $('pairing-title').textContent = 'Connect ' + data.pending_name;
}
function renderApps(data) {
  if (appLoaded) return; appLoaded = true; $('app-grid').replaceChildren();
  for (const app of data.apps) { const label = node('label', undefined, 'app-choice'), input = node('input'); input.type = 'checkbox'; input.value = app.id; input.checked = app.enabled; label.append(input, node('span', app.name)); $('app-grid').append(label); }
}
function deviceRow(name, detail, buttons = []) { const row = node('div', undefined, 'device'), info = node('div'); info.append(node('strong', name), node('small', detail)); row.append(info); if (buttons.length) { const actions = node('div', undefined, 'actions'); actions.append(...buttons); row.append(actions); } return row; }
function renderBluetooth(data) {
  $('bt-message').textContent = data.message || ''; const scanning = Boolean(data.flags & 2), working = Boolean(data.flags & 4);
  $('scan-bluetooth').textContent = scanning ? 'Stop scanning' : 'Scan for speakers'; $('scan-bluetooth').disabled = busy || working;
  const signature = JSON.stringify(data) + busy; if (signature === btSignature) return; btSignature = signature; $('bt-devices').replaceChildren();
  for (const d of data.devices) {
    const actions = [button(d.flags & 1 ? 'Connect' : 'Pair / connect', () => action('bluetooth/action', {command: 12, address: d.address}), 'small primary')];
    if (d.flags & 2) actions.push(button('Disconnect', () => action('bluetooth/action', {command: 13, address: d.address}), 'small'));
    if (d.flags & 1) actions.push(button('Forget', () => action('bluetooth/action', {command: 14, address: d.address}, 'Forget ' + d.name + '? You will need to pair it again.'), 'small danger'));
    actions.forEach(b => { b.dataset.unavailable = working ? '1' : ''; b.disabled = busy || working; }); $('bt-devices').append(deviceRow(d.name, d.address + ' · ' + (d.flags & 4 ? 'Audio connected' : d.flags & 2 ? 'Connected' : d.flags & 1 ? 'Saved' : 'New'), actions));
  }
  if (!data.devices.length) $('bt-devices').append(node('p', 'No speakers found yet.', 'muted'));
}
$('scan-bluetooth').onclick = () => action('bluetooth/action', {command: 11});
$('scan-wifi').onclick = () => action('wifi/scan', {interface: $('wifi-interface').value});
$('confirm-wifi').onclick = () => action('wifi/confirm');
$('wifi-form').onsubmit = async e => { e.preventDefault(); const values = {ssid: $('wifi-ssid').value, password: $('wifi-password').value, interface: $('wifi-interface').value}; await action('wifi/connect', values, 'Switch the Pi to this Wi-Fi? Reopen the new address and confirm within 120 seconds, or the previous connection returns.'); $('wifi-password').value = ''; };
function renderWifi(data) {
  const adapters = data.interfaces || []; if (adapters.length && JSON.stringify([...$('wifi-interface').options].map(o => o.value)) !== JSON.stringify(adapters)) { const selected = $('wifi-interface').value; $('wifi-interface').replaceChildren(...adapters.map(a => { const o = node('option', a); o.value = a; return o; })); if (adapters.includes(selected)) $('wifi-interface').value = selected; }
  $('wifi-confirmation').hidden = !data.confirmation_pending; const signature = JSON.stringify(data) + busy; if (signature === wifiSignature) return; wifiSignature = signature;
  $('wifi-networks').replaceChildren(); $('wifi-saved').replaceChildren();
  for (const n of data.networks) $('wifi-networks').append(deviceRow(n.ssid, `${n.signal}% · ${n.security || 'Open'}${n.active ? ' · Connected' : ''}`, [button('Choose', () => { $('wifi-ssid').value = n.ssid; $('wifi-interface').value = n.interface; $('wifi-password').focus(); }, 'small')]));
  for (const n of data.saved) $('wifi-saved').append(deviceRow(n.name, 'Saved on Pi', [button('Connect', () => action('wifi/saved', {uuid: n.uuid, interface: $('wifi-interface').value}, 'Connect to ' + n.name + '? Confirm on the new connection within 120 seconds.'), 'small'), button('Forget', () => action('wifi/forget', {uuid: n.uuid}, 'Forget ' + n.name + '? If it is connected, the Pi may disconnect.'), 'small danger')]));
}
$('reboot').onclick = () => action('pi/reboot', {}, 'Restart the Pi? Music will stop and this page will disconnect.');
$('poweroff').onclick = () => action('pi/poweroff', {}, 'Shut down the Pi? To start it again, you will need to reconnect its power.');
$('password-form').onsubmit = async e => { e.preventDefault(); try { const result = await api('password', {current: $('current-password').value, password: $('new-password').value}); $('password-form').reset(); notice(result.message); } catch (error) { notice(error.message, true); } };
function renderStatus(data) {
  $('host').textContent = data.hostname || 'Pi'; $('address').textContent = data.url || location.origin;
  $('temperature').textContent = data.temperature === null || data.temperature === undefined ? '—' : data.temperature.toFixed(1) + ' °C';
  const hours = Math.floor((data.uptime || 0) / 3600), minutes = Math.floor((data.uptime || 0) / 60) % 60;
  $('uptime').textContent = hours + 'h ' + minutes + 'm'; $('nano-state').textContent = data.nano_connected ? 'Connected' : 'Unplugged';
  $('connection').textContent = 'Pi online'; $('connection').className = 'pill online';
  const spotify = data.spotify || {}; $('track').textContent = spotify.track || 'Nothing playing';
  $('spotify-state').textContent = spotify.ready ? 'Spotify ready · ' + (spotify.username || 'Select iPod Nano in Spotify') : spotify.online ? 'Waiting for Spotify login' : 'Spotify service offline';
  $('network-addresses').textContent = (data.addresses || []).map(a => a.interface + ': ' + a.ip).join(' · ') || 'No network address';
  const signature = JSON.stringify(data.services) + authenticated + busy;
  if (signature !== servicesSignature) { servicesSignature = signature; $('service-list').replaceChildren(); for (const [service, state] of Object.entries(data.services || {})) $('service-list').append(deviceRow(service.replace('.service', ''), state, [button('Restart', () => action('services/restart', {service}, 'Restart ' + service + '?'), 'small')])); }
}
function renderJob(job) {
  busy = Boolean(job && job.state === 'running'); document.querySelectorAll('.management').forEach(b => b.disabled = !authenticated || busy || b.dataset.unavailable === '1');
  $('operation').hidden = !job || !authenticated; if (!job) return;
  $('job-title').textContent = job.label; $('job-state').textContent = job.state; $('job-message').textContent = job.message; $('job-details').hidden = !job.log; $('job-log').textContent = job.log;
  if (lastJob !== job.id + job.state && job.state === 'error') notice(job.message, true); lastJob = job.id + job.state;
}
let refreshing = false;
async function refresh() {
  if (refreshing) return; refreshing = true;
  try {
    const [status, session] = await Promise.all([api('status'), api('session')]); setAuth(session.authenticated); renderStatus(status);
    if (!session.configured && !setupToken) { notice('Set the household password using the private setup link displayed on the Pi.'); }
    if (authenticated) {
      const [job, accounts, apps, wifi, bluetooth] = await Promise.all([api('jobs'), api('profiles'), api('apps'), api('wifi'), api('bluetooth')]);
      renderJob(job.job); profiles(accounts); renderApps(apps); renderWifi(wifi); renderBluetooth(bluetooth); renderStatus(status);
      if (accounts.pending) {
        const {login} = await api('spotify/code'); let valid = false;
        if (login) { try { const url = new URL(login.url); valid = url.protocol === 'https:' && (url.hostname === 'spotify.com' || url.hostname.endsWith('.spotify.com')); if (valid) $('spotify-link').href = url.href; } catch (_) {} }
        $('spotify-link').hidden = !valid; $('pair-code').textContent = login ? login.code : 'Getting a login code…'; $('pair-expiry').textContent = login ? 'Expires ' + new Date(login.expires_at).toLocaleTimeString() : 'If the code has expired, cancel and add the account again.';
      }
    }
  } catch (error) { $('connection').textContent = 'Reconnecting…'; $('connection').className = 'pill offline'; notice(error.message, true); }
  finally { refreshing = false; }
}
refresh().then(() => { if (setupToken) showLogin(); }); setInterval(refresh, 4000);

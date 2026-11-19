// MCP server settings <-> the admin form's fields. No DOM and no display text here (the page
// translates its labels), so Node can test it. The backend (mcp_settings.py) still validates.
// toConfig starts from the saved settings and changes only what the form changed, so a key the
// form doesn't show, or one it shows but nobody touched, goes back exactly as it was read
// (secrets are read as "", which the backend takes as "keep the saved value").
(function (root) {
  'use strict';
  // older settings keep the token in API_ACCESS_TOKEN; mcp_client.py sends it as "Bearer <token>"
  // over any Authorization header, so the form treats it as the token too
  const SHOWN = ['command', 'args', 'env', 'url', 'transport', 'headers', 'API_ACCESS_TOKEN'];
  const isAuth = k => k.toLowerCase() === 'authorization';
  const isObj = v => !!v && typeof v === 'object' && !Array.isArray(v);
  const isStrMap = v => isObj(v) && Object.values(v).every(x => typeof x === 'string');
  // one item per line, as typed (an argument may need its spaces); a final newline adds nothing
  const rows = text => {
    const out = String(text || '').replace(/\r/g, '').split('\n');
    if (out[out.length - 1] === '') out.pop();
    return out;
  };
  const copy = v => JSON.parse(JSON.stringify(v));
  // a key a plain object cannot hold as data, or one a line of the form cannot show as typed
  const badKey = k => k === '__proto__' || /[=\n\r]/.test(k) || k !== k.trim();

  function check(c) {
    if (!isObj(c)) return 'json';
    if ('command' in c && 'url' in c) return 'json';   // the form keeps one of the two, so it would drop the other
    // (a line break in command or url would be lost in the single-line inputs)
    for (const k of ['command', 'url', 'transport', 'API_ACCESS_TOKEN'])
      if (k in c && (typeof c[k] !== 'string' || /[\n\r]/.test(c[k]))) return 'json';
    // an argument with a line break would split into two lines of the form, and a last empty
    // argument would look like a final newline
    if ('args' in c && !(Array.isArray(c.args) && c.args.every(a => typeof a === 'string' && !/[\n\r]/.test(a))
                         && c.args[c.args.length - 1] !== '')) return 'json';
    for (const k of ['env', 'headers']) if (k in c && !(isStrMap(c[k]) && !Object.keys(c[k]).some(badKey))) return 'json';
    // the form's two transports and the gateway's alias; anything else on a URL server would be rewritten
    if (!('command' in c) && 'transport' in c && !['sse', 'http', 'streamable-http'].includes(c.transport)) return 'json';
    return null;
  }

  function fromConfig(config) {
    const c = config || {};
    const headers = c.headers || {};
    const extra = Object.keys(c).filter(k => !SHOWN.includes(k))
      .concat(Object.keys(headers).filter(k => !isAuth(k)).map(k => 'headers.' + k));
    // the gateway treats a missing transport as sse and "http" as streamable-http (mcp_client.py);
    // a saved value is kept even on a command server, so switching back to a URL keeps it
    const transport = c.transport === 'streamable-http' || c.transport === 'http' ? 'streamable-http'
      : c.transport === 'sse' || c.url ? 'sse' : 'streamable-http';
    return {
      kind: 'command' in c ? 'command' : 'url',   // the client picks stdio by key, even an empty one
      url: c.url || '', transport,
      token: '', token_set: 'API_ACCESS_TOKEN' in c || Object.keys(headers).some(isAuth), token_clear: false,
      command: c.command || '',
      args: (c.args || []).join('\n'),
      env: Object.keys(c.env || {}).map(k => k + '=').join('\n'),   // values are never read back
      extra,
    };
  }

  function toConfig(f, base) {
    const b = base || {};
    const was = fromConfig(b);
    const out = copy(b);
    // args, env and token edits count even if the type is switched afterwards (see below for which)
    // (on a URL server only removing them all counts: an argument typed for the command may hold a secret)
    if (f.args !== was.args && (f.kind === 'command' || !rows(f.args).length)) {
      const args = rows(f.args);
      if (args.length) out.args = args; else delete out.args;
    }
    if (f.env !== was.env) {
      let env = {};
      for (const line of rows(f.env)) {
        if (!line.trim()) continue;
        const i = line.indexOf('=');
        const key = line.slice(0, i).trim();
        if (i < 1 || !key || badKey(key) || Object.prototype.hasOwnProperty.call(env, key)) { env = null; break; }
        env[key] = line.slice(i + 1);
      }
      // an unfinished env is reported only where it is shown; on a URL server the saved one stays
      // (and the backend asks for its secrets if they cannot go along)
      if (!env && f.kind === 'command') return {bad: 'env'};
      // on a URL server only removals count: a value typed for the command is not stored with it
      if (env && f.kind !== 'command') env = Object.fromEntries(Object.keys(env)
        .filter(k => Object.prototype.hasOwnProperty.call(b.env || {}, k)).map(k => [k, b.env[k]]));
      if (env) { if (Object.keys(env).length) out.env = env; else delete out.env; }
    }
    // (a saved secret sent back as "" goes back only if nothing else that reaches the server changed;
    // otherwise mcp_settings.validate asks for it again)
    // "clear" wins over a typed token; a token typed for a URL is not stored with a command
    const token = f.token_clear || f.kind === 'command' ? '' : String(f.token || '').trim();
    if (token || f.token_clear) {
      // a new or cleared token drops API_ACCESS_TOKEN and every spelling of Authorization
      delete out.API_ACCESS_TOKEN;
      const headers = {};
      for (const [k, v] of Object.entries(out.headers || {})) if (!isAuth(k)) headers[k] = v;
      // shortcut: a value without a space gets "Bearer " in front; a header that really is one
      // bare word has to be set in the JSON editor (none of the servers we know uses one)
      if (token) headers.Authorization = /\s/.test(token) ? token : 'Bearer ' + token;
      if (Object.keys(headers).length) out.headers = headers; else delete out.headers;
    }
    if (f.kind === 'command') {
      // an untouched command goes back as saved (a path may end in a space); a typed one is trimmed
      const command = f.command === was.command && b.command ? b.command : String(f.command || '').trim();
      if (!command.trim()) return {bad: 'command'};
      delete out.url;
      out.command = command;
      return {config: out};
    }
    // an untouched URL goes back as saved (a path may end in a space); a typed one is trimmed
    const url = f.url === was.url && b.url ? b.url : String(f.url || '').trim();
    if (!url.trim()) return {bad: 'url'};
    delete out.command;
    out.url = url;
    // written when it was saved, when the server is new to URLs, or when it differs from the
    // gateway's default for a missing value (sse); an untouched server without one stays without
    // untouched: as saved ("http" too); a command server may hold a value no URL server accepts
    if (f.transport === was.transport && ['sse', 'http', 'streamable-http'].includes(b.transport)) out.transport = b.transport;
    else if ('transport' in b || !('url' in b) || f.transport !== 'sse') out.transport = f.transport === 'sse' ? 'sse' : 'streamable-http';
    return {config: out};
  }

  root.McpForm = {check, fromConfig, toConfig};
})(window);

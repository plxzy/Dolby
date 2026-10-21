/** Local conversion and metadata/artwork tagging; server values are text, never HTML. */
import { getJson, postJson } from '../api.js';
import { card } from '../components/card.js';
import { blockedHere, markUnavailable, notice, toast } from '../components/feedback.js';
import { $, el, replace } from '../dom.js';

export function init({ capabilities }) {
  const setup = $('#convert-setup');
  setup.append(operation('convert', capabilities), operation('tag', capabilities));
}

function operation(kind, capabilities) {
  const converting = kind === 'convert';
  const fields = {};
  const pickers = [];
  const output = el('div', { class: 'view-output', id: `${kind}-result`, 'aria-live': 'polite' });
  const field = (name, label, { type = 'text', value, required = false, browse = false, folder = false } = {}) => {
    const id = `${kind}-${name}`;
    const input = el('input', {
      class: 'input', id, name, type, value, required,
      min: type === 'number' ? 0 : null, step: type === 'number' ? 1 : null,
      spellcheck: 'false', autocomplete: 'off',
    });
    fields[name] = input;
    const row = el('div', { class: 'convert-path' }, input);
    const picker = (directory) => {
      const button = el('button', { class: 'btn', type: 'button', onclick: async () => {
        if (blockedHere(button)) return;
        button.disabled = true;
        try {
          const { paths } = await getJson(`/api/pick${directory ? '?directory=true' : ''}`);
          if (paths.length) {
            input.value = paths[0];
            input.dispatchEvent(new Event('input', { bubbles: true }));
          }
        } catch (error) {
          toast(error.message, { tone: 'danger' });
        } finally {
          button.disabled = false;
        }
      } }, directory ? 'Browse folder…' : 'Browse…');
      pickers.push(button);
      row.append(button);
    };
    if (browse) picker(false);
    if (folder) picker(true);
    return el('div', { class: 'convert-field' }, el('label', { for: id }, `${label}${required ? ' (required)' : ''}`), row);
  };
  const checkbox = (name, label, checked = false) => {
    const input = el('input', { type: 'checkbox', name, id: `${kind}-${name}`, checked });
    fields[name] = input;
    return el('label', { class: 'convert-check' }, input, label);
  };
  const submit = el('button', { class: 'btn btn-primary', type: 'submit', id: `${kind}-go` }, converting ? 'Convert' : 'Tag');
  const metadata = el('fieldset', { class: 'convert-fields' },
    el('legend', {}, 'Apple TV metadata'),
    field('metadata_id', 'Apple TV ID (optional)'),
    field('storefront', 'Storefront', { value: 'US' }),
    field('show', 'Show name override (optional)'),
    field('season', 'Season (optional)', { type: 'number' }),
    field('episode', 'Episode (optional)', { type: 'number' }),
  );
  const paths = converting
    ? [field('input', 'Source file', { required: true, browse: true }), field('output', 'Output .m4v path', { required: true })]
    : [field('file', 'Existing file or folder', { required: true, browse: true, folder: true })];
  const options = converting ? el('div', { class: 'convert-options' },
    checkbox('overwrite', 'Overwrite existing output'),
    checkbox('skip_pgs', 'Skip PGS → VobSub'),
    checkbox('no_metadata', 'Skip Apple TV metadata', true),
  ) : null;
  if (converting) {
    metadata.disabled = true;
    fields.no_metadata.addEventListener('change', () => { metadata.disabled = fields.no_metadata.checked; });
  }
  const form = el('form', { class: 'convert-form', id: `${kind}-form`, method: 'post', action: `/api/${kind}` },
    paths, options, metadata,
    field('season_artwork', 'Season artwork image path (optional)', { browse: true }),
    el('p', { class: 'convert-help' }, converting
      ? 'Stream-copy compatible video and audio without re-encoding. Enter a full output path. Artwork can be applied even when metadata is skipped.'
      : 'Folders tag sorted immediate .mp4 and .m4v files only. With artwork but no Apple TV ID or show override, only artwork is changed. Otherwise metadata is looked up noninteractively.'),
    submit,
  );
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    const body = {};
    for (const [name, input] of Object.entries(fields)) {
      body[name] = input.type === 'checkbox' ? input.checked
        : input.type === 'number' ? (input.value === '' ? null : Number(input.value))
          : input.value.trim() || (name === 'storefront' ? 'US' : null);
    }
    const required = converting ? ['input', 'output'] : ['file'];
    const missing = required.find((name) => !body[name]);
    if (missing) {
      replace(output, notice({ tone: 'danger', title: 'Path required', body: 'Enter a non-empty absolute path.' }));
      fields[missing].focus();
      return;
    }
    submit.disabled = true;
    output.setAttribute('aria-busy', 'true');
    replace(output, notice({ title: converting ? 'Converting…' : 'Tagging…', body: 'Working on local files. Keep this page open until the result appears.' }));
    try {
      const data = await postJson(`/api/${kind}`, body);
      replace(output, card({ title: converting ? 'Convert result' : 'Tag result', body: [
        data.directory && el('p', {}, `Directory: ${data.directory} · ${data.files.length} files`),
        el('pre', { class: 'convert-json' }, JSON.stringify(data, null, 2)),
      ] }));
    } catch (error) {
      replace(output, notice({ tone: 'danger', title: converting ? 'Couldn’t convert' : 'Couldn’t tag', body: error.message }));
    } finally {
      submit.disabled = false;
      output.removeAttribute('aria-busy');
    }
  });
  capabilities.then((caps) => {
    if (caps && !caps.tools.osascript) {
      for (const button of pickers) markUnavailable(button, 'The native picker needs macOS — paste a local path instead');
    }
  });
  return el('section', { class: 'convert-operation', 'aria-labelledby': `${kind}-heading` },
    el('h2', { id: `${kind}-heading` }, converting ? 'Convert to Apple TV' : 'Tag existing file or season folder'),
    form, output,
  );
}

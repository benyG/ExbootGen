(function(global){
  if (global.makeSelectSearchable) {
    return;
  }

  const STYLE_ID = 'searchable-select-style';

  function ensureStyles(){
    if (document.getElementById(STYLE_ID)) {
      return;
    }
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      .searchable-select-wrapper {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
      }
      .searchable-select-input {
        width: 100%;
        padding: 0.55rem 0.75rem;
        border-radius: 0.75rem;
        border: 1px solid rgba(255, 255, 255, 0.18);
        background: rgba(15, 23, 42, 0.35);
        color: inherit;
        font: inherit;
        transition: border-color 0.2s ease, box-shadow 0.2s ease;
      }
      .searchable-select-input::placeholder {
        color: rgba(255, 255, 255, 0.55);
      }
      .searchable-select-input:focus {
        outline: none;
        border-color: rgba(96, 165, 250, 0.55);
        box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.35);
        background: rgba(15, 23, 42, 0.55);
      }
      .searchable-select-element {
        width: 100%;
      }
    `;
    document.head.appendChild(style);
  }

  function normalizeText(value){
    return (value == null ? '' : String(value))
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase();
  }

  function toOptionData(item){
    if (!item) {
      return {
        value: '',
        label: '',
        normalizedLabel: '',
        normalizedValue: '',
        dataset: {},
        disabled: false,
        selected: false
      };
    }
    if (item instanceof HTMLOptionElement) {
      return {
        value: item.value,
        label: item.textContent,
        normalizedLabel: normalizeText(item.textContent),
        normalizedValue: normalizeText(item.value),
        dataset: { ...item.dataset },
        disabled: item.disabled,
        selected: item.selected
      };
    }
    const value = item.value ?? item.id ?? '';
    const label = item.label ?? item.text ?? item.name ?? value;
    return {
      value: String(value),
      label: String(label),
      normalizedLabel: normalizeText(label),
      normalizedValue: normalizeText(value),
      dataset: item.dataset ? { ...item.dataset } : {},
      disabled: Boolean(item.disabled),
      selected: Boolean(item.selected)
    };
  }

  function createOptionElement(data){
    const option = document.createElement('option');
    option.value = data.value;
    option.textContent = data.label;
    option.disabled = data.disabled;
    for (const [key, value] of Object.entries(data.dataset || {})) {
      option.dataset[key] = value;
    }
    return option;
  }

  function pickSelectableValue(list, explicitValue, previousValue, lastValue){
    const candidates = new Set(list.map(opt => opt.value));
    const preferred = [
      explicitValue,
      list.find(opt => opt.selected)?.value,
      previousValue,
      lastValue
    ].map(v => (v == null ? null : String(v)));
    for (const candidate of preferred) {
      if (candidate != null && candidates.has(candidate)) {
        return candidate;
      }
    }
    return list.length ? list[0].value : null;
  }

  function makeSelectSearchable(select, config = {}){
    if (!(select instanceof HTMLSelectElement)) {
      return null;
    }

    if (select.__searchableSelect) {
      return select.__searchableSelect;
    }

    ensureStyles();

    const settings = {
      placeholder: 'Rechercher…',
      keepSearchOnUpdate: true,
      ...config
    };

    const wrapper = document.createElement('div');
    wrapper.className = 'searchable-select-wrapper';

    const input = document.createElement('input');
    input.type = 'search';
    input.autocomplete = 'off';
    input.className = 'searchable-select-input';
    input.placeholder = settings.placeholder;
    input.setAttribute('aria-label', settings.placeholder);

    const parent = select.parentNode;
    parent.insertBefore(wrapper, select);
    wrapper.appendChild(input);
    wrapper.appendChild(select);
    select.classList.add('searchable-select-element');

    let lastValue = select.value;
    let masterOptions = Array.from(select.options).map(toOptionData);

    function rebuildOptions(filtered, explicitValue){
      const previousValue = select.value;
      select.innerHTML = '';
      filtered.forEach(optData => {
        const option = createOptionElement(optData);
        select.appendChild(option);
      });

      if (!filtered.length) {
        select.selectedIndex = -1;
        lastValue = '';
        return;
      }

      const valueToSelect = pickSelectableValue(filtered, explicitValue, previousValue, lastValue);
      if (valueToSelect != null) {
        select.value = valueToSelect;
        lastValue = select.value;
      }
    }

    function applyFilter(term, explicitValue){
      const normalizedTerm = normalizeText(term);
      const filtered = normalizedTerm
        ? masterOptions.filter(opt =>
            opt.normalizedLabel.includes(normalizedTerm) ||
            opt.normalizedValue.includes(normalizedTerm)
          )
        : masterOptions.slice();
      rebuildOptions(filtered, explicitValue);
    }

    input.addEventListener('input', () => {
      applyFilter(input.value);
    });

    input.addEventListener('keydown', evt => {
      if (evt.key === 'ArrowDown') {
        select.focus();
        evt.preventDefault();
      }
    });

    select.addEventListener('change', () => {
      lastValue = select.value;
    });

    const api = {
      select,
      input,
      setSearchTerm(term = '') {
        input.value = term;
        applyFilter(term, null);
      },
      setOptions(options = [], opts = {}) {
        const { selectedValue = null, keepSearch = true } = opts;
        masterOptions = (options || []).map(toOptionData);
        const term = keepSearch ? input.value : '';
        if (!keepSearch) {
          input.value = term;
        }
        applyFilter(term, selectedValue);
      },
      syncFromSelect(opts = {}) {
        const { keepSearch = true, selectedValue = null } = opts;
        masterOptions = Array.from(select.options).map(toOptionData);
        const term = keepSearch ? input.value : '';
        if (!keepSearch) {
          input.value = term;
        }
        applyFilter(term, selectedValue);
      },
      refresh() {
        applyFilter(input.value, null);
      }
    };

    select.__searchableSelect = api;

    applyFilter(input.value, null);

    return api;
  }

  global.makeSelectSearchable = makeSelectSearchable;
})(window);

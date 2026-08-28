(() => {
  const normalize = (value) => {
    if (value === null || value === undefined || value === '') return [];
    if (Array.isArray(value)) return value.flatMap(normalize);
    if (typeof value === 'object') {
      const personalName = [value.given_name, value.family_name]
        .filter(Boolean)
        .join(' ');
      const label = value.label
        ?? value.title
        ?? personalName
        ?? value.notation
        ?? value.pid;
      return label ? [String(label)] : Object.values(value).flatMap(normalize);
    }
    return [String(value)];
  };

  const unique = (values) => [...new Set(values)].sort((a, b) => a.localeCompare(b));

  const initialize = (root) => {
    const items = [...root.querySelectorAll('[data-orinoco-item]')].map((node) => ({
      node,
      data: JSON.parse(node.dataset.orinoco ?? '{}'),
    }));
    const filterFields = JSON.parse(root.dataset.filterFields ?? '[]');
    const searchFields = JSON.parse(root.dataset.searchFields ?? '["title"]');
    const filters = root.querySelector('[data-orinoco-filters]');
    const search = root.querySelector('#orinoco-search');
    const count = root.querySelector('[data-orinoco-count]');

    const render = () => {
      const query = search?.value.trim().toLocaleLowerCase() ?? '';
      const selected = new Map(filterFields.map((field) => [
        field,
        [...root.querySelectorAll(`input[data-orinoco-field="${CSS.escape(field)}"]:checked`)]
          .map((input) => input.value),
      ]));
      let visible = 0;
      for (const item of items) {
        const searchable = searchFields
          .flatMap((field) => normalize(item.data[field]))
          .join(' ')
          .toLocaleLowerCase();
        const matchesQuery = !query || searchable.includes(query);
        const matchesFilters = [...selected].every(([field, values]) => (
          values.length === 0
          || values.some((value) => normalize(item.data[field]).includes(value))
        ));
        item.node.hidden = !(matchesQuery && matchesFilters);
        if (!item.node.hidden) visible += 1;
      }
      if (count) count.textContent = `${visible} result${visible === 1 ? '' : 's'}`;
    };

    if (filters) {
      for (const field of filterFields) {
        const values = unique(items.flatMap((item) => normalize(item.data[field])));
        if (values.length === 0) continue;
        const group = document.createElement('fieldset');
        group.className = 'orinoco-filter-group';
        const legend = document.createElement('legend');
        legend.textContent = field.replaceAll('_', ' ');
        group.append(legend);
        for (const value of values) {
          const label = document.createElement('label');
          label.className = 'orinoco-filter-option';
          const input = document.createElement('input');
          input.type = 'checkbox';
          input.value = value;
          input.dataset.orinocoField = field;
          input.addEventListener('change', render);
          label.append(
            input,
            document.createTextNode(value.replace(/^.*:/, '')),
          );
          group.append(label);
        }
        filters.append(group);
      }
    }

    search?.addEventListener('input', render);
    root.querySelector('[data-orinoco-clear]')?.addEventListener('click', () => {
      if (search) search.value = '';
      root.querySelectorAll('input[data-orinoco-field]').forEach((input) => {
        input.checked = false;
      });
      render();
    });
    render();
  };

  document.querySelectorAll('[data-orinoco-taxonomy]').forEach(initialize);
})();

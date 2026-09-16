class FilterableList {
    constructor(config) {
        // // Example config:
        // config = {
        //     itemSelector: '.instrument',
        //     searchInputId: "search",
        //     countId: "instrument-count",   
        //     filters: [
        //         'kind',
        //         'topic',
        //     ],
        //     searchFields: [
        //         "title",
        //         "kind",
        //         "year",
        //         "topic",
        //         "author"
        //     ],
        // }
        this.config = config;
        this.items = Array.from(
            document.querySelectorAll(config.itemSelector)
        );
        // Initialize state
        // first for search
        this.state = {
            search: "",
        };
        // then for each filter field
        config.filters.forEach(filter => {
            this.state[filter] = new Set();
        });
        // now initialize filters and rendering
        this.init();
    }

    init() {
        // On startup:
        // 1) Build filters
        this.buildFilters();
        // 2) Register search input
        this.registerSearch();
        // 3) Add custom interactions/handlers per filter
        this.registerCustomInteractions();
        // 4) Initial render
        this.render();
    }

    // Build filters specified by config
    buildFilters() {
        this.config.filters.forEach(filter => {
            this.renderFilter(`filter-${filter}`, filter, this.getUniqueValues(filter).map(String));
        })
    }

    // Register event handler search input
    registerSearch() {
        const searchEl = document.getElementById(this.config.searchInputId);
        if (!searchEl) return;
        searchEl.addEventListener("input", e => {
            this.state.search = e.target.value.toLowerCase();
            this.render();
        });
    }

    registerCustomInteractions() {
        // topic click handlers
        if (this.config.filters.includes('topic')) {
            document.querySelectorAll(".topic-chip").forEach(el => {
                el.addEventListener("click", () => {
                    const topic = el.dataset.topic;
                    this.selectFilter("topic", topic);
                });
            });
        }
        // anything else...
    }

    // Build specific filter options dynamically
    getUniqueValues(field) {
        const values = new Set();
        this.items.forEach(el => {
            if (field === "topic") {
                this.getTopics(el).forEach(v => values.add(v));
            } else {
                values.add(el.dataset[field]);
            }
        });
        return Array.from(values).sort();
    }

    // Format display values
    formatValue(field, value) {
        if (value) {
            if (field == "kind") {
                return value.split(":").at(-1)
            }
        }
        return value
    }

    renderFilter(containerId, field, values) {
        const container = document.getElementById(containerId);
        values.forEach(value => {
            const id = `${field}-${value}`;
            const label = document.createElement("label");
            label.style.display = "block";
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.value = value;
            checkbox.dataset.field = field;
            checkbox.id = id;
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) {
                    this.state[field].add(value);
                } else {
                    this.state[field].delete(value);
                }
                this.render();
            });
            label.appendChild(checkbox);
            label.appendChild(document.createTextNode(" " + this.formatValue(field, value)));
            container.appendChild(label);
        });
    }

    // Filtering logic
    matchesFilters(el) {
        for (const filter of this.config.filters) {
            const selected = this.state[filter];
            if (!selected.size) continue;

            const values = this.getFieldValues(el, filter);
            const match = values.some(v => selected.has(v));
            if (!match) return false;
        }
        return true;
    }

    // Get values of a specific data field on an element
    getFieldValues(el, field) {
        if (field == "topic") {
            return this.getTopics(el);
        } else {
            return [el.dataset[field]];
        }
    }

    // Helper to get topics from an element
    getTopics(el) {
        try {
            return JSON.parse(el.dataset.topic || "[]").map(t => t.display_label);
        } catch {
            return [];
        }
    }

    // Search logic
    matchesSearch(el) {
        if (!this.state.search) return true;
        const searchBlob = this.config.searchFields
            .map(field => {
                const values = this.getFieldValues(el, field);
                return values.join(" ");
            })
            .join(" ")
            .toLowerCase();
        return searchBlob.includes(this.state.search);
    }

    // Change display of divs based on filtering/searching
    render() {
        let count = 0;
        this.items.forEach(el => {
            const visible =
                this.matchesFilters(el) &&
                this.matchesSearch(el);
            if (visible) count+=1;
            el.style.display = visible ? "" : "none";
        });
        this.renderCount(count)
    }

    // Count of searched+filtered publications
    renderCount(count) {
        const countEl = document.getElementById(this.config.countId);
        if (countEl) {
            countEl.innerHTML = `${count}` ;
        }
    }

    // Set checkbox if user clicks on topic pill
    selectFilter(field, value) {
        const checkbox = document.querySelector(
            `input[type="checkbox"][data-field="${field}"][value="${CSS.escape(value)}"]`
        );
        if (!checkbox) return;
        if (!checkbox.checked) {
            checkbox.checked = true;
            this.state[field].add(value);
            this.render();
        }
    }

    // Clear all
    clearAllFilters() {
        // Reset state
        this.config.filters.forEach(filter => {
            this.state[filter].clear();
        });

        this.state.search = "";
        // Uncheck all checkboxes
        document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.checked = false;
        });
        // Clear search input
        const searchInput = document.getElementById(this.config.searchInputId);
        if (searchInput) {
            searchInput.value = "";
        }
        // Re-render results
        this.render();
    }
}
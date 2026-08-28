fetch('/graph.json')
  .then((response) => {
    if (!response.ok) {
      throw new Error(`Could not load graph: ${response.status}`);
    }
    return response.json();
  })
  .then((graph) => {
    window.orinocoGraph = graph;
    window.dispatchEvent(new CustomEvent('orinoco:graph-ready', { detail: graph }));
    const target = document.getElementById('orinoco-graph');
    if (target) {
      target.dataset.nodes = String(graph.nodes?.length ?? 0);
      target.dataset.edges = String(graph.edges?.length ?? 0);
    }
  })
  .catch((error) => {
    console.error(error);
  });

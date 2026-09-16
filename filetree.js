// Keep track of first click on a given distribution tab
const distributionsRendered = [];

// Add dropdown select event listerners
document.getElementById("distribution-select")
.addEventListener("change", (e) => {
    const target = e.target.value;
    if (target) onTabClick(target);
});

async function onTabClick(targetId) {
    // Only show selected pane
    document.querySelectorAll(".tab-pane").forEach(p => {
        p.style.display = "none";
    });
    const pane = document.getElementById(targetId);
    if (pane) pane.style.display = "block";
    // Only render new root for the first click
    if (!distributionsRendered.includes(targetId)) {
        distributionsRendered.push(targetId)
        await renderDistributionRoot(targetId);
    }
}

// Render the root and children for the first time,
// after fetching and decompressing the source json
// and then building the file tree hierarchy
async function renderDistributionRoot(distributionId) {
    const tree = document.getElementById(`filetree${distributionId}`);
    const download = await fetchJsonGzFileContent(`${distributionId}.json.gz`);
    const treeData = buildTreeFromDataDownload(download);
    renderChildren(treeData.children, tree);
}

// Fetch and stream gzipped json file
async function fetchJsonGzFileContent(url) {
    const response = await fetch(url, { cache: 'no-cache' });
    if (!response.ok) {
        configError.value = true;
        throw new Error(
            `Error fetching config file: ${response.statusText}`
        );
    }
    const ds = new DecompressionStream("gzip");
    const decompressed = response.body.pipeThrough(ds);
    const json = await new Response(decompressed).json();
    return json
}

function buildTreeFromDataDownload(download) {
    // A DataDownload can have many children DataDownloads via the hasPart array,
    // each with the `position` indicating its path relative to its parent.
    // We need to process every path and create a hierarchical structure.
    // For now we assume no recursion.
    const root = {
        type: "directory",
        name: download.prefLabel || download["@id"] || "distribution",
        children: []
    };
    if (!download.hasPart) {
        return root;
    }
    for (const part of download.hasPart) {
        insertPart(root, part);
    }
    sortTree(root)
    return root;
}

function insertPart(parent, part) {
    const path = part.position.split("/");
    let current = parent;
    for (let i = 0; i < path.length - 1; i++) {
        const dirname = path[i];
        let child = current.children.find(
            x => x.type === "directory"
              && x.name === dirname
        );
        if (!child) {
            child = {
                type: "directory",
                name: dirname,
                children: []
            };
            current.children.push(child);
        }
        current = child;
    }
    const filename = path[path.length - 1];
    current.children.push({
        type: "file",
        name: filename,
        contentSize: part.contentSize,
        contentUrl: part.contentUrl,
        schema: part
    });
}

// Sort children of a directory alphanumerically
function sortTree(node) {
    if (!node.children) {
        return;
    }
    node.children.sort((a, b) => {
        if (a.type === "directory" && b.type !== "directory") {
            return -1;
        }
        if (a.type !== "directory" && b.type === "directory") {
            return 1;
        }
        return a.name.localeCompare(
            b.name,
            undefined,
            {
                numeric: true,
                sensitivity: "base"
            }
        );
    });
    node.children.forEach(sortTree);
}

// Create elements for a directory node
// Uses details-summary element for expand/collapse functionality
// (https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/details)
function createDirectoryNode(node) {
    const li = document.createElement("li");
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    details.classList.add("tree-directory");
    // Open/closed folder icon depending on expand/collapse state
    summary.innerHTML = `
    <span class="icon icon-closed">${HUGO_ICONS.folder}</span>
    <span class="icon icon-open">${HUGO_ICONS.folderOpen}</span>
    ${node.name}
    `;
    details.appendChild(summary);
    const childList = document.createElement("ul");
    details.appendChild(childList);
    let rendered = false;
    // Render directory children when toggled to open state
    details.addEventListener( "toggle",() => {
        if (!details.open || rendered) {
            return;
        }
        renderChildren(node.children, childList);
        rendered = true;
    });
    li.appendChild(details);
    return li;
}

// Render children of an open directory
function renderChildren(children, parentElement) {
    children.forEach(child => {
        const node =
            child.type === "directory"
                ? createDirectoryNode(child)
                : createFileNode(child);
        parentElement.appendChild(node);
    });
}

// File node with file icon
// TODO: file size
function createFileNode(node) {
    const li = document.createElement("li");
    li.classList.add("file-node");
    const span = document.createElement("span");
    span.innerHTML = `
    <span class="icon">${HUGO_ICONS.file}</span>
    ${node.name}
    `;
    li.appendChild(span);
    if (node.contentbytesize) {
        const size = document.createElement("span");
        size.textContent = ` (${formatBytes(node.contentbytesize)})`;
        size.classList.add("file-size");
        li.appendChild(size);
    }
    return li;
}

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
}
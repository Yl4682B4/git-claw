// Utility helper functions
const API_BASE = "http://localhost:8171";

async function fetchJSON(url, options = {}) {
    const response = await fetch(`${API_BASE}${url}`, {
        headers: { "Content-Type": "application/json" },
        ...options
    });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.json();
}

function debounce(fn, delay = 300) {
    let timer = null;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

export { fetchJSON, debounce };

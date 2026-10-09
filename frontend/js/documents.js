/**
 * Documents panel (Part 7): upload, list, delete, semantic search with sources.
 */

const docUploadForm = document.getElementById("doc-upload-form");
const docFileInput = document.getElementById("doc-file-input");
const docUploadBtn = document.getElementById("doc-upload-btn");
const docSearchForm = document.getElementById("doc-search-form");
const docSearchInput = document.getElementById("doc-search-input");
const docSearchResults = document.getElementById("doc-search-results");
const docList = document.getElementById("doc-list");

async function loadDocuments() {
  if (!docList) return;
  docList.textContent = "Loading…";
  try {
    const documents = await fetchDocuments();
    docList.textContent = "";
    if (documents.length === 0) {
      const p = document.createElement("p");
      p.className = "placeholder-text";
      p.textContent = "No documents uploaded yet.";
      docList.appendChild(p);
      return;
    }
    documents.forEach((doc) => docList.appendChild(renderDocument(doc)));
  } catch (err) {
    docList.textContent = `Failed to load documents: ${err.message}`;
  }
}

function renderDocument(doc) {
  const row = document.createElement("div");
  row.className = "doc-item";

  const name = document.createElement("span");
  name.className = "doc-name";
  name.textContent = doc.filename;

  const meta = document.createElement("span");
  meta.className = "doc-meta";
  meta.textContent =
    doc.status === "failed"
      ? `failed: ${doc.error || "processing error"}`
      : `${doc.page_count} pages · ${doc.chunk_count} chunks · ${doc.status}`;

  const del = document.createElement("button");
  del.className = "btn";
  del.textContent = "🗑";
  del.title = "Delete document";
  del.addEventListener("click", async () => {
    if (!confirm(`Delete "${doc.filename}" and all of its indexed content?`)) return;
    try {
      await deleteDocument(doc.id);
      loadDocuments();
    } catch (err) {
      alert(err.message);
    }
  });

  row.appendChild(name);
  row.appendChild(meta);
  row.appendChild(del);
  return row;
}

docUploadForm && docUploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = docFileInput.files && docFileInput.files[0];
  if (!file) {
    alert("Choose a PDF, TXT, or DOCX file first.");
    return;
  }
  docUploadBtn.disabled = true;
  docUploadBtn.textContent = "Processing…";
  try {
    const doc = await uploadDocument(file);
    docFileInput.value = "";
    docSearchResults.textContent = "";
    loadDocuments();
    docUploadBtn.textContent = `✓ Indexed ${doc.chunk_count} chunks`;
    setTimeout(() => (docUploadBtn.textContent = "Upload"), 3000);
  } catch (err) {
    alert(err.message);
    docUploadBtn.textContent = "Upload";
  } finally {
    docUploadBtn.disabled = false;
  }
});

docSearchForm && docSearchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = docSearchInput.value.trim();
  if (!query) return;
  docSearchResults.textContent = "Searching…";
  try {
    const result = await searchDocuments(query);
    docSearchResults.textContent = "";
    if (result.count === 0) {
      const p = document.createElement("p");
      p.className = "placeholder-text";
      p.textContent = "No relevant passages found in your documents.";
      docSearchResults.appendChild(p);
      return;
    }
    result.results.forEach((hit) => {
      const card = document.createElement("div");
      card.className = "doc-result";
      const source = document.createElement("div");
      source.className = "source";
      source.textContent = `Source: ${hit.source} (${Math.round(hit.similarity * 100)}% match)`;
      const excerpt = document.createElement("div");
      excerpt.className = "excerpt";
      excerpt.textContent = hit.excerpt;
      card.appendChild(source);
      card.appendChild(excerpt);
      docSearchResults.appendChild(card);
    });
  } catch (err) {
    docSearchResults.textContent = `Search failed: ${err.message}`;
  }
});

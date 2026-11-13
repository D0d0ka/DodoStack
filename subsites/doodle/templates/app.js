'use strict';

const appBase = document.body.dataset.adminBase || '';
const API = appBase + '/api';
const searchPage = appBase + '/search';
const PREVIEW_COUNT = 5;

const _cache = {
  searches: {},
  maxCacheSize: 50,
};

let _searchTimeout = null;
let _isSearching = false;
let _searchPaginationState = {
  page: 1,
  totalPages: 0,
  total: 0,
  pageSize: 20,
};
let _activeSearchOptions = {
  sort: 'relevance',
  from_date: '',
  to_date: '',
  domain: '',
  language: 'all',
  content_type: 'all',
  exact_phrase_only: false,
  exclude: '',
};

const RECENT_QUERIES_KEY = 'doodleRecentQueries';

const $ = id => document.getElementById(id);
const show = el => { if (el) el.classList.remove('hidden'); return el; };
const hide = el => { if (el) el.classList.add('hidden'); return el; };

function debounce(func, delay) {
  let timeoutId;
  return function(...args) {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => func(...args), delay);
  };
}

function getCachedSearch(key) {
  return _cache.searches[key] || null;
}

function setCachedSearch(key, data) {
  const keys = Object.keys(_cache.searches);
  if (keys.length >= _cache.maxCacheSize) {
    delete _cache.searches[keys[0]];
  }
  _cache.searches[key] = data;
}

async function _renderProgressively(containerEl, items, buildHtml, q) {
  const best = items.slice(0, PREVIEW_COUNT);
  const rest = items.slice(PREVIEW_COUNT);

  containerEl.innerHTML = best.map(i => buildHtml(i, q)).join('');
  if (!rest.length) return;

  const spinner = document.createElement('div');
  spinner.className = 'results-loading-more';
  spinner.innerHTML = '<div class="spinner spinner-sm"></div><span>Loading more results...</span>';
  containerEl.appendChild(spinner);

  await new Promise(r => setTimeout(r, 380));
  if (!containerEl.contains(spinner)) return;

  spinner.remove();

  const label = document.createElement('div');
  label.className = 'results-more-label';
  label.textContent = 'More results';
  containerEl.appendChild(label);

  const restEl = document.createElement('div');
  restEl.className = 'results-group-rest';
  restEl.innerHTML = rest.map(i => buildHtml(i, q)).join('');
  containerEl.appendChild(restEl);
}

function _buildWebResultHtml(r, query) {
  const url = r.url ?? '';
  const title = r.title || url;
  const snippet = highlightKeywords(r.snippet ?? '', query);
  const display = url.length > 70 ? url.slice(0, 70) + '...' : url;
  return `<div class="result-item">
      <div class="result-url">${escapeHtml(display)}</div>
      <a class="result-title" href="${escapeWebLink(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>
      <div class="result-snippet">${snippet}</div>
    </div>`;
}

function _buildPicItemHtml(item) {
  return `<article class="pic-card db-pic-card">
      <a href="${escapeWebLink(item.source_url || item.url)}" target="_blank" rel="noopener noreferrer" class="pic-placeholder-link">
        ${item.url && !item.url.startsWith('http://127.0.0.1')
          ? `<img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.title || item.url)}" loading="lazy" decoding="async" />`
          : '<div class="pic-placeholder">Picture Page</div>'}
      </a>
      <div class="pic-caption">
        <a class="pic-link-title" href="${escapeWebLink(item.source_url || item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
        <div class="pic-link-url">${escapeHtml(truncateUrl(item.source_url || item.url, 44))}</div>
      </div>
    </article>`;
}

function _buildVideoItemHtml(item, q) {
  return `<div class="video-card db-video-card">
      <a href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer" class="video-thumb-wrap video-thumb-fallback">
        ${item.thumbnail_url
          ? `<img class="video-thumb" src="${escapeHtml(item.thumbnail_url)}" alt="${escapeHtml(item.title || item.url)}" loading="lazy" decoding="async" />`
          : '<div class="video-thumb-placeholder">Video</div>'}
      </a>
      <div class="video-info">
        <a class="video-title" href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
        <div class="video-meta">${escapeHtml(truncateUrl(item.source_url || item.url))}</div>
        <div class="result-snippet media-snippet">${highlightKeywords(item.snippet || '', q)}</div>
      </div>
    </div>`;
}

function _buildSoundItemHtml(item, q) {
  return `<div class="audio-card">
      <div class="audio-main">
        <div class="audio-icon">♪</div>
        <div class="audio-info">
          <a class="audio-title" href="${escapeWebLink(item.source_url || item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
          <div class="audio-meta">${escapeHtml(truncateUrl(item.source_url || item.url))}</div>
        </div>
      </div>
      ${(item.url && (item.media_match !== false || isLikelyPlayableAudioUrl(item.url)))
        ? `<audio class="audio-player" controls preload="metadata" src="${escapeHtml(item.url)}"></audio>`
        : `<div class="audio-no-preview media-snippet">${highlightKeywords(item.snippet || '', q)}${item.url ? `<br><a href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">Open media link</a>` : ''}</div>`}
    </div>`;
}

function _buildYouTubeItemHtml(item, q) {
  if (item.embed_url || item.youtube_id) {
    return `<div class="yt-card">
        <div class="yt-embed-wrap">
          <iframe class="yt-embed"
                  src="${escapeHtml(item.embed_url || `https://www.youtube-nocookie.com/embed/${item.youtube_id}`)}"
                  title="${escapeHtml(item.title || '')}"
                  loading="lazy"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowfullscreen></iframe>
        </div>
        <div class="yt-info">
          <div class="yt-title">${escapeHtml(item.title || item.url)}</div>
          <div class="yt-author">${escapeHtml(truncateUrl(item.source_url || item.url))}</div>
        </div>
      </div>`;
  }
  return `<div class="yt-card yt-fallback-card">
      <div class="yt-info yt-fallback-info">
        <a class="video-title" href="${escapeWebLink(item.source_url || item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
        <div class="yt-author">${escapeHtml(truncateUrl(item.source_url || item.url))}</div>
        <div class="result-snippet media-snippet">${highlightKeywords(item.snippet || '', q)}</div>
      </div>
    </div>`;
}

/* ── Admin session ─────────────────────────────────────────────────────── */
const isAdminPage = document.body.classList.contains('admin-page');
let _adminHistoryItems = [];
let _adminRecommendedItems = [];
let _lastCrawlLogSignature = '';

function jsonHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
  };
}

/* ── Page init ─────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initSearchAutocomplete();
  initSearchFormHandling();
  initLuckyButton();

  // Crawl modal — present on both pages
  if ($('openCrawlModal')) initCrawlModal();

  if (isAdminPage) initAdminPage();

  // Search results page — only search.html has #resultsSection
  if ($('resultsSection')) initSearchPage();
});

function initSearchPage() {
  initTabs();
  initFilterControls();

  const params = new URLSearchParams(location.search);
  const q = params.get('q') || '';
  const requestedTab = normalizeTab(params.get('tab') || 'all');
  const requestedPage = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);

  _activeSearchOptions = extractSearchOptionsFromParams(params);
  applySearchOptionsToUi(_activeSearchOptions);
  initFilterPanelToggle(hasActiveSearchOptions(_activeSearchOptions));
  syncTabInput(requestedTab);

  if (q) {
    const inp = $('searchInput');
    if (inp) inp.value = q;
    doSearch(q, requestedTab, _activeSearchOptions, requestedPage);
  } else {
    $('resultsSummary').textContent = 'Enter a search term above.';
  }
}

function initSearchFormHandling() {
  const forms = document.querySelectorAll(`form[action="${searchPage}"]`);
  forms.forEach(form => {
    form.addEventListener('submit', e => {
      const input = form.querySelector('input[name="q"]');
      const q = (input?.value || '').trim();
      if (!q) return;

      if (!$('resultsSection')) {
        saveRecentQuery(q);
        return;
      }

      e.preventDefault();
      _activeSearchOptions = getSearchOptionsFromUi();
      doSearch(q, currentTab, _activeSearchOptions);
    });
  });
}

function initLuckyButton() {
  const btn = $('luckyBtn');
  if (!btn) return;

  const form = document.querySelector(`form[action="${searchPage}"]`);
  btn.addEventListener('click', async () => {
    const input = form?.querySelector('input[name="q"]') || $('searchInput');
    const q = (input?.value || '').trim();
    if (!q) {
      input?.focus();
      return;
    }

    saveRecentQuery(q);

    const previousLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
      const recRes = await fetch(`${API}/recommended?q=${encodeURIComponent(q)}&limit=12`);
      const recData = await recRes.json();
      const recommended = (recData?.results || []).filter(item => item?.url);

      if (recommended.length) {
        const picked = recommended[Math.floor(Math.random() * recommended.length)];
        location.href = picked.url;
        return;
      }

      const params = new URLSearchParams({ q });
      const res = await fetch(`${API}/search?${params.toString()}`);
      const data = await res.json();
      const first = (data?.results || []).find(item => item?.url);

      if (first?.url) {
        location.href = first.url;
        return;
      }
    } catch (err) {
      console.error('Lucky search failed:', err);
    } finally {
      btn.disabled = false;
      btn.textContent = previousLabel;
    }

    location.href = `${searchPage}?q=${encodeURIComponent(q)}`;
  });
}

function initFilterControls() {
  const applyBtn = $('applyFiltersBtn');
  const resetBtn = $('resetFiltersBtn');
  if (!applyBtn || !resetBtn) return;

  applyBtn.addEventListener('click', () => {
    const q = ($('searchInput')?.value || '').trim();
    if (!q) return;
    _activeSearchOptions = getSearchOptionsFromUi();
    doSearch(q, currentTab, _activeSearchOptions);
  });

  resetBtn.addEventListener('click', () => {
    const defaults = {
      sort: 'relevance',
      from_date: '',
      to_date: '',
      domain: '',
      language: 'all',
      content_type: 'all',
      exact_phrase_only: false,
      exclude: '',
    };
    _activeSearchOptions = defaults;
    applySearchOptionsToUi(defaults);

    const q = ($('searchInput')?.value || '').trim();
    if (q) doSearch(q, currentTab, _activeSearchOptions);
  });
}

function initFilterPanelToggle(openInitially = false) {
  const panel = $('filtersPanel');
  const btn = $('toggleFiltersBtn');
  if (!panel || !btn) return;

  const setState = isOpen => {
    panel.classList.toggle('hidden', !isOpen);
    btn.textContent = isOpen ? 'Hide filters' : 'Show filters';
    btn.setAttribute('aria-expanded', String(isOpen));
  };

  setState(Boolean(openInitially));
  btn.addEventListener('click', () => {
    const isOpen = panel.classList.contains('hidden');
    setState(isOpen);
  });
}

function hasActiveSearchOptions(options = {}) {
  return Boolean(
    (options.sort && options.sort !== 'relevance') ||
    options.from_date ||
    options.to_date ||
    options.domain ||
    (options.language && options.language !== 'all') ||
    (options.content_type && options.content_type !== 'all') ||
    options.exact_phrase_only ||
    options.exclude
  );
}

function extractSearchOptionsFromParams(params) {
  return {
    sort: params.get('sort') || 'relevance',
    from_date: params.get('from_date') || '',
    to_date: params.get('to_date') || '',
    domain: params.get('domain') || '',
    language: params.get('language') || 'all',
    content_type: params.get('content_type') || 'all',
    exact_phrase_only: ['1', 'true', 'yes', 'on'].includes((params.get('exact_phrase_only') || '').toLowerCase()),
    exclude: params.get('exclude') || '',
  };
}

function getSearchOptionsFromUi() {
  return {
    sort: $('filterSort')?.value || 'relevance',
    from_date: $('filterFromDate')?.value || '',
    to_date: $('filterToDate')?.value || '',
    domain: ($('filterDomain')?.value || '').trim(),
    language: $('filterLanguage')?.value || 'all',
    content_type: $('filterContentType')?.value || 'all',
    exact_phrase_only: Boolean($('filterExactPhraseOnly')?.checked),
    exclude: ($('filterExclude')?.value || '').trim(),
  };
}

function applySearchOptionsToUi(options) {
  if ($('filterSort')) $('filterSort').value = options.sort || 'relevance';
  if ($('filterFromDate')) $('filterFromDate').value = options.from_date || '';
  if ($('filterToDate')) $('filterToDate').value = options.to_date || '';
  if ($('filterDomain')) $('filterDomain').value = options.domain || '';
  if ($('filterLanguage')) $('filterLanguage').value = options.language || 'all';
  if ($('filterContentType')) $('filterContentType').value = options.content_type || 'all';
  if ($('filterExactPhraseOnly')) $('filterExactPhraseOnly').checked = Boolean(options.exact_phrase_only);
  if ($('filterExclude')) $('filterExclude').value = options.exclude || '';
}

function appendSearchOptions(params, options = {}) {
  if (options.sort && options.sort !== 'relevance') params.set('sort', options.sort);
  if (options.from_date) params.set('from_date', options.from_date);
  if (options.to_date) params.set('to_date', options.to_date);
  if (options.domain) params.set('domain', options.domain);
  if (options.language && options.language !== 'all') params.set('language', options.language);
  if (options.content_type && options.content_type !== 'all') params.set('content_type', options.content_type);
  if (options.exact_phrase_only) params.set('exact_phrase_only', '1');
  if (options.exclude) params.set('exclude', options.exclude);
}

function buildSearchCacheKey(query, options, page = 1) {
  return `${query}::${page}::${JSON.stringify(options || {})}`;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str ?? '';
  return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function escapeWebLink(value) {
  try {
    const url = new URL(value, document.baseURI);
    if (url.protocol === 'http:' || url.protocol === 'https:') return escapeHtml(value);
  } catch {
    // Leave malformed links inert.
  }
  return '#';
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
function formatDuration(secs) {
  if (!secs) return '';
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}
function formatViews(n) {
  if (!n) return '';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M views';
  if (n >= 1_000) return Math.round(n / 1_000) + 'K views';
  return n + ' views';
}
function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString();
}
function formatUnixDate(seconds) {
  if (!seconds) return '';
  const d = new Date(seconds * 1000);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString();
}

function isLikelyPlayableAudioUrl(rawUrl) {
  if (!rawUrl) return false;
  try {
    const u = new URL(rawUrl);
    const p = (u.pathname || '').toLowerCase();
    return /\.(mp3|wav|ogg|oga|flac|m4a|aac|opus)(?:$|\?)/i.test(p) || /\.(mp3|wav|ogg|oga|flac|m4a|aac|opus)$/i.test(p);
  } catch {
    const lower = String(rawUrl).toLowerCase();
    return /\.(mp3|wav|ogg|oga|flac|m4a|aac|opus)(?:$|\?)/i.test(lower);
  }
}

function mediaEmptyHtml(message) {
  return `<p class="no-results">${escapeHtml(message)}</p>`;
}

function truncateUrl(url, length = 56) {
  return url.length > length ? url.slice(0, length) + '…' : url;
}

/* ── Tab state ────────────────────────────────────────────────────────── */
let currentTab = 'all';
const loadedTabs = new Set();

function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  currentTab = normalizeTab(tab);
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelector(`.tab-btn[data-tab="${currentTab}"]`).classList.add('active');
  document.querySelectorAll('.tab-content').forEach(c => hide(c));
  show($(`tab-${currentTab}`));
  $('resultsSection').dataset.activetab = currentTab;
  syncTabInput(currentTab);
  syncTabInUrl(currentTab);

  const q = $('searchInput').value.trim();
  if (!q || loadedTabs.has(currentTab)) return;
  loadedTabs.add(currentTab);

  if (currentTab === 'pictures') searchPictures(q);
  else if (currentTab === 'videos')  searchVideos(q);
  else if (currentTab === 'sound')   searchSound(q);
  else if (currentTab === 'youtube') searchYouTube(q);
  else if (currentTab === 'social')  searchSocial(q);
  else if (currentTab === 'ai')      searchAi(q);
}

function resetTabs(defaultTab = 'all') {
  currentTab = normalizeTab(defaultTab);
  loadedTabs.clear();
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelector(`[data-tab="${currentTab}"]`).classList.add('active');
  document.querySelectorAll('.tab-content').forEach(c => hide(c));
  show($(`tab-${currentTab}`));
  $('resultsSection').dataset.activetab = currentTab;
  syncTabInput(currentTab);
}

/* ═══════════════════════════════════════════════════════════════════════════
   ALL TAB — web search
   ══════════════════════════════════════════════════════════════════════════ */

async function doSearch(q, preferredTab = 'all', optionsOverride = null, page = 1) {
  if (!q) return;

  checkEasterEgg(q);

  const activeTab = normalizeTab(preferredTab);
  const options = optionsOverride || _activeSearchOptions || getSearchOptionsFromUi();
  _activeSearchOptions = options;
  const safePage = Math.max(1, Number(page || 1));

  saveRecentQuery(q);
  resetTabs(activeTab);
  if (activeTab === 'all') loadedTabs.add('all');
  if (activeTab === 'ai') {
    switchTab('ai');
    return;
  }

  // Check cache first
  const cacheKey = buildSearchCacheKey(q, options, safePage);
  const cached = getCachedSearch(cacheKey);
  if (cached) {
    renderResults(cached);
    if (activeTab !== 'all') switchTab(activeTab);
    syncSearchInUrl(activeTab, q, options, cached.page || safePage);
    return;
  }

  _isSearching = true;
  $('resultsSummary').textContent = 'Searching…';
  $('resultsList').innerHTML = '';
  const pagination = $('searchPagination');
  if (pagination) hide(pagination);

  try {
    const params = new URLSearchParams({ q, page: String(safePage) });
    appendSearchOptions(params, options);
    const fetchOpts = undefined;
    const res  = await fetch(`${API}/search?${params.toString()}`, fetchOpts);
    const data = await res.json();
    setCachedSearch(cacheKey, data);
    renderResults(data);
    syncSearchInUrl(activeTab, q, options, data.page || safePage);
    if (activeTab !== 'all') switchTab(activeTab);
  } catch {
    $('resultsSummary').textContent = '';
    $('resultsList').innerHTML =
      '<p class="no-results">Could not reach the backend. Is it running?</p>';
  } finally {
    _isSearching = false;
  }
}

function normalizeTab(tab) {
  const allowed = new Set(['all', 'pictures', 'videos', 'sound', 'youtube', 'social', 'ai']);
  return allowed.has(tab) ? tab : 'all';
}

function syncTabInput(tab) {
  const inp = $('tabInput');
  if (!inp) return;
  inp.value = normalizeTab(tab);
}

function syncTabInUrl(tab) {
  const q = ($('searchInput')?.value || '').trim();
  if (!q) return;
  const options = _activeSearchOptions || getSearchOptionsFromUi();
  const page = normalizeTab(tab) === 'all' ? _searchPaginationState.page : 1;
  syncSearchInUrl(tab, q, options, page);
}

function syncSearchInUrl(tab, q, options = {}, page = 1) {
  const params = new URLSearchParams(location.search);
  params.set('q', q);
  params.set('tab', normalizeTab(tab));
  [
    'sort', 'from_date', 'to_date', 'domain', 'language',
    'content_type', 'exact_phrase_only', 'exclude',
  ].forEach(key => params.delete(key));
  appendSearchOptions(params, options);
  if (page > 1) params.set('page', String(page));
  else params.delete('page');
  history.replaceState(null, '', `${searchPage}?${params.toString()}`);
}

function renderResults({ results, count, total, page, page_size, total_pages, query, sort, filters, recommended }) {
  renderRecommendedBlock(query, recommended);

  const currentPage = Math.max(1, Number(page || 1));
  const pageSize = Math.max(1, Number(page_size || results?.length || 1));
  const totalResults = Math.max(0, Number(total ?? count ?? results?.length ?? 0));
  const resolvedTotalPages = Math.max(0, Number(total_pages || 0)) || (totalResults ? Math.max(1, Math.ceil(totalResults / pageSize)) : 0);

  _searchPaginationState = {
    page: currentPage,
    totalPages: resolvedTotalPages,
    total: totalResults,
    pageSize,
  };

  if (!totalResults) {
    $('resultsSummary').textContent = `No results for "${query}"`;
    $('resultsList').innerHTML =
      '<p class="no-results">Nothing found — try crawling some pages first.</p>';
    renderSearchPagination();
    return;
  }

  const sortLabel = {
    relevance: 'relevance',
    newest: 'newest',
    oldest: 'oldest',
    most_matches: 'most matches',
  }[sort || _activeSearchOptions.sort] || 'relevance';

  const chips = [];
  if (filters?.domain) chips.push(`domain:${filters.domain}`);
  if (filters?.language) chips.push(`lang:${filters.language}`);
  if (filters?.content_type) chips.push(`type:${filters.content_type}`);
  if (filters?.exact_phrase_only) chips.push('exact phrase');

  $('resultsSummary').textContent =
    `About ${totalResults} result${totalResults !== 1 ? 's' : ''} for "${query}" · page ${currentPage}${resolvedTotalPages ? ` of ${resolvedTotalPages}` : ''} · sort: ${sortLabel}${chips.length ? ` · ${chips.join(' · ')}` : ''}`;

  _renderProgressively($('resultsList'), results, _buildWebResultHtml, query);
  renderSearchPagination();
}

function renderSearchPagination() {
  const container = $('searchPagination');
  if (!container) return;

  const { page, totalPages, total, pageSize } = _searchPaginationState;
  if (!totalPages || totalPages < 2) {
    container.innerHTML = '';
    hide(container);
    return;
  }

  const pages = [];

  const addPage = (targetPage, label = String(targetPage), current = false, disabled = false) => {
    const classNames = ['search-page-btn'];
    if (current) classNames.push('is-current');
    if (disabled) classNames.push('is-disabled');
    pages.push(`<button type="button" class="${classNames.join(' ')}" data-search-page="${targetPage}" ${disabled ? 'disabled' : ''} aria-current="${current ? 'page' : 'false'}">${label}</button>`);
  };

  const addEllipsis = () => pages.push('<span class="search-page-ellipsis" aria-hidden="true">…</span>');

  const windowSize = 2;
  const start = Math.max(2, page - windowSize);
  const end = Math.min(totalPages - 1, page + windowSize);

  addPage(Math.max(1, page - 1), 'Prev', false, page <= 1);
  addPage(1, '1', page === 1);

  if (start > 2) addEllipsis();

  for (let i = start; i <= end; i += 1) {
    if (i > 1 && i < totalPages) addPage(i, String(i), i === page);
  }

  if (end < totalPages - 1) addEllipsis();

  if (totalPages > 1) addPage(totalPages, String(totalPages), page === totalPages);

  addPage(Math.min(totalPages, page + 1), 'Next', false, page >= totalPages);

  container.innerHTML = `
    <div class="search-pagination-meta">Page ${page} of ${totalPages}${total ? ` · ${total} results` : ''}${pageSize ? ` · ${pageSize} per page` : ''}</div>
    <div class="search-pagination-controls">${pages.join('')}</div>
  `;
  show(container);

  if (!container.dataset.bound) {
    container.addEventListener('click', event => {
      const button = event.target.closest('[data-search-page]');
      if (!button || button.disabled) return;
      const targetPage = Number(button.getAttribute('data-search-page') || '1');
      if (!Number.isFinite(targetPage) || targetPage < 1 || targetPage === _searchPaginationState.page) return;
      const query = ($('searchInput')?.value || '').trim();
      if (!query) return;
      doSearch(query, currentTab, _activeSearchOptions || getSearchOptionsFromUi(), targetPage);
    });
    container.dataset.bound = '1';
  }
}

function renderRecommendedBlock(query, items) {
  const block = $('recommendedBlock');
  const list = $('recommendedList');
  if (!block || !list) return;

  const safeItems = (items || []).filter(item => item?.url);
  if (!safeItems.length) {
    list.innerHTML = '';
    hide(block);
    return;
  }

  list.innerHTML = safeItems.map(item => {
    const title = item.title || item.url;
    const displayUrl = truncateUrl(item.url, 70);
    const infoText = `Recommended result for "${query || item.query || ''}"`;
    return `<div class="result-item">
      <div class="result-url">${escapeHtml(displayUrl)}</div>
      <a class="result-title" href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>
      <div class="result-snippet">${escapeHtml(infoText)}</div>
    </div>`;
  }).join('');

  show(block);
}

function highlightKeywords(text, query) {
  if (!text) return '';
  const parts = parseQueryTokens(query);
  const words = [...parts.phrases, ...parts.terms].filter(w => w.length > 1).sort((a, b) => b.length - a.length);
  if (!words.length) return escapeHtml(text);

  const pattern = new RegExp(words.map(escapeRegex).join('|'), 'gi');
  let html = '';
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    html += escapeHtml(text.slice(offset, match.index));
    html += `<strong class="kw-hit">${escapeHtml(match[0])}</strong>`;
    offset = match.index + match[0].length;
  }
  return html + escapeHtml(text.slice(offset));
}

function parseQueryTokens(query) {
  const raw = (query || '').trim().toLowerCase();
  const phrases = [...raw.matchAll(/"([^"]+)"/g)].map(m => m[1].trim()).filter(Boolean);
  const withoutPhrases = raw.replace(/"[^"]+"/g, ' ');
  const terms = withoutPhrases
    .split(/\s+/)
    .map(t => t.trim())
    .filter(t => t.length > 1 && !t.startsWith('-'));
  return { phrases, terms };
}

function getRecentQueries() {
  try {
    const raw = localStorage.getItem(RECENT_QUERIES_KEY);
    const parsed = JSON.parse(raw || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveRecentQuery(query) {
  const normalized = (query || '').trim();
  if (!normalized) return;

  const existing = getRecentQueries().filter(q => q.toLowerCase() !== normalized.toLowerCase());
  const next = [normalized, ...existing].slice(0, 20);
  localStorage.setItem(RECENT_QUERIES_KEY, JSON.stringify(next));
}

function initSearchAutocomplete() {
  const forms = document.querySelectorAll(`form[action="${searchPage}"]`);
  if (!forms.length) return;

  forms.forEach(form => {
    const input = form.querySelector('input[name="q"]');
    const box = form.querySelector('.search-suggestions');
    if (!input || !box) return;

    let currentItems = [];
    let activeIndex = -1;

    const clearActive = () => {
      box.querySelectorAll('.search-suggestion-item').forEach(el => el.classList.remove('is-active'));
      activeIndex = -1;
    };

    const setActive = index => {
      const buttons = box.querySelectorAll('.search-suggestion-item');
      if (!buttons.length) {
        activeIndex = -1;
        return;
      }
      const bounded = Math.max(0, Math.min(index, buttons.length - 1));
      buttons.forEach(el => el.classList.remove('is-active'));
      buttons[bounded].classList.add('is-active');
      buttons[bounded].scrollIntoView({ block: 'nearest' });
      activeIndex = bounded;
    };

    const applyActiveSuggestion = (submitAfter = false) => {
      if (activeIndex < 0 || activeIndex >= currentItems.length) return false;
      input.value = currentItems[activeIndex].text;
      hide(box);
      clearActive();
      if (submitAfter) form.requestSubmit();
      return true;
    };

    const render = items => {
      currentItems = items;
      activeIndex = -1;

      if (!items.length) {
        box.innerHTML = '';
        hide(box);
        return;
      }

      box.innerHTML = items.map(item => `
        <button type="button" class="search-suggestion-item" data-value="${escapeHtml(item.text)}">
          <span class="search-suggestion-text">${escapeHtml(item.text)}</span>
          <span class="search-suggestion-source">${escapeHtml(item.source)}</span>
        </button>
      `).join('');
      show(box);
    };

    const requestSuggestions = debounce(async value => {
      const q = (value || '').trim();
      if (!q || q.length < 2) {
        hide(box);
        return;
      }

      const localItems = getRecentQueries()
        .filter(item => item.toLowerCase().startsWith(q.toLowerCase()))
        .slice(0, 5)
        .map(text => ({ text, source: 'history' }));

      let remoteItems = [];
      try {
        const res = await fetch(`${API}/search/suggestions?q=${encodeURIComponent(q)}&limit=8`);
        const data = await res.json();
        remoteItems = (data.suggestions || []).map(item => ({ text: item.text, source: item.source }));
      } catch {
        remoteItems = [];
      }

      const merged = [];
      const seen = new Set();
      [...localItems, ...remoteItems].forEach(item => {
        const key = item.text.toLowerCase();
        if (!item.text || seen.has(key)) return;
        seen.add(key);
        merged.push(item);
      });

      render(merged.slice(0, 8));
    }, 180);

    input.addEventListener('input', e => requestSuggestions(e.target.value));
    input.addEventListener('focus', () => requestSuggestions(input.value));
    input.addEventListener('keydown', e => {
      const isVisible = !box.classList.contains('hidden') && currentItems.length > 0;

      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && isVisible) {
        e.preventDefault();
        const step = e.key === 'ArrowDown' ? 1 : -1;
        const start = activeIndex === -1 ? (step > 0 ? -1 : currentItems.length) : activeIndex;
        setActive((start + step + currentItems.length) % currentItems.length);
        return;
      }

      if (e.key === 'Tab' && isVisible && !e.shiftKey) {
        const idx = activeIndex === -1 ? 0 : activeIndex;
        if (idx >= 0 && idx < currentItems.length) {
          e.preventDefault();
          setActive(idx);
          applyActiveSuggestion(false);
        }
        return;
      }

      if (e.key === 'Home' && isVisible && activeIndex !== -1) {
        e.preventDefault();
        setActive(0);
        return;
      }

      if (e.key === 'End' && isVisible && activeIndex !== -1) {
        e.preventDefault();
        setActive(currentItems.length - 1);
        return;
      }

      if (e.key === 'Enter' && isVisible) {
        if (applyActiveSuggestion(true)) {
          e.preventDefault();
        }
        return;
      }

      if (e.key === 'Escape') hide(box);
    });

    input.addEventListener('blur', () => {
      setTimeout(() => hide(box), 120);
    });

    box.addEventListener('mousedown', e => {
      const item = e.target.closest('.search-suggestion-item');
      if (!item) return;
      const buttons = Array.from(box.querySelectorAll('.search-suggestion-item'));
      const idx = buttons.indexOf(item);
      if (idx >= 0 && idx < currentItems.length) {
        input.value = currentItems[idx].text;
      } else {
        const value = item.getAttribute('data-value') || '';
        input.value = value;
      }
      hide(box);
      clearActive();
    });
  });
}

/* ═══════════════════════════════════════════════════════════════════════════
   MEDIA TABS — search the crawled SQLite database
   ══════════════════════════════════════════════════════════════════════════ */

async function fetchMediaResults(kind, q, customLimit = null) {
  let url = `${API}/search/media?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}`;
  if (customLimit !== null) {
    url += `&limit=${customLimit}`;
  }
  const fetchOpts = undefined;
  const res = await fetch(url, fetchOpts);
  if (!res.ok) throw new Error('media search failed');
  return res.json();
}

async function searchPictures(q) {
  $('picsSummary').textContent = 'Searching crawled pages for pictures…';
  $('picsGrid').innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';

  try {
    // Request more pictures (backend default 200, we request up to 500)
    const data = await fetchMediaResults('pictures', q, 500);
    renderPictures(data.results || [], q);
  } catch {
    $('picsSummary').textContent = '';
    $('picsGrid').innerHTML = mediaEmptyHtml('Could not load picture results from the backend.');
  }
}

function renderPictures(results, q) {
  if (!results.length) {
    $('picsSummary').textContent = `No pictures found for "${q}"`;
    $('picsGrid').innerHTML = mediaEmptyHtml('Re-crawl pages to extract image assets, or try a different search term.');
    return;
  }

  $('picsSummary').textContent = `${results.length} picture result${results.length !== 1 ? 's' : ''} for "${q}"`;
  _renderProgressively($('picsGrid'), results, _buildPicItemHtml, q);
}

async function searchVideos(q) {
  $('videosSummary').textContent = 'Searching crawled pages for videos…';
  $('videosList').innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';

  try {
    const data = await fetchMediaResults('videos', q);
    renderVideoResults(data.results || [], q);
  } catch {
    $('videosSummary').textContent = '';
    $('videosList').innerHTML = mediaEmptyHtml('Could not load video results from the backend.');
  }
}

function renderVideoResults(results, q) {
  if (!results.length) {
    $('videosSummary').textContent = `No videos found for "${q}"`;
    $('videosList').innerHTML = mediaEmptyHtml('Re-crawl pages to extract video assets, or try a different search term.');
    return;
  }

  $('videosSummary').textContent = `${results.length} video result${results.length !== 1 ? 's' : ''} for "${q}"`;
  _renderProgressively($('videosList'), results, _buildVideoItemHtml, q);
}

async function searchSound(q) {
  $('soundSummary').textContent = 'Searching crawled pages for audio…';
  $('soundList').innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';

  try {
    const data = await fetchMediaResults('sound', q);
    renderSound(data.results || [], q);
  } catch {
    $('soundSummary').textContent = '';
    $('soundList').innerHTML = mediaEmptyHtml('Could not load audio results from the backend.');
  }
}

function renderSound(results, q) {
  if (!results.length) {
    $('soundSummary').textContent = `No audio found for "${q}"`;
    $('soundList').innerHTML = mediaEmptyHtml('Re-crawl pages to extract audio assets, or try a different search term.');
    return;
  }

  $('soundSummary').textContent = `${results.length} audio result${results.length !== 1 ? 's' : ''} for "${q}"`;
  _renderProgressively($('soundList'), results, _buildSoundItemHtml, q);
}

async function searchYouTube(q) {
  $('ytSummary').textContent = 'Searching crawled pages for YouTube…';
  $('ytList').innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';

  try {
    const data = await fetchMediaResults('youtube', q);
    renderYouTubeResults(data.results || [], q);
  } catch {
    $('ytSummary').textContent = '';
    $('ytList').innerHTML = mediaEmptyHtml('Could not load YouTube results from the backend.');
  }
}

function renderYouTubeResults(results, q) {
  if (!results.length) {
    $('ytSummary').textContent = `No YouTube results found for "${q}"`;
    $('ytList').innerHTML = mediaEmptyHtml('Re-crawl pages that include YouTube links, or try another query.');
    return;
  }

  $('ytSummary').textContent = `${results.length} YouTube result${results.length !== 1 ? 's' : ''} for "${q}"`;
  _renderProgressively($('ytList'), results, _buildYouTubeItemHtml, q);
}

async function searchSocial(q) {
  $('socialSummary').textContent = 'Searching YouTube, Reddit, Wikipedia, Modrinth, CurseForge and Steam…';
  $('socialList').innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';

  try {
    const res = await fetch(`${API}/social-search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data?.message || 'Social search failed.');
    renderSocialResults(data, q);
  } catch {
    $('socialSummary').textContent = '';
    $('socialList').innerHTML = mediaEmptyHtml('Could not load social search results from the backend.');
  }
}

function renderSocialResults(data, q) {
  const youtube = data?.youtube || [];
  const reddit = data?.reddit || [];
  const wikipedia = data?.wikipedia || [];
  const modrinth = data?.modrinth || [];
  const curseforge = data?.curseforge || [];
  const steam = data?.steam || [];
  const epicGamesSearchUrl = data?.epic_games_search_url || '';
  const errors = data?.errors || {};
  const total = youtube.length + reddit.length + wikipedia.length + modrinth.length + curseforge.length + steam.length;

  if (!total) {
    $('socialSummary').textContent = `No social results found for "${q}"`;
    const warnings = Object.values(errors).map(msg => `<li>${escapeHtml(msg)}</li>`).join('');
    $('socialList').innerHTML = `
      <div class="social-empty">
        <p class="no-results">Try another query or configure missing API credentials.</p>
        ${warnings ? `<ul class="social-warnings">${warnings}</ul>` : ''}
      </div>
    `;
    return;
  }

  $('socialSummary').textContent = `${total} social result${total !== 1 ? 's' : ''} for "${q}"`;

  const youtubeHtml = youtube.map(item => `
    <article class="social-card">
      <div class="social-source social-source-youtube">YouTube</div>
      <a class="social-title" href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
      <div class="social-meta">${escapeHtml(item.channel || 'Unknown channel')}${item.published_at ? ` · ${escapeHtml(formatDate(item.published_at))}` : ''}</div>
      ${item.description ? `<div class="social-snippet">${highlightKeywords(item.description, q)}</div>` : ''}
    </article>
  `).join('');

  const redditHtml = reddit.map(item => `
    <article class="social-card">
      <div class="social-source social-source-reddit">Reddit</div>
      <a class="social-title" href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
      <div class="social-meta">${escapeHtml(item.subreddit || 'reddit')} · by ${escapeHtml(item.author || 'unknown')} · ${escapeHtml(String(item.score || 0))} points · ${escapeHtml(String(item.num_comments || 0))} comments${item.created_utc ? ` · ${escapeHtml(formatUnixDate(item.created_utc))}` : ''}</div>
    </article>
  `).join('');

  const wikiHtml = wikipedia.map(item => `
    <article class="social-card">
      <div class="social-source social-source-wikipedia">Wikipedia</div>
      <a class="social-title" href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
      ${item.snippet ? `<div class="social-snippet">${highlightKeywords(item.snippet, q)}</div>` : ''}
    </article>
  `).join('');

  const modrinthHtml = modrinth.map(item => `
    <article class="social-card">
      <div class="social-source social-source-modrinth">Modrinth</div>
      <a class="social-title" href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
      <div class="social-meta">${escapeHtml(item.project_type || 'project')}${item.author ? ` · by ${escapeHtml(item.author)}` : ''}${item.downloads ? ` · ${escapeHtml(String(item.downloads))} downloads` : ''}</div>
      ${item.description ? `<div class="social-snippet">${highlightKeywords(item.description, q)}</div>` : ''}
    </article>
  `).join('');

  const curseforgeHtml = curseforge.map(item => `
    <article class="social-card">
      <div class="social-source social-source-curseforge">CurseForge</div>
      <a class="social-title" href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
      <div class="social-meta">${item.authors?.length ? `by ${escapeHtml(item.authors.join(', '))} · ` : ''}${item.download_count ? `${escapeHtml(String(item.download_count))} downloads` : 'downloads unknown'}${item.categories?.length ? ` · ${escapeHtml(item.categories.slice(0, 3).join(', '))}` : ''}</div>
      ${item.summary ? `<div class="social-snippet">${highlightKeywords(item.summary, q)}</div>` : ''}
    </article>
  `).join('');

  const steamHtml = steam.map(item => `
    <article class="social-card">
      <div class="social-source social-source-steam">Steam</div>
      <a class="social-title" href="${escapeWebLink(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url)}</a>
      <div class="social-meta">${escapeHtml(item.type || 'app')}${item.price ? ` · ${escapeHtml(item.price)}` : ''}${item.metascore ? ` · Metascore ${escapeHtml(item.metascore)}` : ''}${item.controller_support ? ` · ${escapeHtml(item.controller_support)} controller` : ''}</div>
    </article>
  `).join('');

  const epicGamesHtml = epicGamesSearchUrl ? `
    <article class="social-card social-card-linkout">
      <div class="social-source social-source-epic">Epic Games</div>
      <a class="social-title" href="${escapeWebLink(epicGamesSearchUrl)}" target="_blank" rel="noopener noreferrer">Open Epic Games Store search for "${escapeHtml(q)}"</a>
      <div class="social-snippet">Epic Games blocks reliable server-side store search requests from this backend, so this section opens the official store search directly.</div>
    </article>
  ` : '';

  const warningList = Object.values(errors).map(msg => `<li>${escapeHtml(msg)}</li>`).join('');

  $('socialList').innerHTML = `
    ${warningList ? `<ul class="social-warnings">${warningList}</ul>` : ''}
    <section class="social-group">
      <h3 class="social-heading">YouTube</h3>
      ${youtubeHtml || '<p class="no-results">No YouTube results.</p>'}
    </section>
    <section class="social-group">
      <h3 class="social-heading">Reddit</h3>
      ${redditHtml || '<p class="no-results">No Reddit results.</p>'}
    </section>
    <section class="social-group">
      <h3 class="social-heading">Wikipedia</h3>
      ${wikiHtml || '<p class="no-results">No Wikipedia results.</p>'}
    </section>
    <section class="social-group">
      <h3 class="social-heading">Modrinth</h3>
      ${modrinthHtml || '<p class="no-results">No Modrinth results.</p>'}
    </section>
    <section class="social-group">
      <h3 class="social-heading">CurseForge</h3>
      ${curseforgeHtml || '<p class="no-results">No CurseForge results.</p>'}
    </section>
    <section class="social-group">
      <h3 class="social-heading">Steam</h3>
      ${steamHtml || '<p class="no-results">No Steam results.</p>'}
    </section>
    <section class="social-group">
      <h3 class="social-heading">Epic Games</h3>
      ${epicGamesHtml || '<p class="no-results">Epic Games search link unavailable.</p>'}
    </section>
  `;
}

/* ═══════════════════════════════════════════════════════════════════════════
   AI TAB — Ollama (model: phi)
   ══════════════════════════════════════════════════════════════════════════ */

async function searchAi(query) {
  const summary = $('aiSummary');
  const answerEl = $('aiAnswer');

  if (!summary || !answerEl) return;

  const normalizedQuery = (query || '').trim();
  if (!normalizedQuery) {
    summary.textContent = 'Enter a search query to see an AI answer.';
    answerEl.innerHTML = '';
    return;
  }

  summary.textContent = `AI answer for "${normalizedQuery}"`;
  answerEl.innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';

  try {
    const res = await fetch(`${API}/ai`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ query: normalizedQuery }),
    });
    if (!res.ok) {
      let message = 'AI request failed.';
      try {
        const text = await res.text();
        try {
          const data = JSON.parse(text);
          message = data?.message || message;
        } catch {
          message = text || message;
        }
      } catch {
        /* ignore */
      }
      throw new Error(message);
    }

    if (!res.body) {
      throw new Error('AI response stream is unavailable.');
    }

    answerEl.innerHTML = '<div class="ai-answer-text" id="aiAnswerText"></div>';
    const outputEl = $('aiAnswerText');
    if (!outputEl) return;

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      fullText += decoder.decode(value, { stream: true });
      outputEl.textContent = fullText;
    }

    fullText += decoder.decode();
    outputEl.textContent = fullText.trim() || 'Model returned an empty answer.';
  } catch (err) {
    summary.textContent = 'AI request failed';
    answerEl.innerHTML = `<p class="no-results">${escapeHtml(err?.message || 'Could not connect to AI backend.')}</p>`;
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   MODAL
   ══════════════════════════════════════════════════════════════════════════ */

let statusPoller = null;

function initCrawlModal() {
  $('openCrawlModal').addEventListener('click', openModal);
  $('closeCrawlModal').addEventListener('click', closeModal);
  $('closeCrawlModalFooter').addEventListener('click', closeModal);
  $('crawlModal').addEventListener('click', e => {
    if (e.target === $('crawlModal')) closeModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !$('crawlModal').classList.contains('hidden')) closeModal();
  });
  $('addUrlBtn').addEventListener('click', addUrls);
  $('backfillMediaBtn').addEventListener('click', backfillMedia);
  $('refreshQueueBtn').addEventListener('click', refreshQueue);
  $('startCrawlBtn').addEventListener('click', startCrawl);
  $('queueList').addEventListener('click', onQueueListClick);
}

function openModal() {
  if (!$('crawlModal')) return;
  show($('crawlModal'));
  refreshQueue();
  if (isAdminPage && !statusPoller) statusPoller = setInterval(fetchStatus, 1000);
}

function closeModal() {
  if ($('crawlModal')) hide($('crawlModal'));
  clearInterval(statusPoller);
  statusPoller = null;
}

/* ── Add URLs (multi-line textarea) ───────────────────────────────────── */

async function addUrls() {
  const raw      = ($('urlsInput').value || '');
  const urls     = raw.split('\n').map(u => u.trim()).filter(u => u.length > 0);
  const feedback = $('addUrlFeedback');

  if (!urls.length) return;

  let added = 0, dupes = 0, invalid = 0;
  const dupeDomains = new Set();

  for (const url of urls) {
    try {
      const res  = await fetch(`${API}/crawl`, {
        method:  'POST',
        headers: jsonHeaders(),
        body:    JSON.stringify({ url }),
      });
      const data = await res.json();
      if (data.success) {
        added++;
      } else {
        dupes++;
        // Extract domain from the URL to offer recrawl
        try {
          const host = new URL(url.startsWith('http') ? url : 'https://' + url).hostname;
          if (host) dupeDomains.add(host);
        } catch { /* ignore */ }
      }
    } catch {
      invalid++;
    }
  }

  const parts = [];
  if (added)   parts.push(`${added} added`);
  if (dupes)   parts.push(`${dupes} skipped (duplicate)`);
  if (invalid) parts.push(`${invalid} failed`);
  const ok = added > 0;

  feedback.textContent = parts.join(' · ') || 'No URLs processed';
  feedback.className   = 'add-url-feedback ' + (ok ? 'success' : 'error');
  show(feedback);

  if (ok) {
    $('urlsInput').value = '';
    await refreshQueue();
  }

  // If there were duplicates, offer to recrawl the domain(s)
  if (dupes > 0 && dupeDomains.size > 0) {
    const domainList = [...dupeDomains].join(', ');
    setTimeout(async () => {
      if (!confirm(`${dupes} URL-i on juba crawlitud (${domainList}).\nKas soovid need domeenid uuesti crawlida?`)) return;
      for (const domain of dupeDomains) {
        try {
          const checkRes = await fetch(`${API}/admin/domains/check?domain=${encodeURIComponent(domain)}`, { headers: jsonHeaders() });
          const checkData = await checkRes.json();
          if (!checkData.success || checkData.count === 0) continue;
          await fetch(`${API}/admin/domains/recrawl`, {
            method: 'POST',
            headers: jsonHeaders(),
            body: JSON.stringify({ domain }),
          });
        } catch { /* silently ignore per-domain errors */ }
      }
      feedback.textContent = `Recrawl lisatud: ${domainList}`;
      feedback.className = 'add-url-feedback success';
      show(feedback);
      await refreshQueue();
    }, 200);
  }

  setTimeout(() => hide(feedback), 4500);
}


async function backfillMedia() {
  const feedback = $('addUrlFeedback');
  try {
    const res = await fetch(`${API}/backfill-media`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({}),
    });
    const data = await res.json();
    feedback.textContent = data.message;
    feedback.className = 'add-url-feedback ' + (data.success ? 'success' : 'error');
    show(feedback);
    refreshQueue();
    fetchStatus();
  } catch {
    feedback.textContent = 'Could not start media backfill.';
    feedback.className = 'add-url-feedback error';
    show(feedback);
  }

  setTimeout(() => hide(feedback), 4500);
}

/* ── Queue ────────────────────────────────────────────────────────────── */


async function refreshQueue() {
  try {
    const res  = await fetch(`${API}/queue`, isAdminPage ? { headers: jsonHeaders() } : undefined);
    const data = await res.json();
    renderQueue(data.queue ?? []);
  } catch { /* silently ignore */ }
}

function renderQueue(queue) {
  $('queueCount').textContent = queue.length;

  if (!queue.length) {
    $('queueList').innerHTML = '<li class="queue-empty">No URLs queued</li>';
    return;
  }

  $('queueList').innerHTML = queue.map(({ url, depth }) =>
    `<li class="queue-item" title="${escapeHtml(url)}">
       <span class="queue-url"><span class="queue-depth">[d${depth}]</span>&nbsp;${escapeHtml(url)}</span>
       <button class="queue-remove-btn" data-url="${escapeHtml(url)}" type="button">Remove</button>
     </li>`
  ).join('');
}

function onQueueListClick(e) {
  const btn = e.target.closest('.queue-remove-btn');
  if (!btn) return;
  const url = btn.getAttribute('data-url') || '';
  removeQueueUrl(url);
}

async function removeQueueUrl(url) {
  if (!url) return;
  const feedback = $('addUrlFeedback');

  try {
    const res = await fetch(`${API}/queue`, {
      method: 'DELETE',
      headers: jsonHeaders(),
      body: JSON.stringify({ url }),
    });
    const data = await res.json();

    feedback.textContent = data.message;
    feedback.className = 'add-url-feedback ' + (data.success ? 'success' : 'error');
    show(feedback);
    refreshQueue();
  } catch {
    feedback.textContent = 'Could not remove URL from queue.';
    feedback.className = 'add-url-feedback error';
    show(feedback);
  }

  setTimeout(() => hide(feedback), 2500);
}

/* ── Start crawl ──────────────────────────────────────────────────────── */


async function startCrawl() {
  const max_depth = parseInt($('depthSelect').value, 10);
  $('startCrawlBtn').disabled = true;

  try {
    const res  = await fetch(`${API}/start-crawl`, {
      method:  'POST',
      headers: jsonHeaders(),
      body:    JSON.stringify({ max_depth }),
    });
    const data = await res.json();

    if (data.success) {
      show($('crawlLog'));
    } else {
      $('startCrawlBtn').disabled = false;
      setStatus('idle', data.message);
    }
    await fetchStatus();
  } catch {
    $('startCrawlBtn').disabled = false;
    setStatus('error', 'Error connecting to server.');
  }
}

/* ── Crawl status ─────────────────────────────────────────────────────── */

async function fetchStatus() {
  if (!isAdminPage) return;
  try {
    const res  = await fetch(`${API}/crawl-status`);
    const data = await res.json();
    updateStatusUI(data);
  } catch { /* silently ignore */ }
}

function updateStatusUI(data) {
  const { status, current_url, crawled_count, results = [], total_pages = 0 } = data;

  // Header badge — always visible when crawling, even with modal closed
  const badge = $('crawlBadge');
  if (badge) {
    if (status === 'crawling') {
      badge.textContent = `Crawling… (${crawled_count} done)`;
      show(badge);
    } else {
      hide(badge);
    }
  }

  const crawledPagesCount = $('crawledPagesCount');
  if (crawledPagesCount) {
    crawledPagesCount.textContent = String(total_pages || 0);
  }

  // Only update modal UI if modal is currently open
  const modal = $('crawlModal');
  if ((!modal || modal.classList.contains('hidden')) && !document.body.classList.contains('admin-page')) return;

  const startBtn  = $('startCrawlBtn');
  const crawlNote = $('crawlNote');

  if (status === 'crawling') {
    const label = current_url
      ? `Crawling: ${current_url.length > 55 ? current_url.slice(0, 55) + '…' : current_url}`
      : 'Crawling…';
    setStatus('crawling', label);
    if (startBtn) startBtn.disabled = true;
    if (crawlNote) { crawlNote.textContent = 'Crawl running — you can still add more URLs below.'; show(crawlNote); }
    refreshQueue();

  } else if (status === 'done') {
    setStatus('done', `Done — ${crawled_count} page${crawled_count !== 1 ? 's' : ''} crawled`);
    if (startBtn) startBtn.disabled = false;
    if (crawlNote) hide(crawlNote);

  } else {
    setStatus('idle', 'Idle');
    if (startBtn) startBtn.disabled = false;
    if (crawlNote) hide(crawlNote);
  }

  if (results.length) {
    const log = $('crawlLog');
    if (log) {
      const signature = results.map(r => `${r.success ? 1 : 0}:${r.url}:${r.message}`).join('|');
      if (signature !== _lastCrawlLogSignature) {
        show(log);
        log.innerHTML = results.map(r => {
          const cls  = r.success ? 'log-ok' : 'log-err';
          const icon = r.success ? '✓' : '✗';
          const url  = r.url.length > 58 ? r.url.slice(0, 58) + '…' : r.url;
          return `<div class="${cls}">${icon}&nbsp;${escapeHtml(url)}&nbsp;—&nbsp;${escapeHtml(r.message)}</div>`;
        }).join('');
        _lastCrawlLogSignature = signature;
      }
      log.scrollTop = log.scrollHeight;
    }
  }
}

function initAdminPage() {
  initAdminSettingsPanel();
  initCrawlControlsForAdminPage();
  initSearchHistoryAdmin();
  initRecommendedAdmin();
  initHiddenDomainsAdmin();
  fetchStatus();
  refreshQueue();
  loadAdminSettings();
  loadSearchHistory();
  loadRecommendedEntries();
  loadHiddenDomains();

  if (!statusPoller) statusPoller = setInterval(fetchStatus, 3000);
}

async function saveAdminSettings() {
  const field = $('aiModelInput');
  const button = $('saveAdminSettingsBtn');
  const feedback = $('adminSettingsFeedback');
  const model = field.value.trim();
  if (!model) {
    feedback.textContent = 'Enter an AI model name.';
    feedback.className = 'admin-feedback error';
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`${API}/admin/settings`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ai_model: model}),
    });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.message || 'Could not save settings.');
    feedback.textContent = 'Settings saved.';
    feedback.className = 'admin-feedback success';
  } catch (error) {
    feedback.textContent = error instanceof SyntaxError ? 'Sign in to DodoStack and try again.' : error.message;
    feedback.className = 'admin-feedback error';
  } finally {
    button.disabled = false;
  }
}

function initAdminSettingsPanel() {
  if ($('saveAdminSettingsBtn')) {
    $('saveAdminSettingsBtn').addEventListener('click', saveAdminSettings);
  }
}

async function loadAdminSettings() {
  if (!$('aiModelInput')) return;
  try {
    const res = await fetch(`${API}/admin/settings`, { headers: jsonHeaders() });
    if (!res.ok) throw new Error("Could not load settings");
    const data = await res.json();
    if (data.success) $('aiModelInput').value = data.ai_model || '';
  } catch {
    const fb = $('adminSettingsFeedback');
    if (fb) {
      fb.textContent = 'Seadete laadimine ebaõnnestus';
      fb.className = 'admin-feedback error';
      show(fb);
    }
  }
}

function initCrawlControlsForAdminPage() {
  if ($('addUrlBtn')) $('addUrlBtn').addEventListener('click', addUrls);
  if ($('backfillMediaBtn')) $('backfillMediaBtn').addEventListener('click', backfillMedia);
  if ($('refreshQueueBtn')) $('refreshQueueBtn').addEventListener('click', refreshQueue);
  if ($('startCrawlBtn')) $('startCrawlBtn').addEventListener('click', startCrawl);
  if ($('queueList')) $('queueList').addEventListener('click', onQueueListClick);
}

function initSearchHistoryAdmin() {
  if ($('addHistoryBtn')) $('addHistoryBtn').addEventListener('click', addSearchHistoryEntry);
  if ($('clearSearchHistoryBtn')) $('clearSearchHistoryBtn').addEventListener('click', clearSearchHistory);
  if ($('historySearchInput')) {
    $('historySearchInput').addEventListener('input', () => {
      renderSearchHistoryList(_adminHistoryItems);
    });
  }
  if ($('historySortSelect')) {
    $('historySortSelect').addEventListener('change', () => {
      renderSearchHistoryList(_adminHistoryItems);
    });
  }
  if ($('historyList')) {
    $('historyList').addEventListener('click', event => {
      const button = event.target.closest('[data-delete-query]');
      if (!button) return;
      deleteSearchHistoryEntry(button.getAttribute('data-delete-query') || '');
    });
  }
}

async function loadSearchHistory() {
  const list = $('historyList');
  if (!list) return;

  try {
    const res = await fetch(`${API}/admin/search-history?limit=200`, { headers: jsonHeaders() });
    const data = await res.json();
    if (!data.success) throw new Error(data.message || 'Load failed');
    _adminHistoryItems = data.items || [];
    renderSearchHistoryList(_adminHistoryItems);
  } catch {
    list.innerHTML = '<div class="history-empty">History laadimine ebaõnnestus.</div>';
  }
}

function renderSearchHistoryList(items) {
  const list = $('historyList');
  if (!list) return;
  const searchQuery = ($('historySearchInput')?.value || '').trim().toLowerCase();
  const sortMode = $('historySortSelect')?.value || 'recent';
  const filteredItems = !searchQuery
    ? items
    : items.filter(item => String(item.query || '').toLowerCase().includes(searchQuery));

  const sortedItems = [...filteredItems].sort((left, right) => {
    if (sortMode === 'count') {
      const countDiff = Number(right.search_count || 0) - Number(left.search_count || 0);
      if (countDiff !== 0) return countDiff;
    } else if (sortMode === 'query') {
      return String(left.query || '').localeCompare(String(right.query || ''), 'et', { sensitivity: 'base' });
    }

    const rightTime = Date.parse(String(right.last_searched || '')) || 0;
    const leftTime = Date.parse(String(left.last_searched || '')) || 0;
    const timeDiff = rightTime - leftTime;
    if (timeDiff !== 0) return timeDiff;
    return String(left.query || '').localeCompare(String(right.query || ''), 'et', { sensitivity: 'base' });
  });

  if (!sortedItems.length) {
    list.innerHTML = `<div class="history-empty">${searchQuery ? 'Ühtegi sobivat history kirjet ei leitud.' : 'History puudub.'}</div>`;
    return;
  }

  list.innerHTML = sortedItems.map(item => {
    const query = escapeHtml(item.query || '');
    const count = Number(item.search_count || 0);
    const lastSearched = item.last_searched ? escapeHtml(String(item.last_searched).replace('T', ' ')) : '-';
    return `<article class="history-item">
      <div class="history-item-main">
        <div class="history-item-query">${query}</div>
        <div class="history-item-meta">
          <span class="history-item-count">${count} korda</span>
          <span class="history-item-date">${lastSearched}</span>
        </div>
      </div>
      <button type="button" class="btn-secondary history-delete-btn" data-delete-query="${query}">Kustuta</button>
    </article>`;
  }).join('');
}

async function addSearchHistoryEntry() {
  const query = ($('historyQueryInput')?.value || '').trim();
  const count = parseInt(($('historyCountInput')?.value || '1'), 10);
  const fb = $('historyFeedback');
  if (!query || !fb) return;

  try {
    const res = await fetch(`${API}/admin/search-history`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ query, count: Number.isFinite(count) ? count : 1 }),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Kirje lisatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) {
      $('historyQueryInput').value = '';
      $('historyCountInput').value = '1';
      loadSearchHistory();
    }
  } catch {
    fb.textContent = 'History kirje lisamine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

async function deleteSearchHistoryEntry(query) {
  const fb = $('historyFeedback');
  if (!query || !fb) return;

  try {
    const res = await fetch(`${API}/admin/search-history`, {
      method: 'DELETE',
      headers: jsonHeaders(),
      body: JSON.stringify({ query }),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Kirje kustutatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) loadSearchHistory();
  } catch {
    fb.textContent = 'History kirje kustutamine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

async function clearSearchHistory() {
  const fb = $('historyFeedback');
  if (!fb) return;

  try {
    const res = await fetch(`${API}/admin/search-history`, {
      method: 'DELETE',
      headers: jsonHeaders(),
      body: JSON.stringify({}),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'History kustutatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) loadSearchHistory();
  } catch {
    fb.textContent = 'Search history puhastamine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

function initRecommendedAdmin() {
  if ($('addRecommendedBtn')) $('addRecommendedBtn').addEventListener('click', addRecommendedEntry);
  if ($('clearRecommendedBtn')) $('clearRecommendedBtn').addEventListener('click', clearRecommendedEntries);
  if ($('recommendedSearchInput')) {
    $('recommendedSearchInput').addEventListener('input', () => {
      renderRecommendedAdminList(_adminRecommendedItems);
    });
  }
  if ($('recommendedAdminList')) {
    $('recommendedAdminList').addEventListener('click', event => {
      const button = event.target.closest('[data-delete-recommended-id]');
      if (!button) return;
      const id = Number(button.getAttribute('data-delete-recommended-id'));
      if (!Number.isFinite(id)) return;
      deleteRecommendedEntry(id);
    });
  }
}

async function loadRecommendedEntries() {
  const list = $('recommendedAdminList');
  if (!list) return;

  try {
    const res = await fetch(`${API}/admin/recommended?limit=500`, { headers: jsonHeaders() });
    const data = await res.json();
    if (!data.success) throw new Error(data.message || 'Load failed');
    _adminRecommendedItems = data.items || [];
    renderRecommendedAdminList(_adminRecommendedItems);
  } catch {
    list.innerHTML = '<div class="history-empty">Recommended laadimine ebaõnnestus.</div>';
  }
}

function renderRecommendedAdminList(items) {
  const list = $('recommendedAdminList');
  if (!list) return;

  const filterText = ($('recommendedSearchInput')?.value || '').trim().toLowerCase();
  const filteredItems = !filterText
    ? items
    : items.filter(item => {
      const hay = `${item.query || ''} ${item.title || ''} ${item.url || ''}`.toLowerCase();
      return hay.includes(filterText);
    });

  if (!filteredItems.length) {
    list.innerHTML = `<div class="history-empty">${filterText ? 'Ühtegi sobivat recommended kirjet ei leitud.' : 'Recommended kirjeid ei ole.'}</div>`;
    return;
  }

  list.innerHTML = filteredItems.map(item => {
    const id = Number(item.id || 0);
    const query = escapeHtml(item.query || '');
    const title = escapeHtml(item.title || '(ilma pealkirjata)');
    const url = escapeHtml(item.url || '');
    return `<article class="history-item">
      <div class="history-item-main">
        <div class="history-item-query">${query}</div>
        <div class="history-item-meta"><span>${title}</span></div>
        <div class="history-item-meta"><span>${url}</span></div>
      </div>
      <button type="button" class="btn-secondary history-delete-btn" data-delete-recommended-id="${id}">Kustuta</button>
    </article>`;
  }).join('');
}

async function addRecommendedEntry() {
  const query = ($('recommendedQueryInput')?.value || '').trim();
  const title = ($('recommendedTitleInput')?.value || '').trim();
  const url = ($('recommendedUrlInput')?.value || '').trim();
  const fb = $('recommendedFeedback');
  if (!query || !url || !fb) return;

  try {
    const res = await fetch(`${API}/admin/recommended`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ query, title, url }),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Recommended kirje lisatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) {
      $('recommendedQueryInput').value = '';
      $('recommendedTitleInput').value = '';
      $('recommendedUrlInput').value = '';
      loadRecommendedEntries();
    }
  } catch {
    fb.textContent = 'Recommended kirje lisamine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

async function deleteRecommendedEntry(id) {
  const fb = $('recommendedFeedback');
  if (!Number.isFinite(id) || !fb) return;

  try {
    const res = await fetch(`${API}/admin/recommended`, {
      method: 'DELETE',
      headers: jsonHeaders(),
      body: JSON.stringify({ id }),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Recommended kirje kustutatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) loadRecommendedEntries();
  } catch {
    fb.textContent = 'Recommended kirje kustutamine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

async function clearRecommendedEntries() {
  const fb = $('recommendedFeedback');
  if (!fb) return;

  try {
    const res = await fetch(`${API}/admin/recommended`, {
      method: 'DELETE',
      headers: jsonHeaders(),
      body: JSON.stringify({}),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Recommended kustutatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) loadRecommendedEntries();
  } catch {
    fb.textContent = 'Recommended puhastamine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

/* ── Hidden domains ────────────────────────────────────────────────────── */

function initHiddenDomainsAdmin() {
  if ($('deleteDomainBtn')) $('deleteDomainBtn').addEventListener('click', deleteDomain);
  if ($('deleteDomainInput')) {
    $('deleteDomainInput').addEventListener('keydown', e => { if (e.key === 'Enter') deleteDomain(); });
  }
  if ($('recrawlDomainBtn')) $('recrawlDomainBtn').addEventListener('click', recrawlDomain);
  if ($('recrawlDomainInput')) {
    $('recrawlDomainInput').addEventListener('keydown', e => { if (e.key === 'Enter') recrawlDomain(); });
  }
  if ($('addHiddenDomainBtn')) $('addHiddenDomainBtn').addEventListener('click', addHiddenDomain);
  if ($('hiddenDomainInput')) {
    $('hiddenDomainInput').addEventListener('keydown', e => {
      if (e.key === 'Enter') addHiddenDomain();
    });
  }
  if ($('hiddenDomainsList')) {
    $('hiddenDomainsList').addEventListener('click', event => {
      const btn = event.target.closest('[data-delete-domain]');
      if (!btn) return;
      removeHiddenDomain(btn.getAttribute('data-delete-domain') || '');
    });
  }
  if ($('exportDomainsBtn')) {
    $('exportDomainsBtn').addEventListener('click', () => {
      const a = document.createElement('a');
      a.href = `${API}/admin/domains/export`;
      fetch(a.href)
        .then(r => r.blob())
        .then(blob => {
          const url = URL.createObjectURL(blob);
          a.href = url;
          a.download = 'domains.txt';
          a.click();
          URL.revokeObjectURL(url);
        })
        .catch(() => alert('Domeenide eksport ebaõnnestus'));
    });
  }
}

async function deleteDomain() {
  const input = $('deleteDomainInput');
  const fb = $('deleteDomainFeedback');
  const domain = (input?.value || '').trim();
  if (!domain || !fb) return;
  if (!confirm(`Kustutad kõik "${domain}" lehed andmebaasist. Jätkata?`)) return;
  try {
    const res = await fetch(`${API}/admin/domains`, {
      method: 'DELETE',
      headers: jsonHeaders(),
      body: JSON.stringify({ domain }),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Kustutatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success && input) input.value = '';
  } catch {
    fb.textContent = 'Domeeni kustutamine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

async function recrawlDomain() {
  const input = $('recrawlDomainInput');
  const fb = $('recrawlDomainFeedback');
  const domain = (input?.value || '').trim();
  if (!domain || !fb) return;
  try {
    const checkRes = await fetch(`${API}/admin/domains/check?domain=${encodeURIComponent(domain)}`, { headers: jsonHeaders() });
    const checkData = await checkRes.json();
    if (!checkData.success || checkData.count === 0) {
      fb.textContent = `Domeenil "${checkData.domain || domain}" pole ühtegi lehte`;
      fb.className = 'admin-feedback error';
      show(fb);
      return;
    }
    if (!confirm(`Domeenil "${checkData.domain}" on ${checkData.count} lehte. Lisada kõik crawl-järjekorda?`)) return;
    const res = await fetch(`${API}/admin/domains/recrawl`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ domain }),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Järjekorda lisatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) {
      if (input) input.value = '';
      await refreshQueue();
    }
  } catch {
    fb.textContent = 'Recrawl ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

async function loadHiddenDomains() {
  const list = $('hiddenDomainsList');
  if (!list) return;
  try {
    const res = await fetch(`${API}/admin/hidden-domains`, { headers: jsonHeaders() });
    const data = await res.json();
    if (!data.success) throw new Error(data.message || 'Load failed');
    renderHiddenDomainsList(data.domains || []);
  } catch {
    list.innerHTML = '<div class="history-empty">Peidetud domeenide laadimine ebaõnnestus.</div>';
  }
}

function renderHiddenDomainsList(domains) {
  const list = $('hiddenDomainsList');
  if (!list) return;
  if (!domains.length) {
    list.innerHTML = '<div class="history-empty">Peidetud domeene pole.</div>';
    return;
  }
  list.innerHTML = domains.map(d => `
    <article class="history-item">
      <div class="history-item-main">
        <div class="history-item-query">${escapeHtml(d)}</div>
      </div>
      <button type="button" class="btn-secondary history-delete-btn" data-delete-domain="${escapeHtml(d)}">Kuva uuesti</button>
    </article>`).join('');
}

async function addHiddenDomain() {
  const input = $('hiddenDomainInput');
  const fb = $('hiddenDomainFeedback');
  const domain = (input?.value || '').trim();
  if (!domain || !fb) return;
  try {
    const res = await fetch(`${API}/admin/hidden-domains`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ domain }),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Peidetud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) {
      input.value = '';
      loadHiddenDomains();
    }
  } catch {
    fb.textContent = 'Domeeni peitmine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

async function removeHiddenDomain(domain) {
  const fb = $('hiddenDomainFeedback');
  if (!domain || !fb) return;
  try {
    const res = await fetch(`${API}/admin/hidden-domains`, {
      method: 'DELETE',
      headers: jsonHeaders(),
      body: JSON.stringify({ domain }),
    });
    const data = await res.json();
    fb.textContent = data.message || (data.success ? 'Domeen taastatud' : 'Viga');
    fb.className = 'admin-feedback ' + (data.success ? 'success' : 'error');
    show(fb);
    if (data.success) loadHiddenDomains();
  } catch {
    fb.textContent = 'Domeeni kuvamise taastamine ebaõnnestus';
    fb.className = 'admin-feedback error';
    show(fb);
  }
}

function setStatus(state, text) {
  const dot  = $('statusDot');
  const span = $('statusText');
  if (dot)  dot.dataset.state = state;
  if (span) span.textContent  = text;
}

/* ═══════════════════════════════════════════════════════════════════════════
   EASTER EGGS
   ══════════════════════════════════════════════════════════════════════════ */

function checkEasterEgg(q) {
  const panel = $('easterEggPanel');
  if (!panel) return;
  const raw = (q || '').trim().toLowerCase();

  const egg = detectEasterEgg(raw);
  if (egg) {
    panel.innerHTML = egg.html;
    panel.classList.remove('hidden');
    egg.init && egg.init(panel, q.trim());
  } else {
    panel.innerHTML = '';
    panel.classList.add('hidden');
  }
}

function detectEasterEgg(q) {
  if (/\beaster eggs?\b|\ball easter eggs?\b/.test(q)) return eggEasterEggsList();
  if (/\brickroll\b/.test(q))           return eggRickroll();
  if (/\btimer\b|\bstopwatch\b/.test(q)) return eggTimer();
  if (/\btyping test\b/.test(q))         return eggTypingTest();
  if (/\brock paper scissors\b/.test(q)) return eggRPS();
  if (/\bcolor picker\b/.test(q))        return eggColorPicker();
  if (/\bcalendar\b/.test(q))            return eggCalendar();
  if (/\bjoke\b/.test(q))               return eggJoke();
  if (/\bmemory game\b/.test(q))         return eggMemoryGame();
  if (/\bsnake\b/.test(q))              return eggSnake();
  if (/\bconvert\b/.test(q))            return eggConverter();
  if (/\bcalculator\b/.test(q))         return eggCalculator(q);
  if (/\bflip a coin\b|\bcoin flip\b/.test(q)) return eggCoinFlip();
  if (/\bdice\b/.test(q))               return eggRng(1, 6);
  if (/\brandom number\b|\brng\b/.test(q)) return eggRng();
  if (/\btic tac toe\b/.test(q))        return eggTicTacToe();
  if (/\bmagic 8 ball\b/.test(q))       return eggMagic8Ball();
  if (/\brandom name\b/.test(q))        return eggNamePicker();
  if (/\bpassword strength\b/.test(q))  return eggPasswordStrength();
  if (/\bpassword generator\b/.test(q)) return eggPasswordGenerator();
  if (/\bsudoku\b/.test(q))             return eggSudoku();
  if (/\bhash\b/.test(q))              return eggHash();
  if (/\busername generator\b/.test(q)) return eggUsernameGenerator();
  if (/^are you dumb\??$/.test(q))       return eggAreYouDumb();
  if (/^am i dumb\??$/.test(q))          return eggAmIDumb();

  // Calculator auto-detect: expression with numbers and operators
  if (/^[\d\s\+\-\*\/\%\^\(\)\.]+$/.test(q) && /[\+\-\*\/]/.test(q)) {
    return eggCalculator(q);
  }

  return null;
}

/* ── Helper: hook up after DOM insert ──────────────────────────────────── */
function afterInsert(fn) { return fn; }

/* ════════════════════════════════════════════════════════════════
   1. TIMER / STOPWATCH
   ════════════════════════════════════════════════════════════════ */
function eggTimer() {
  return {
    html: `<div class="ee-card">
      <h2>⏱️ Timer & Stopwatch</h2>
      <div class="ee-tabs">
        <button class="ee-tab-btn active" id="eeTimerTabTimer" onclick="eeTimerSwitch('timer')">Timer</button>
        <button class="ee-tab-btn" id="eeTimerTabSW" onclick="eeTimerSwitch('stopwatch')">Stopwatch</button>
      </div>
      <div id="eeTimerSection">
        <div class="ee-row">
          <input class="ee-input" type="number" id="eeTimerMins" placeholder="min" min="0" max="999" style="width:80px" />
          <input class="ee-input" type="number" id="eeTimerSecs" placeholder="sec" min="0" max="59" style="width:80px" />
          <button class="ee-btn green" onclick="eeTimerStart()">Start</button>
          <button class="ee-btn grey" onclick="eeTimerReset()">Reset</button>
        </div>
        <div class="ee-timer-display" id="eeTimerDisplay">00:00</div>
      </div>
      <div id="eeSWSection" class="hidden">
        <div class="ee-timer-display" id="eeSWDisplay">00:00.0</div>
        <div class="ee-row" style="justify-content:center">
          <button class="ee-btn green" id="eeSWStartBtn" onclick="eeSWToggle()">Start</button>
          <button class="ee-btn grey" onclick="eeSWReset()">Reset</button>
        </div>
      </div>
    </div>`,
    init() {
      window._eeTimerInterval = null;
      window._eeSWInterval = null;
      window._eeSWRunning = false;
      window._eeSWElapsed = 0;
      window._eeSWLast = 0;
      window._eeTimerRemaining = 0;
    }
  };
}
window.eeTimerSwitch = function(mode) {
  $('eeTimerSection').classList.toggle('hidden', mode !== 'timer');
  $('eeSWSection').classList.toggle('hidden',   mode !== 'stopwatch');
  $('eeTimerTabTimer').classList.toggle('active', mode === 'timer');
  $('eeTimerTabSW').classList.toggle('active',   mode === 'stopwatch');
};
window.eeTimerStart = function() {
  clearInterval(window._eeTimerInterval);
  const m = parseInt($('eeTimerMins')?.value || '0', 10) || 0;
  const s = parseInt($('eeTimerSecs')?.value || '0', 10) || 0;
  window._eeTimerRemaining = m * 60 + s;
  if (!window._eeTimerRemaining) return;
  function tick() {
    if (window._eeTimerRemaining <= 0) {
      clearInterval(window._eeTimerInterval);
      $('eeTimerDisplay').textContent = '⏰ Time!';
      return;
    }
    window._eeTimerRemaining--;
    const mm = String(Math.floor(window._eeTimerRemaining / 60)).padStart(2, '0');
    const ss = String(window._eeTimerRemaining % 60).padStart(2, '0');
    $('eeTimerDisplay').textContent = `${mm}:${ss}`;
  }
  tick();
  window._eeTimerInterval = setInterval(tick, 1000);
};
window.eeTimerReset = function() {
  clearInterval(window._eeTimerInterval);
  window._eeTimerRemaining = 0;
  $('eeTimerDisplay').textContent = '00:00';
};
window.eeSWToggle = function() {
  if (window._eeSWRunning) {
    clearInterval(window._eeSWInterval);
    window._eeSWRunning = false;
    window._eeSWElapsed += Date.now() - window._eeSWLast;
    $('eeSWStartBtn').textContent = 'Resume';
  } else {
    window._eeSWRunning = true;
    window._eeSWLast = Date.now();
    $('eeSWStartBtn').textContent = 'Stop';
    window._eeSWInterval = setInterval(() => {
      const total = window._eeSWElapsed + (Date.now() - window._eeSWLast);
      const mins = String(Math.floor(total / 60000)).padStart(2, '0');
      const secs = String(Math.floor((total % 60000) / 1000)).padStart(2, '0');
      const dec  = Math.floor((total % 1000) / 100);
      $('eeSWDisplay').textContent = `${mins}:${secs}.${dec}`;
    }, 100);
  }
};
window.eeSWReset = function() {
  clearInterval(window._eeSWInterval);
  window._eeSWRunning = false;
  window._eeSWElapsed = 0;
  $('eeSWDisplay').textContent = '00:00.0';
  $('eeSWStartBtn').textContent = 'Start';
};

/* ════════════════════════════════════════════════════════════════
   2. TYPING TEST
   ════════════════════════════════════════════════════════════════ */
const TYPING_TEXTS = [
  'The quick brown fox jumps over the lazy dog.',
  'Pack my box with five dozen liquor jugs.',
  'How vexingly quick daft zebras jump.',
  'The five boxing wizards jump quickly.',
  'Sphinx of black quartz, judge my vow.'
];
function eggTypingTest() {
  return {
    html: `<div class="ee-card">
      <h2>🔤 Typing Test</h2>
      <div class="ee-typing-text" id="eeTypingText"></div>
      <input class="ee-typing-input" id="eeTypingInput" placeholder="Start typing…" autocomplete="off" spellcheck="false" />
      <div class="ee-stats">
        <div class="ee-stat-item"><div class="ee-stat-val" id="eeTypingWPM">—</div><div class="ee-stat-label">WPM</div></div>
        <div class="ee-stat-item"><div class="ee-stat-val" id="eeTypingAcc">—</div><div class="ee-stat-label">Accuracy</div></div>
        <div class="ee-stat-item"><div class="ee-stat-val" id="eeTypingTime">—</div><div class="ee-stat-label">Time (s)</div></div>
      </div>
      <button class="ee-btn" style="margin-top:10px" onclick="eeTypingReset()">Restart</button>
    </div>`,
    init() { eeTypingReset(); }
  };
}
window.eeTypingReset = function() {
  const texts = TYPING_TEXTS;
  window._eeTypingTarget = texts[Math.floor(Math.random() * texts.length)];
  window._eeTypingStart = null;
  window._eeTypingDone = false;
  const el = $('eeTypingText');
  const inp = $('eeTypingInput');
  if (el) el.innerHTML = window._eeTypingTarget.split('').map(c => `<span>${escapeHtml(c)}</span>`).join('');
  if (inp) { inp.value = ''; inp.disabled = false; inp.focus(); }
  [$('eeTypingWPM'), $('eeTypingAcc'), $('eeTypingTime')].forEach(e => { if (e) e.textContent = '—'; });
  if (inp) inp.oninput = eeTypingInput;
};
window.eeTypingInput = function() {
  const inp = $('eeTypingInput');
  if (!inp || window._eeTypingDone) return;
  const typed = inp.value;
  const target = window._eeTypingTarget || '';
  if (!window._eeTypingStart && typed.length) window._eeTypingStart = Date.now();
  const spans = $('eeTypingText').querySelectorAll('span');
  spans.forEach((s, i) => {
    s.className = i < typed.length
      ? (typed[i] === target[i] ? 'correct' : 'wrong')
      : '';
  });
  if (typed.length >= target.length) {
    window._eeTypingDone = true;
    inp.disabled = true;
    const elapsed = (Date.now() - window._eeTypingStart) / 1000;
    const words = target.trim().split(' ').length;
    const wpm = Math.round((words / elapsed) * 60);
    let correct = 0;
    for (let i = 0; i < target.length; i++) if (typed[i] === target[i]) correct++;
    const acc = Math.round((correct / target.length) * 100);
    $('eeTypingWPM').textContent = wpm;
    $('eeTypingAcc').textContent = acc + '%';
    $('eeTypingTime').textContent = elapsed.toFixed(1);
  }
};

/* ════════════════════════════════════════════════════════════════
   3. ROCK PAPER SCISSORS
   ════════════════════════════════════════════════════════════════ */
function eggRPS() {
  return {
    html: `<div class="ee-card">
      <h2>🧠 Rock Paper Scissors</h2>
      <div class="ee-rps-choices">
        <button class="ee-rps-btn" onclick="eeRPSPlay('rock')" title="Rock">🪨</button>
        <button class="ee-rps-btn" onclick="eeRPSPlay('paper')" title="Paper">📄</button>
        <button class="ee-rps-btn" onclick="eeRPSPlay('scissors')" title="Scissors">✂️</button>
      </div>
      <div class="ee-rps-result" id="eeRPSResult"></div>
      <div class="ee-rps-score" id="eeRPSScore">Wins: 0 · Losses: 0 · Draws: 0</div>
    </div>`,
    init() { window._eeRPS = {w:0,l:0,d:0}; }
  };
}
window.eeRPSPlay = function(choice) {
  const opts = ['rock','paper','scissors'];
  const ai = opts[Math.floor(Math.random() * 3)];
  const em = {rock:'🪨', paper:'📄', scissors:'✂️'};
  const res = $('eeRPSResult');
  if (!window._eeRPS) window._eeRPS = {w:0,l:0,d:0};
  let outcome, cls;
  if (choice === ai) { outcome = `Draw! Both chose ${em[ai]}`; cls = 'draw'; window._eeRPS.d++; }
  else if ((choice==='rock'&&ai==='scissors')||(choice==='paper'&&ai==='rock')||(choice==='scissors'&&ai==='paper')) {
    outcome = `You win! ${em[choice]} beats ${em[ai]}`; cls = 'win'; window._eeRPS.w++;
  } else {
    outcome = `You lose! ${em[ai]} beats ${em[choice]}`; cls = 'lose'; window._eeRPS.l++;
  }
  res.textContent = outcome;
  res.className = `ee-rps-result ${cls}`;
  $('eeRPSScore').textContent = `Wins: ${window._eeRPS.w} · Losses: ${window._eeRPS.l} · Draws: ${window._eeRPS.d}`;
};

/* ════════════════════════════════════════════════════════════════
   4. COLOR PICKER
   ════════════════════════════════════════════════════════════════ */
function eggColorPicker() {
  return {
    html: `<div class="ee-card">
      <h2>🎨 Color Picker</h2>
      <div class="ee-color-preview" id="eeColorPreview" style="background:#4285f4"></div>
      <div class="ee-color-input-row">
        <input type="color" class="ee-color-native" id="eeColorNative" value="#4285f4" oninput="eeColorUpdate(this.value)" />
        <div class="ee-codes">
          <div class="ee-color-code" id="eeColorHex">HEX: #4285f4</div>
          <div class="ee-color-code" id="eeColorRgb">RGB: 66, 133, 244</div>
        </div>
        <button class="ee-btn ee-copy-btn" onclick="eeColorCopy()">Copy HEX</button>
      </div>
    </div>`,
    init() { eeColorUpdate('#4285f4'); }
  };
}
window.eeColorUpdate = function(hex) {
  const pr = $('eeColorPreview');
  const hexEl = $('eeColorHex');
  const rgbEl = $('eeColorRgb');
  if (pr) pr.style.background = hex;
  if (hexEl) hexEl.textContent = 'HEX: ' + hex.toUpperCase();
  if (rgbEl) {
    const r = parseInt(hex.slice(1,3),16);
    const g = parseInt(hex.slice(3,5),16);
    const b = parseInt(hex.slice(5,7),16);
    rgbEl.textContent = `RGB: ${r}, ${g}, ${b}`;
  }
  window._eeColorHex = hex;
};
window.eeColorCopy = function() {
  const h = window._eeColorHex || '#000000';
  navigator.clipboard?.writeText(h.toUpperCase());
};

/* ════════════════════════════════════════════════════════════════
   5. CALENDAR
   ════════════════════════════════════════════════════════════════ */
function eggCalendar() {
  return {
    html: `<div class="ee-card">
      <h2>📅 Calendar</h2>
      <div class="ee-calendar" id="eeCalendar"></div>
    </div>`,
    init() {
      window._eeCalDate = new Date();
      eeCalRender();
    }
  };
}
window.eeCalNav = function(dir) {
  window._eeCalDate.setMonth(window._eeCalDate.getMonth() + dir);
  eeCalRender();
};
function eeCalRender() {
  const d = window._eeCalDate;
  const today = new Date();
  const year = d.getFullYear(), month = d.getMonth();
  const first = new Date(year, month, 1).getDay();
  const days = new Date(year, month + 1, 0).getDate();
  const names = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  const dayNames = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  const todayLabel = month === today.getMonth() && year === today.getFullYear()
    ? `<div style="font-size:12.5px;color:var(--brand);margin-bottom:10px;text-align:center">📅 Today is ${dayNames[today.getDay()]}, ${monthNames[today.getMonth()]} ${today.getDate()}, ${today.getFullYear()}</div>`
    : '';
  let html = todayLabel + `<div class="ee-cal-header">
    <button class="ee-cal-nav" onclick="eeCalNav(-1)">‹</button>
    <span class="ee-cal-title">${monthNames[month]} ${year}</span>
    <button class="ee-cal-nav" onclick="eeCalNav(1)">›</button>
  </div>
  <div class="ee-cal-grid">`;
  names.forEach(n => { html += `<div class="ee-cal-day-name">${n}</div>`; });
  for (let i = 0; i < first; i++) html += `<div class="ee-cal-day other-month"></div>`;
  for (let i = 1; i <= days; i++) {
    const isToday = i === today.getDate() && month === today.getMonth() && year === today.getFullYear();
    html += `<div class="ee-cal-day${isToday ? ' today' : ''}">${i}</div>`;
  }
  html += '</div>';
  const el = $('eeCalendar');
  if (el) el.innerHTML = html;
}

/* ════════════════════════════════════════════════════════════════
   6. JOKE
   ════════════════════════════════════════════════════════════════ */
const JOKES = [
  {setup:"Why don't scientists trust atoms?",  punchline:"Because they make up everything!"},
  {setup:"Why did the scarecrow win an award?", punchline:"Because he was outstanding in his field!"},
  {setup:"I'm reading a book about anti-gravity.", punchline:"It's impossible to put down."},
  {setup:"Why did the bicycle fall over?",  punchline:"Because it was two-tired."},
  {setup:"What do you call fake spaghetti?", punchline:"An impasta."},
  {setup:"Why can't you give Elsa a balloon?", punchline:"Because she'll let it go."},
  {setup:"I told a joke about construction.", punchline:"I'm still working on it."},
  {setup:"Why do programmers prefer dark mode?", punchline:"Because light attracts bugs!"},
  {setup:"I asked my dog what 2 minus 2 is.", punchline:"He said nothing."},
  {setup:"Why don't eggs tell jokes?", punchline:"They'd crack each other up."},
  {setup:"What do you call a fish without eyes?", punchline:"A fsh."},
  {setup:"I only know 25 letters of the alphabet.", punchline:"I don't know why."},
  {setup:"Did you hear about the mathematician who's afraid of negative numbers?", punchline:"He'll stop at nothing to avoid them."},
  {setup:"Why is Peter Pan always flying?", punchline:"He Neverlands."},
  {setup:"I'm on a seafood diet.", punchline:"I see food and I eat it."},
  {setup:"What do you call a sleeping dinosaur?", punchline:"A dino-snore."},
  {setup:"Why can't a leopard hide?", punchline:"Because he's always spotted."},
  {setup:"How does NASA organize a party?", punchline:"They planet."},
  {setup:"I told a joke about a UDP packet.", punchline:"You might not get it."},
  {setup:"Why did the developer go broke?", punchline:"Because he used up all his cache."},
  {setup:"A SQL query walks into a bar, walks up to two tables and asks…", punchline:"'Can I join you?'"},
  {setup:"Why was the JavaScript developer sad?", punchline:"Because he didn't Node how to Express himself."},
  {setup:"What's a computer's favorite snack?", punchline:"Microchips."},
  {setup:"Why do cows wear bells?", punchline:"Because their horns don't work."},
  {setup:"Why did the chicken join a band?", punchline:"Because it had the drumsticks."},
  {setup:"What do you call cheese that isn't yours?", punchline:"Nacho cheese."},
  {setup:"Why did the tomato turn red?", punchline:"Because it saw the salad dressing!"},
  {setup:"What do you call a bear with no teeth?", punchline:"A gummy bear."},
  {setup:"Why don't skeletons fight each other?", punchline:"They don't have the guts."},
  {setup:"What do you call an alligator in a vest?", punchline:"An investigator."},
  {setup:"Why did the golfer bring two pairs of pants?", punchline:"In case he got a hole in one."},
  {setup:"What do you call a pile of cats?", punchline:"A meowntain."},
  {setup:"Why did the coffee file a police report?", punchline:"It got mugged."},
  {setup:"What do you call a snowman with a six-pack?", punchline:"An abdominal snowman."},
  {setup:"Why did the cookie go to the hospital?", punchline:"Because it felt crummy."},
];
function eggJoke() {
  return {
    html: `<div class="ee-card">
      <h2>😂 Random Joke</h2>
      <div id="eeJokeSetup" class="ee-joke-text"></div>
      <div id="eeJokePunchline" class="ee-joke-punchline hidden"></div>
      <div class="ee-row" style="margin-top:14px">
        <button class="ee-btn" onclick="eeJokeReveal()">Show punchline</button>
        <button class="ee-btn grey" onclick="eeJokeNext()">Another one</button>
      </div>
    </div>`,
    init() { eeJokeNext(); }
  };
}
window.eeJokeNext = function() {
  const j = JOKES[Math.floor(Math.random() * JOKES.length)];
  const s = $('eeJokeSetup'), p = $('eeJokePunchline');
  if (s) s.textContent = j.setup;
  if (p) { p.textContent = j.punchline; p.classList.add('hidden'); }
  window._eeCurrentJoke = j;
};
window.eeJokeReveal = function() {
  const p = $('eeJokePunchline');
  if (p) p.classList.remove('hidden');
};

/* ════════════════════════════════════════════════════════════════
   7. MEMORY GAME
   ════════════════════════════════════════════════════════════════ */
function eggMemoryGame() {
  return {
    html: `<div class="ee-card">
      <h2>🧩 Memory Game</h2>
      <div class="ee-memory-grid" id="eeMemoryGrid"></div>
      <div class="ee-memory-status" id="eeMemoryStatus"></div>
      <button class="ee-btn grey" style="margin-top:10px" onclick="eeMemoryInit()">New Game</button>
    </div>`,
    init() { eeMemoryInit(); }
  };
}
window.eeMemoryInit = function() {
  const emojis = ['🍎','🍊','🍋','🍇','🍓','🎸','🎺','🎮'];
  const cards = [...emojis, ...emojis].sort(() => Math.random() - .5);
  window._eeMemory = { cards, flipped: [], matched: new Set(), moves: 0, lock: false, startTime: null };
  const grid = $('eeMemoryGrid');
  if (!grid) return;
  grid.innerHTML = cards.map((c, i) => `<button class="ee-memory-card" data-idx="${i}" onclick="eeMemoryFlip(${i})"><span class="ee-card-inner">${c}</span></button>`).join('');
  $('eeMemoryStatus').textContent = 'Flip two cards to match them!';
};
window.eeMemoryFlip = function(idx) {
  const m = window._eeMemory;
  if (!m || m.lock) return;
  if (m.matched.has(idx) || m.flipped.includes(idx)) return;
  const cells = $('eeMemoryGrid').querySelectorAll('.ee-memory-card');
  if (!m.startTime) m.startTime = Date.now();
  cells[idx].classList.add('flipped');
  m.flipped.push(idx);
  if (m.flipped.length === 2) {
    m.lock = true;
    m.moves++;
    const [a, b] = m.flipped;
    if (m.cards[a] === m.cards[b]) {
      m.matched.add(a); m.matched.add(b);
      cells[a].classList.add('matched'); cells[b].classList.add('matched');
      m.flipped = []; m.lock = false;
      if (m.matched.size === m.cards.length) {
        const elapsed = ((Date.now() - m.startTime) / 1000).toFixed(1);
        $('eeMemoryStatus').textContent = `🎉 Solved in ${m.moves} moves and ${elapsed}s!`;
      } else {
        $('eeMemoryStatus').textContent = `Moves: ${m.moves}`;
      }
    } else {
      $('eeMemoryStatus').textContent = `Moves: ${m.moves}`;
      setTimeout(() => {
        cells[a].classList.remove('flipped'); cells[b].classList.remove('flipped');
        m.flipped = []; m.lock = false;
      }, 900);
    }
  }
};

/* ════════════════════════════════════════════════════════════════
   8. SNAKE GAME
   ════════════════════════════════════════════════════════════════ */
function eggSnake() {
  return {
    html: `<div class="ee-card">
      <h2>🐍 Snake</h2>
      <div class="ee-snake-wrap">
        <canvas id="ee-snake-canvas" width="300" height="300"></canvas>
        <div class="ee-snake-info" id="eeSnakeInfo">Press any arrow key or WASD to start · Score: <span id="eeSnakeScore">0</span></div>
        <button class="ee-btn grey" onclick="eeSnakeInit()">New Game</button>
      </div>
    </div>`,
    init() { eeSnakeInit(); }
  };
}
window.eeSnakeInit = function() {
  const CELL = 15, COLS = 20, ROWS = 20;
  const canvas = $('ee-snake-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let snake = [{x:10,y:10}];
  let dir = {x:1,y:0};
  let nextDir = {x:1,y:0};
  let food = randomFood();
  let score = 0;
  let running = true;

  function randomFood() {
    return {x: Math.floor(Math.random()*COLS), y: Math.floor(Math.random()*ROWS)};
  }
  function draw() {
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0,0,300,300);
    snake.forEach((s, i) => {
      ctx.fillStyle = i === 0 ? '#ff4444' : '#4ec9b0';
      ctx.fillRect(s.x*CELL, s.y*CELL, CELL-1, CELL-1);
    });
    ctx.fillStyle = '#f48771';
    ctx.beginPath();
    ctx.arc(food.x*CELL+CELL/2, food.y*CELL+CELL/2, CELL/2-1, 0, Math.PI*2);
    ctx.fill();
  }
  function update() {
    if (!running) return;
    dir = nextDir;
    const head = {x: snake[0].x + dir.x, y: snake[0].y + dir.y};
    if (head.x < 0 || head.x >= COLS || head.y < 0 || head.y >= ROWS) { gameOver(); return; }
    if (snake.some(s => s.x === head.x && s.y === head.y)) { gameOver(); return; }
    snake.unshift(head);
    if (head.x === food.x && head.y === food.y) {
      score++;
      $('eeSnakeScore').textContent = score;
      food = randomFood();
    } else {
      snake.pop();
    }
    draw();
  }
  function gameOver() {
    running = false;
    ctx.fillStyle = 'rgba(0,0,0,.6)';
    ctx.fillRect(0,0,300,300);
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 24px Segoe UI';
    ctx.textAlign = 'center';
    ctx.fillText('Game Over', 150, 140);
    ctx.font = '16px Segoe UI';
    ctx.fillText(`Score: ${score}`, 150, 170);
  }
  clearInterval(window._eeSnakeInterval);
  window._eeSnakeInterval = setInterval(update, 120);
  draw();
  const keyHandler = e => {
    const map = {ArrowUp:{x:0,y:-1},ArrowDown:{x:0,y:1},ArrowLeft:{x:-1,y:0},ArrowRight:{x:1,y:0},
                 w:{x:0,y:-1},s:{x:0,y:1},a:{x:-1,y:0},d:{x:1,y:0}};
    const nd = map[e.key];
    if (!nd) return;
    if (nd.x === -dir.x && nd.y === -dir.y) return;
    nextDir = nd;
    e.preventDefault();
  };
  document.removeEventListener('keydown', window._eeSnakeKeyHandler);
  window._eeSnakeKeyHandler = keyHandler;
  document.addEventListener('keydown', keyHandler);
};

/* ════════════════════════════════════════════════════════════════
   9. UNIT CONVERTER
   ════════════════════════════════════════════════════════════════ */
const CONVERSIONS = {
  'cm → inch': v => (v / 2.54).toFixed(4) + ' in',
  'inch → cm': v => (v * 2.54).toFixed(4) + ' cm',
  'kg → lbs':  v => (v * 2.20462).toFixed(4) + ' lbs',
  'lbs → kg':  v => (v / 2.20462).toFixed(4) + ' kg',
  'm → ft':    v => (v * 3.28084).toFixed(4) + ' ft',
  'ft → m':    v => (v / 3.28084).toFixed(4) + ' m',
  'km → miles':v => (v * 0.621371).toFixed(4) + ' miles',
  'miles → km':v => (v / 0.621371).toFixed(4) + ' km',
  '°C → °F':   v => ((v * 9/5) + 32).toFixed(2) + ' °F',
  '°F → °C':   v => ((v - 32) * 5/9).toFixed(2) + ' °C',
};
function eggConverter() {
  const opts = Object.keys(CONVERSIONS).map(k => `<option value="${escapeHtml(k)}">${escapeHtml(k)}</option>`).join('');
  return {
    html: `<div class="ee-card">
      <h2>🌍 Unit Converter</h2>
      <div class="ee-converter-row">
        <input class="ee-input" type="number" id="eeConvInput" placeholder="Value" oninput="eeConvert()" style="width:120px" />
        <select class="ee-input ee-converter-select" id="eeConvType" onchange="eeConvert()">${opts}</select>
      </div>
      <div class="ee-converter-result" id="eeConvResult">—</div>
    </div>`,
    init() {}
  };
}
window.eeConvert = function() {
  const v = parseFloat($('eeConvInput')?.value);
  const type = $('eeConvType')?.value;
  const el = $('eeConvResult');
  if (!el) return;
  if (isNaN(v)) { el.textContent = '—'; return; }
  const fn = CONVERSIONS[type];
  el.textContent = fn ? fn(v) : '—';
};

/* ════════════════════════════════════════════════════════════════
   10. CALCULATOR
   ════════════════════════════════════════════════════════════════ */
function eggCalculator(q) {
  let prefill = '';
  if (q && q !== 'calculator') {
    const expr = q.trim();
    if (/^[\d\s\+\-\*\/\%\^\(\)\.]+$/.test(expr)) prefill = expr.replace(/\s/g,'');
  }
  const btns = [
    {k:'C',c:'clear'},{k:'(',c:'op'},{k:')',c:'op'},{k:'/',c:'op'},
    {k:'7',c:''},{k:'8',c:''},{k:'9',c:''},{k:'*',c:'op'},
    {k:'4',c:''},{k:'5',c:''},{k:'6',c:''},{k:'-',c:'op'},
    {k:'1',c:''},{k:'2',c:''},{k:'3',c:''},{k:'+',c:'op'},
    {k:'0',c:''},{k:'.',c:''},{k:'^',c:'op'},{k:'√',c:'op'},
    {k:'⌫',c:'clear'},{k:'=',c:'eq'},
  ];
  return {
    html: `<div class="ee-card">
      <h2>🧮 Calculator</h2>
      <div class="ee-calc-display">
        <div class="ee-calc-history" id="eeCalcHistory"></div>
        <span id="eeCalcExpr">${escapeHtml(prefill) || '0'}</span>
      </div>
      <div class="ee-calc-grid">
        ${btns.map(({k,c}) =>
          `<button class="ee-calc-btn ${c}" onclick="eeCalcPress('${k === '\'' ? "'" : k}')">${escapeHtml(k)}</button>`
        ).join('')}
      </div>
      <div class="ee-calc-recent" id="eeCalcRecent"></div>
    </div>`,
    init() {
      window._eeCalcExpr = prefill || '';
      window._eeCalcHistory = JSON.parse(localStorage.getItem('eeCalcHistory') || '[]');
      eeCalcRenderHistory();
      if (prefill) eeCalcEval();
    }
  };
}
window.eeCalcPress = function(k) {
  const exprEl = $('eeCalcExpr');
  if (!exprEl) return;
  if (window._eeCalcExpr === undefined) window._eeCalcExpr = '';
  if (k === 'C') {
    window._eeCalcExpr = '';
    $('eeCalcHistory').textContent = '';
  } else if (k === '⌫') {
    window._eeCalcExpr = window._eeCalcExpr.slice(0, -1);
  } else if (k === '=') {
    eeCalcEval();
    return;
  } else if (k === '√') {
    window._eeCalcExpr += 'sqrt(';
  } else {
    window._eeCalcExpr += k;
  }
  exprEl.textContent = window._eeCalcExpr || '0';
};
window.eeCalcEval = function() {
  const expr = window._eeCalcExpr;
  if (!expr) return;
  try {
    // Replace ^ with ** and sqrt( with Math.sqrt( before evaluating
    const cleanExpr = expr
      .replace(/\^/g, '**')
      .replace(/sqrt\(/g, 'Math.sqrt(');
    // Validate: allow digits, operators, parens, dot, letters for sqrt/Math
    if (!/^[\d\s\+\-\*\/\%\(\)\.a-zA-Z_]+$/.test(cleanExpr)) throw new Error('Invalid');
    // Only allow Math.sqrt — no other identifiers
    if (/(?<![a-zA-Z])[a-zA-Z_][a-zA-Z0-9_]*(?!\.)/.test(cleanExpr.replace(/Math\.sqrt/g, ''))) throw new Error('Disallowed');
    const result = Function('"use strict"; return (' + cleanExpr + ')')();
    const display = String(parseFloat(result.toFixed(10)));
    $('eeCalcHistory').textContent = expr + ' =';
    $('eeCalcExpr').textContent = display;
    window._eeCalcExpr = display;
    if (!window._eeCalcHistory) window._eeCalcHistory = [];
    window._eeCalcHistory.unshift(expr + ' = ' + display);
    window._eeCalcHistory = window._eeCalcHistory.slice(0, 5);
    localStorage.setItem('eeCalcHistory', JSON.stringify(window._eeCalcHistory));
    eeCalcRenderHistory();
  } catch {
    $('eeCalcExpr').textContent = 'Error';
    window._eeCalcExpr = '';
  }
};
function eeCalcRenderHistory() {
  const el = $('eeCalcRecent');
  if (!el || !window._eeCalcHistory?.length) return;
  el.textContent = 'Recent: ' + window._eeCalcHistory.join(' | ');
}

/* ════════════════════════════════════════════════════════════════
   11. COIN FLIP
   ════════════════════════════════════════════════════════════════ */
function eggCoinFlip() {
  return {
    html: `<div class="ee-card">
      <h2>🪙 Coin Flip</h2>
      <div class="ee-coin-wrap">
        <div class="ee-coin" id="eeCoin">🪙</div>
        <div class="ee-coin-result" id="eeCoinResult">—</div>
        <div class="ee-coin-stats" id="eeCoinStats">Heads: 0 · Tails: 0</div>
        <button class="ee-btn" onclick="eeCoinFlip()">Flip!</button>
      </div>
    </div>`,
    init() { window._eeCoin = {h:0,t:0}; setTimeout(eeCoinFlip, 300); }
  };
}
window.eeCoinFlip = function() {
  const coin = $('eeCoin');
  if (!coin) return;
  coin.classList.remove('flipping');
  void coin.offsetWidth;
  coin.classList.add('flipping');
  setTimeout(() => {
    const heads = Math.random() < .5;
    if (!window._eeCoin) window._eeCoin = {h:0,t:0};
    if (heads) window._eeCoin.h++; else window._eeCoin.t++;
    coin.textContent = heads ? '👑' : '🦅';
    $('eeCoinResult').textContent = heads ? 'Heads!' : 'Tails!';
    $('eeCoinStats').textContent = `Heads: ${window._eeCoin.h} · Tails: ${window._eeCoin.t}`;
  }, 300);
};

/* ════════════════════════════════════════════════════════════════
   12. RANDOM NUMBER GENERATOR
   ════════════════════════════════════════════════════════════════ */
function eggRng(defaultMin, defaultMax) {
  const mn = defaultMin ?? 1;
  const mx = defaultMax ?? 100;
  return {
    html: `<div class="ee-card">
      <h2>🎲 Random Number Generator</h2>
      <div class="ee-rng-result" id="eeRngResult">?</div>
      <div class="ee-rng-row">
        <span class="ee-rng-label">Min</span>
        <input class="ee-input ee-rng-num-input" type="number" id="eeRngMin" value="${mn}" />
        <span class="ee-rng-label">Max</span>
        <input class="ee-input ee-rng-num-input" type="number" id="eeRngMax" value="${mx}" />
        <span class="ee-rng-label">Count</span>
        <input class="ee-input ee-rng-num-input" type="number" id="eeRngCount" value="1" min="1" max="20" title="How many numbers to generate" />
      </div>
      <div class="ee-row" style="margin-top:12px">
        <button class="ee-btn" onclick="eeRngGenerate()">Generate</button>
      </div>
    </div>`,
    init() {}
  };
}
window.eeRngGenerate = function() {
  const mn = parseInt($('eeRngMin')?.value || '1', 10);
  const mx = parseInt($('eeRngMax')?.value || '100', 10);
  const count = Math.min(Math.max(parseInt($('eeRngCount')?.value || '1', 10), 1), 20);
  const el = $('eeRngResult');
  if (!el || mn > mx) return;
  const nums = Array.from({length: count}, () => Math.floor(Math.random() * (mx - mn + 1)) + mn);
  el.textContent = nums.join(', ');
};

/* ════════════════════════════════════════════════════════════════
   13. TIC-TAC-TOE
   ════════════════════════════════════════════════════════════════ */
function eggTicTacToe() {
  return {
    html: `<div class="ee-card">
      <h2>❌⭕ Tic-Tac-Toe</h2>
      <div class="ee-ttt-mode">
        <button class="ee-btn" id="eeTTTBtnPvP" onclick="eeTTTInit('pvp')">👥 Player vs Player</button>
        <button class="ee-btn grey" id="eeTTTBtnAI" onclick="eeTTTInit('ai')">🤖 Player vs AI</button>
      </div>
      <div class="ee-ttt-mode-label" id="eeTTTModeLabel"></div>
      <div class="ee-ttt-diff" id="eeTTTDiff">
        AI Difficulty:
        <label><input type="radio" name="tttDiff" value="easy" checked> Easy</label>
        <label><input type="radio" name="tttDiff" value="medium"> Medium</label>
        <label><input type="radio" name="tttDiff" value="hard"> Hard</label>
      </div>
      <div class="ee-ttt-status" id="eeTTTStatus">Choose a mode to start!</div>
      <div class="ee-ttt-board" id="eeTTTBoard">
        ${Array(9).fill(0).map((_,i) => `<div class="ee-ttt-cell" data-idx="${i}" onclick="eeTTTClick(${i})"></div>`).join('')}
      </div>
      <button class="ee-btn grey" onclick="eeTTTInit(window._eeTTT?.mode||'pvp')">Reset</button>
    </div>`,
    init() { eeTTTInit('pvp'); }
  };
}
window.eeTTTInit = function(mode) {
  window._eeTTT = { board: Array(9).fill(''), turn: 'X', mode, done: false };
  $('eeTTTStatus').textContent = mode === 'pvp' ? "Player X's turn" : "Your turn (X)";
  $('eeTTTBoard').querySelectorAll('.ee-ttt-cell').forEach(c => {
    c.textContent = ''; c.className = 'ee-ttt-cell'; c.removeAttribute('data-filled');
  });
  // Highlight active mode button
  const pvpBtn = $('eeTTTBtnPvP'), aiBtn = $('eeTTTBtnAI');
  if (pvpBtn && aiBtn) {
    pvpBtn.className = mode === 'pvp' ? 'ee-btn' : 'ee-btn grey';
    aiBtn.className  = mode === 'ai'  ? 'ee-btn' : 'ee-btn grey';
  }
  // Show mode label
  const label = $('eeTTTModeLabel');
  if (label) label.textContent = mode === 'pvp' ? '👥 Mode: Player vs Player' : '🤖 Mode: Player vs AI';
  // Hide diff selector in pvp mode
  const diff = $('eeTTTDiff');
  if (diff) diff.style.display = mode === 'pvp' ? 'none' : '';
};
window.eeTTTClick = function(idx) {
  const t = window._eeTTT;
  if (!t || t.done || t.board[idx]) return;
  eeTTTPlace(idx, t.turn);
  if (t.done) return;
  if (t.mode === 'ai' && t.turn === 'O') {
    setTimeout(() => eeTTTAiMove(), 300);
  }
};
function eeTTTPlace(idx, mark) {
  const t = window._eeTTT;
  t.board[idx] = mark;
  const cells = $('eeTTTBoard').querySelectorAll('.ee-ttt-cell');
  cells[idx].textContent = mark;
  cells[idx].dataset.filled = '1';
  cells[idx].classList.add(mark === 'X' ? 'x-cell' : 'o-cell');
  const win = eeTTTCheck(t.board);
  if (win) {
    t.done = true;
    win.forEach(i => cells[i].classList.add('win-cell'));
    $('eeTTTStatus').textContent = t.mode === 'ai' && mark === 'O' ? '🤖 AI wins!' : `${mark} wins! 🎉`;
  } else if (!t.board.includes('')) {
    t.done = true;
    $('eeTTTStatus').textContent = "It's a draw!";
  } else {
    t.turn = t.turn === 'X' ? 'O' : 'X';
    $('eeTTTStatus').textContent = t.mode === 'pvp' ? `Player ${t.turn}'s turn` : (t.turn === 'X' ? 'Your turn (X)' : '🤖 AI thinking…');
  }
}
function eeTTTAiMove() {
  const t = window._eeTTT;
  if (!t || t.done) return;
  const diff = document.querySelector('input[name="tttDiff"]:checked')?.value || 'easy';
  let idx;
  if (diff === 'hard') {
    idx = eeTTTMinimax(t.board, 'O').idx;
  } else if (diff === 'medium') {
    idx = Math.random() < .6 ? eeTTTMinimax(t.board, 'O').idx : eeTTTRandMove(t.board);
  } else {
    idx = eeTTTRandMove(t.board);
  }
  if (idx !== undefined) eeTTTPlace(idx, 'O');
}
function eeTTTRandMove(board) {
  const empty = board.map((v,i)=>v?-1:i).filter(i=>i>=0);
  return empty[Math.floor(Math.random()*empty.length)];
}
function eeTTTCheck(b) {
  const lines = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
  for (const l of lines) if (b[l[0]] && b[l[0]]===b[l[1]] && b[l[1]]===b[l[2]]) return l;
  return null;
}
function eeTTTMinimax(board, player) {
  const win = eeTTTCheck(board);
  if (win) return {score: board[win[0]] === 'O' ? 10 : -10};
  if (!board.includes('')) return {score: 0};
  const moves = board.map((v,i)=>v?-1:i).filter(i=>i>=0);
  let best = player === 'O' ? {score:-Infinity} : {score:Infinity};
  for (const i of moves) {
    const nb = [...board]; nb[i] = player;
    const score = eeTTTMinimax(nb, player === 'O' ? 'X' : 'O').score;
    if (player === 'O' ? score > best.score : score < best.score) best = {score, idx: i};
  }
  return best;
}

/* ════════════════════════════════════════════════════════════════
   14. MAGIC 8 BALL
   ════════════════════════════════════════════════════════════════ */
const BALL_ANSWERS = [
  'It is certain','It is decidedly so','Without a doubt','Yes definitely',
  'You may rely on it','As I see it, yes','Most likely','Outlook good',
  'Yes','Signs point to yes','Reply hazy try again','Ask again later',
  'Better not tell you now','Cannot predict now','Concentrate and ask again',
  "Don't count on it",'My reply is no','My sources say no',
  'Outlook not so good','Very doubtful', "Absolutely!","No way!","The stars say yes","The stars say no", 
  "The future is unclear","Definitely not","I have a good feeling about this","I have a bad feeling about this",
];
function eggMagic8Ball() {
  return {
    html: `<div class="ee-card">
      <h2>🧙 Magic 8 Ball</h2>
      <div class="ee-8ball-wrap">
        <div class="ee-8ball" onclick="ee8BallAsk()" title="Click to ask">
          <div class="ee-8ball-inner"><span id="ee8BallInner">8</span></div>
        </div>
        <div class="ee-8ball-answer" id="ee8BallAnswer">Ask me anything…</div>
        <div class="ee-8ball-input-row">
          <input class="ee-input ee-8ball-input" id="ee8BallQ" placeholder="Type your question…" />
          <button class="ee-btn" onclick="ee8BallAsk()">Ask</button>
        </div>
      </div>
    </div>`,
    init() {}
  };
}
window.ee8BallAsk = function() {
  const ans = BALL_ANSWERS[Math.floor(Math.random() * BALL_ANSWERS.length)];
  const inner = $('ee8BallInner');
  const ansEl = $('ee8BallAnswer');
  if (inner) { inner.textContent = '?'; setTimeout(() => { inner.textContent = ans.slice(0,18); }, 300); }
  if (ansEl) ansEl.textContent = ans;
};

/* ════════════════════════════════════════════════════════════════
   15. NAME PICKER
   ════════════════════════════════════════════════════════════════ */
function eggNamePicker() {
  return {
    html: `<div class="ee-card">
      <h2>🔡 Random Name Picker</h2>
      <textarea class="ee-names-textarea" id="eeNamesInput" placeholder="Enter names, one per line or comma-separated…"></textarea>
      <div class="ee-picked-name" id="eePickedName">—</div>
      <button class="ee-btn" onclick="eePickName()">Pick!</button>
    </div>`,
    init() {}
  };
}
window.eePickName = function() {
  const raw = $('eeNamesInput')?.value || '';
  const names = raw.split(/[\n,]/).map(n => n.trim()).filter(Boolean);
  const el = $('eePickedName');
  if (!el) return;
  if (!names.length) { el.textContent = 'Enter some names first!'; return; }
  el.textContent = names[Math.floor(Math.random() * names.length)];
};

/* ════════════════════════════════════════════════════════════════
   16. PASSWORD GENERATOR
   ════════════════════════════════════════════════════════════════ */
function eggPasswordGenerator() {
  return {
    html: `<div class="ee-card">
      <h2>🧾 Password Generator</h2>
      <div class="ee-pw-length-row">
        <span style="font-size:13px;font-weight:500;color:var(--text-muted)">Length:</span>
        <input type="range" class="ee-pw-range" id="eePwLen" min="8" max="32" value="16" oninput="$('eePwLenVal').textContent=this.value;eePwGen()" />
        <span class="ee-pw-length-val" id="eePwLenVal">16</span>
      </div>
      <div class="ee-pw-options">
        <label class="ee-pw-option"><input type="checkbox" id="eePwUpper" checked onchange="eePwGen()"> Uppercase letters</label>
        <label class="ee-pw-option"><input type="checkbox" id="eePwNums" checked onchange="eePwGen()"> Numbers</label>
        <label class="ee-pw-option"><input type="checkbox" id="eePwSyms" onchange="eePwGen()"> Symbols</label>
      </div>
      <div class="ee-pw-output" id="eePwOutput"></div>
      <div class="ee-row">
        <button class="ee-btn" onclick="eePwGen()">Generate</button>
        <button class="ee-btn grey" onclick="eePwCopy()">Copy</button>
      </div>
    </div>`,
    init() { eePwGen(); }
  };
}
window.eePwGen = function() {
  let chars = 'abcdefghijklmnopqrstuvwxyz';
  if ($('eePwUpper')?.checked) chars += 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  if ($('eePwNums')?.checked) chars += '0123456789';
  if ($('eePwSyms')?.checked) chars += '!@#$%^&*()_+-=[]{}|;:,.<>?';
  const len = parseInt($('eePwLen')?.value || '16', 10);
  let pw = '';
  const arr = new Uint32Array(len);
  crypto.getRandomValues(arr);
  for (let i = 0; i < len; i++) pw += chars[arr[i] % chars.length];
  const el = $('eePwOutput');
  if (el) el.textContent = pw;
  window._eePwCurrent = pw;
};
window.eePwCopy = function() {
  if (window._eePwCurrent) navigator.clipboard?.writeText(window._eePwCurrent);
};

/* ════════════════════════════════════════════════════════════════
   17. PASSWORD STRENGTH
   ════════════════════════════════════════════════════════════════ */
function eggPasswordStrength() {
  return {
    html: `<div class="ee-card">
      <h2>🔐 Password Strength Checker</h2>
      <input class="ee-input" type="password" id="eePwStrInput" placeholder="Enter a password…" oninput="eePwStrCheck()" style="width:100%;margin-bottom:12px" />
      <div class="ee-strength-bar"><div class="ee-strength-fill" id="eePwStrFill"></div></div>
      <div class="ee-strength-label" id="eePwStrLabel"></div>
      <div class="ee-strength-tips" id="eePwStrTips"></div>
    </div>`,
    init() {}
  };
}
window.eePwStrCheck = function() {
  const pw = $('eePwStrInput')?.value || '';
  let score = 0;
  const tips = [];
  if (pw.length >= 8) score++; else tips.push('Use at least 8 characters');
  if (pw.length >= 12) score++;
  if (/[A-Z]/.test(pw)) score++; else tips.push('Add uppercase letters');
  if (/[0-9]/.test(pw)) score++; else tips.push('Add numbers');
  if (/[^A-Za-z0-9]/.test(pw)) score++; else tips.push('Add symbols (!@#…)');
  const fill = $('eePwStrFill');
  const label = $('eePwStrLabel');
  const tipsEl = $('eePwStrTips');
  const pct = Math.round((score / 5) * 100);
  const colors = ['#ea4335','#ea4335','#fbbc05','#34a853','#1e8e3e'];
  const labels = ['Very Weak','Weak','Fair','Strong','Very Strong'];
  if (fill) { fill.style.width = pct + '%'; fill.style.background = colors[score] || colors[0]; }
  if (label) { label.textContent = pw ? labels[score] || 'Very Strong' : ''; label.style.color = colors[score] || colors[0]; }
  if (tipsEl) tipsEl.textContent = tips.length ? 'Tips: ' + tips.join(' · ') : (pw ? '✓ Looks good!' : '');
};

/* ════════════════════════════════════════════════════════════════
   18. SUDOKU
   ════════════════════════════════════════════════════════════════ */
function eggSudoku() {
  return {
    html: `<div class="ee-card">
      <h2>🧩 Sudoku</h2>
      <div class="ee-row" style="margin-bottom:12px;justify-content:center;gap:8px">
        <button class="ee-btn grey" onclick="eeSudokuNew('easy')">Easy</button>
        <button class="ee-btn grey" onclick="eeSudokuNew('medium')">Medium</button>
        <button class="ee-btn grey" onclick="eeSudokuNew('hard')">Hard</button>
        <button class="ee-btn" onclick="eeSudokuSolve()">Solve</button>
      </div>
      <div class="ee-sudoku-grid" id="eeSudokuGrid"></div>
    </div>`,
    init() { eeSudokuNew('easy'); }
  };
}
// Minimal valid base puzzle
const SUDOKU_BASE = [
  [5,3,0,0,7,0,0,0,0],[6,0,0,1,9,5,0,0,0],[0,9,8,0,0,0,0,6,0],
  [8,0,0,0,6,0,0,0,3],[4,0,0,8,0,3,0,0,1],[7,0,0,0,2,0,0,0,6],
  [0,6,0,0,0,0,2,8,0],[0,0,0,4,1,9,0,0,5],[0,0,0,0,8,0,0,7,9]
];
const SUDOKU_SOL = [
  [5,3,4,6,7,8,9,1,2],[6,7,2,1,9,5,3,4,8],[1,9,8,3,4,2,5,6,7],
  [8,5,9,7,6,1,4,2,3],[4,2,6,8,5,3,7,9,1],[7,1,3,9,2,4,8,5,6],
  [9,6,1,5,3,7,2,8,4],[2,8,7,4,1,9,6,3,5],[3,4,5,2,8,6,1,7,9]
];
window.eeSudokuNew = function(diff) {
  const removeCount = diff === 'easy' ? 30 : diff === 'medium' ? 45 : 55;
  // Start from the full solution to avoid infinite loops
  const board = SUDOKU_SOL.map(r => [...r]);
  // Shuffle all cell positions and remove the first removeCount
  const positions = [];
  for (let r = 0; r < 9; r++) for (let c = 0; c < 9; c++) positions.push([r, c]);
  positions.sort(() => Math.random() - .5);
  for (let i = 0; i < removeCount; i++) {
    const [r, c] = positions[i];
    board[r][c] = 0;
  }
  window._eeSudokuBoard = board;
  window._eeSudokuGiven = board.map(r => r.map(v => v !== 0));
  eeSudokuRender(board);
};
window.eeSudokuSolve = function() {
  eeSudokuRender(SUDOKU_SOL);
};
function eeSudokuRender(board) {
  const grid = $('eeSudokuGrid');
  if (!grid) return;
  grid.innerHTML = '';
  for (let r = 0; r < 9; r++) {
    for (let c = 0; c < 9; c++) {
      const cell = document.createElement('input');
      cell.type = 'text';
      cell.maxLength = 1;
      cell.className = 'ee-sudoku-cell';
      const given = window._eeSudokuGiven?.[r]?.[c];
      if (board[r][c]) cell.value = board[r][c];
      if (given) { cell.readOnly = true; cell.classList.add('given'); }
      if (c === 2 || c === 5) cell.classList.add('box-right');
      if (r === 2 || r === 5) cell.classList.add('box-bottom');
      cell.oninput = () => { cell.value = cell.value.replace(/[^1-9]/g,'').slice(-1); };
      grid.appendChild(cell);
    }
  }
}

/* ════════════════════════════════════════════════════════════════
   19. HASH GENERATOR
   ════════════════════════════════════════════════════════════════ */
function eggHash() {
  return {
    html: `<div class="ee-card">
      <h2>🔑 Hash Generator</h2>
      <div class="ee-hash-tabs">
        <button class="ee-hash-tab active" id="eeHashTabMd5" onclick="eeHashSwitch('md5')">MD5</button>
        <button class="ee-hash-tab" id="eeHashTabSha" onclick="eeHashSwitch('sha256')">SHA-256</button>
      </div>
      <input class="ee-input" type="text" id="eeHashInput" placeholder="Type text to hash…" oninput="eeHashCompute()" style="width:100%;margin-bottom:12px" />
      <div class="ee-hash-output" id="eeHashOutput">—</div>
      <button class="ee-btn grey" style="margin-top:10px" onclick="eeHashCopy()">Copy</button>
    </div>`,
    init() { window._eeHashAlgo = 'sha256'; eeHashCompute(); }
  };
}
window.eeHashSwitch = function(algo) {
  window._eeHashAlgo = algo;
  $('eeHashTabMd5').classList.toggle('active', algo==='md5');
  $('eeHashTabSha').classList.toggle('active', algo==='sha256');
  eeHashCompute();
};
window.eeHashCompute = async function() {
  const text = $('eeHashInput')?.value || '';
  const el = $('eeHashOutput');
  if (!el) return;
  if (!text) { el.textContent = '—'; return; }
  if (window._eeHashAlgo === 'sha256') {
    const enc = new TextEncoder().encode(text);
    const buf = await crypto.subtle.digest('SHA-256', enc);
    el.textContent = Array.from(new Uint8Array(buf)).map(b=>b.toString(16).padStart(2,'0')).join('');
  } else {
    // Simple MD5 implementation
    el.textContent = simpleMd5(text);
  }
  window._eeHashValue = el.textContent;
};
window.eeHashCopy = function() {
  if (window._eeHashValue) navigator.clipboard?.writeText(window._eeHashValue);
};
// Minimal MD5 (RFC 1321)
function simpleMd5(str) {
  function safeAdd(x,y){const l=(x&0xffff)+(y&0xffff);return(((x>>16)+(y>>16)+(l>>16))<<16)|(l&0xffff);}
  function rol(n,c){return(n<<c)|(n>>>(32-c));}
  function cmn(q,a,b,x,s,t){return safeAdd(rol(safeAdd(safeAdd(a,q),safeAdd(x,t)),s),b);}
  function ff(a,b,c,d,x,s,t){return cmn((b&c)|((~b)&d),a,b,x,s,t);}
  function gg(a,b,c,d,x,s,t){return cmn((b&d)|(c&(~d)),a,b,x,s,t);}
  function hh(a,b,c,d,x,s,t){return cmn(b^c^d,a,b,x,s,t);}
  function ii(a,b,c,d,x,s,t){return cmn(c^(b|(~d)),a,b,x,s,t);}
  const n = str.length;
  const state = [1732584193,-271733879,-1732584194,271733878];
  const msg = [];
  for(let i=0;i<n;i++){msg[i>>2]|=(str.charCodeAt(i)&0xff)<<((i%4)*8);}
  msg[n>>2]|=0x80<<((n%4)*8);
  msg[(((n+8)>>6)<<4)+14]=n*8;
  for(let i=0,l=msg.length;i<l;i+=16){
    let [a,b,c,d]=[...state];
    a=ff(a,b,c,d,msg[i],7,-680876936);d=ff(d,a,b,c,msg[i+1],12,-389564586);c=ff(c,d,a,b,msg[i+2],17,606105819);b=ff(b,c,d,a,msg[i+3],22,-1044525330);
    a=ff(a,b,c,d,msg[i+4],7,-176418897);d=ff(d,a,b,c,msg[i+5],12,1200080426);c=ff(c,d,a,b,msg[i+6],17,-1473231341);b=ff(b,c,d,a,msg[i+7],22,-45705983);
    a=ff(a,b,c,d,msg[i+8],7,1770035416);d=ff(d,a,b,c,msg[i+9],12,-1958414417);c=ff(c,d,a,b,msg[i+10],17,-42063);b=ff(b,c,d,a,msg[i+11],22,-1990404162);
    a=ff(a,b,c,d,msg[i+12],7,1804603682);d=ff(d,a,b,c,msg[i+13],12,-40341101);c=ff(c,d,a,b,msg[i+14],17,-1502002290);b=ff(b,c,d,a,msg[i+15],22,1236535329);
    a=gg(a,b,c,d,msg[i+1],5,-165796510);d=gg(d,a,b,c,msg[i+6],9,-1069501632);c=gg(c,d,a,b,msg[i+11],14,643717713);b=gg(b,c,d,a,msg[i],20,-373897302);
    a=gg(a,b,c,d,msg[i+5],5,-701558691);d=gg(d,a,b,c,msg[i+10],9,38016083);c=gg(c,d,a,b,msg[i+15],14,-660478335);b=gg(b,c,d,a,msg[i+4],20,-405537848);
    a=gg(a,b,c,d,msg[i+9],5,568446438);d=gg(d,a,b,c,msg[i+14],9,-1019803690);c=gg(c,d,a,b,msg[i+3],14,-187363961);b=gg(b,c,d,a,msg[i+8],20,1163008128);
    a=gg(a,b,c,d,msg[i+13],5,-1444681467);d=gg(d,a,b,c,msg[i+2],9,-51403784);c=gg(c,d,a,b,msg[i+7],14,1735328473);b=gg(b,c,d,a,msg[i+12],20,-1926607734);
    a=hh(a,b,c,d,msg[i+5],4,-378558);d=hh(d,a,b,c,msg[i+8],11,-2022574463);c=hh(c,d,a,b,msg[i+11],16,1839030562);b=hh(b,c,d,a,msg[i+14],23,-35309556);
    a=hh(a,b,c,d,msg[i+1],4,-1530992060);d=hh(d,a,b,c,msg[i+4],11,1272893353);c=hh(c,d,a,b,msg[i+7],16,-155497632);b=hh(b,c,d,a,msg[i+10],23,-1094730640);
    a=hh(a,b,c,d,msg[i+13],4,681279174);d=hh(d,a,b,c,msg[i],11,-358537222);c=hh(c,d,a,b,msg[i+3],16,-722521979);b=hh(b,c,d,a,msg[i+6],23,76029189);
    a=hh(a,b,c,d,msg[i+9],4,-640364487);d=hh(d,a,b,c,msg[i+12],11,-421815835);c=hh(c,d,a,b,msg[i+15],16,530742520);b=hh(b,c,d,a,msg[i+2],23,-995338651);
    a=ii(a,b,c,d,msg[i],6,-198630844);d=ii(d,a,b,c,msg[i+7],10,1126891415);c=ii(c,d,a,b,msg[i+14],15,-1416354905);b=ii(b,c,d,a,msg[i+5],21,-57434055);
    a=ii(a,b,c,d,msg[i+12],6,1700485571);d=ii(d,a,b,c,msg[i+3],10,-1894986606);c=ii(c,d,a,b,msg[i+10],15,-1051523);b=ii(b,c,d,a,msg[i+1],21,-2054922799);
    a=ii(a,b,c,d,msg[i+8],6,1873313359);d=ii(d,a,b,c,msg[i+15],10,-30611744);c=ii(c,d,a,b,msg[i+6],15,-1560198380);b=ii(b,c,d,a,msg[i+13],21,1309151649);
    a=ii(a,b,c,d,msg[i+4],6,-145523070);d=ii(d,a,b,c,msg[i+11],10,-1120210379);c=ii(c,d,a,b,msg[i+2],15,718787259);b=ii(b,c,d,a,msg[i+9],21,-343485551);
    state[0]=safeAdd(a,state[0]);state[1]=safeAdd(b,state[1]);state[2]=safeAdd(c,state[2]);state[3]=safeAdd(d,state[3]);
  }
  return state.map(v=>{let s='';for(let i=0;i<4;i++)s+=('0'+((v>>(i*8))&0xff).toString(16)).slice(-2);return s;}).join('');
}

/* ════════════════════════════════════════════════════════════════
   20. RICKROLL
   ════════════════════════════════════════════════════════════════ */
function eggRickroll() {
  return {
    html: `<div class="ee-card">
      <h2>💃 You just got rickrolled!</h2>
      <div class="ee-rickroll-wrap">
        <iframe src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1"
                title="Rick Astley - Never Gonna Give You Up"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowfullscreen></iframe>
      </div>
    </div>`,
    init() {}
  };
}

/* ════════════════════════════════════════════════════════════════
   21. USERNAME GENERATOR
   ════════════════════════════════════════════════════════════════ */
const UG_ADJ = ['swift','dark','cool','epic','silent','neon','wild','cyber','turbo','hyper','glitch','ultra','mystic','shadow','pixel','frozen','solar','lunar','void','storm'];
const UG_NOUNS = ['wolf','eagle','panda','shark','falcon','tiger','raven','cobra','viper','phoenix','dragon','ninja','wizard','pirate','robot','hunter','ghost','knight','samurai','legend'];
const UG_NUMS = () => Math.floor(Math.random() * 900 + 100);
function eeGenerateUsername() {
  const styles = [
    () => UG_ADJ[~~(Math.random()*UG_ADJ.length)] + '_' + UG_NOUNS[~~(Math.random()*UG_NOUNS.length)] + UG_NUMS(),
    () => UG_ADJ[~~(Math.random()*UG_ADJ.length)] + UG_NOUNS[~~(Math.random()*UG_NOUNS.length)].replace(/./,(c)=>c.toUpperCase()),
    () => 'x_' + UG_ADJ[~~(Math.random()*UG_ADJ.length)] + UG_NOUNS[~~(Math.random()*UG_NOUNS.length)] + '_x',
    () => UG_NOUNS[~~(Math.random()*UG_NOUNS.length)] + UG_NUMS(),
    () => UG_ADJ[~~(Math.random()*UG_ADJ.length)].toUpperCase() + '_' + UG_NUMS(),
  ];
  return styles[~~(Math.random()*styles.length)]();
}
function eggUsernameGenerator() {
  return {
    html: `<div class="ee-card">
      <h2>📛 Username Generator</h2>
      <div class="ee-ug-result" id="eeUGResult">—</div>
      <div class="ee-ug-list" id="eeUGList"></div>
      <div class="ee-row" style="margin-top:14px;flex-wrap:wrap">
        <button class="ee-btn" onclick="eeUGGenerate()">Generate</button>
        <button class="ee-btn green" onclick="eeUGGenerate(5)">Generate 5</button>
        <button class="ee-btn grey" onclick="eeUGCopy()">Copy</button>
      </div>
    </div>`,
    init() { eeUGGenerate(); }
  };
}
window.eeUGGenerate = function(count) {
  const n = count || 1;
  const usernames = Array.from({length: n}, eeGenerateUsername);
  const result = $('eeUGResult');
  const list = $('eeUGList');
  if (n === 1) {
    if (result) result.textContent = usernames[0];
    if (list) list.innerHTML = '';
    window._eeUGCurrent = usernames[0];
  } else {
    if (result) result.textContent = '';
    if (list) list.innerHTML = usernames.map(u =>
      `<div class="ee-ug-item" onclick="eeUGCopyText('${u}')">${escapeHtml(u)} <span class="ee-ug-copy-hint">click to copy</span></div>`
    ).join('');
    window._eeUGCurrent = usernames[0];
  }
};
window.eeUGCopy = function() {
  const r = $('eeUGResult');
  const text = r?.textContent || window._eeUGCurrent || '';
  if (text && text !== '—') navigator.clipboard?.writeText(text);
};
window.eeUGCopyText = function(text) {
  navigator.clipboard?.writeText(text);
};

/* ════════════════════════════════════════════════════════════════
   22. EASTER EGGS LIST
   ════════════════════════════════════════════════════════════════ */
const EE_LIST = [
  {icon:'⏱️', name:'Timer / Stopwatch',     trigger:'timer'},
  {icon:'🔤', name:'Typing Test',             trigger:'typing test'},
  {icon:'🧠', name:'Rock Paper Scissors',      trigger:'rock paper scissors'},
  {icon:'🎨', name:'Color Picker',             trigger:'color picker'},
  {icon:'📅', name:'Calendar',                 trigger:'calendar'},
  {icon:'😂', name:'Random Joke',               trigger:'joke'},
  {icon:'🧩', name:'Memory Game',               trigger:'memory game'},
  {icon:'🐍', name:'Snake Game',                trigger:'snake'},
  {icon:'🌍', name:'Unit Converter',            trigger:'convert'},
  {icon:'🧮', name:'Calculator',                trigger:'calculator'},
  {icon:'🪙', name:'Coin Flip',                 trigger:'coin flip'},
  {icon:'🎲', name:'Random Number Generator', trigger:'random number'},
  {icon:'🎲', name:'Dice',                      trigger:'dice'},
  {icon:'❌⭕', name:'Tic-Tac-Toe',              trigger:'tic tac toe'},
  {icon:'🧙', name:'Magic 8 Ball',              trigger:'magic 8 ball'},
  {icon:'🔡', name:'Random Name Picker',       trigger:'random name'},
  {icon:'🧧', name:'Password Generator',       trigger:'password generator'},
  {icon:'🔐', name:'Password Strength',       trigger:'password strength'},
  {icon:'🧩', name:'Sudoku',                   trigger:'sudoku'},
  {icon:'🔑', name:'Hash Generator',           trigger:'hash'},
  {icon:'📛', name:'Username Generator',       trigger:'username generator'},
  {icon:'💃', name:'Rickroll',                 trigger:'rickroll'},
  {icon:'🤣', name:'Am I dumb?',               trigger:'am i dumb'},
  {icon:'🙄', name:'Are you dumb?',             trigger:'are you dumb'},
];
function eggEasterEggsList() {
  const rows = EE_LIST.map(e =>
    `<div class="ee-list-item" onclick="document.getElementById('searchInput').value='${escapeHtml(e.trigger)}';document.querySelector('.header-search-box').dispatchEvent(new Event('submit',{bubbles:true}));doSearch('${escapeHtml(e.trigger)}')">
      <span class="ee-list-icon">${e.icon}</span>
      <span class="ee-list-name">${escapeHtml(e.name)}</span>
      <code class="ee-list-trigger">${escapeHtml(e.trigger)}</code>
    </div>`
  ).join('');
  return {
    html: `<div class="ee-card">
      <h2>🥚 Easter Eggs</h2>
      <p style="font-size:13px;color:var(--text-muted);margin-bottom:12px">Search these keywords to activate each easter egg. Click a row to try it!</p>
      <div class="ee-list">${rows}</div>
    </div>`,
    init() {}
  };
}

/* ════════════════════════════════════════════════════════════════
   23. AM I DUMB / ARE YOU DUMB
   ════════════════════════════════════════════════════════════════ */
function eggAmIDumb() {
  return {
    html: `<div class="ee-card" style="text-align:center">
      <h2>🤣 Am I dumb?</h2>
      <div style="font-size:80px;margin:16px 0">🤔</div>
      <div style="font-size:48px;font-weight:800;color:var(--red)">Yes.</div>
    </div>`,
    init() {}
  };
}
function eggAreYouDumb() {
  return {
    html: `<div class="ee-card" style="text-align:center">
      <h2>🙄 Are you dumb?</h2>
      <div style="font-size:80px;margin:16px 0">🤨</div>
      <div style="font-size:32px;font-weight:800;color:var(--brand)">No, but you are.</div>
    </div>`,
    init() {}
  };
}

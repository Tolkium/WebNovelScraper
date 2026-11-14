// Paste into the DevTools console (F12, then Console) on a chapter page of the novel.
// The desktop app's Copy script button fills in SETTINGS for your book. Without them,
// chapter links come from the open page, whose address must end in the chapter number.
// Popups ask for the first and last chapter; all chapters are saved to one file in Downloads.
// Type wnsStart() to ask again, or wnsStop() to stop early, without pasting again.
(() => {
  const SETTINGS = /*SETTINGS*/null;
  const DELAY_MS = 3000;
  const page = location.href.match(/^([^?#]*?)(\d+)\/?(?:[?#].*)?$/);
  const config = SETTINGS || {
    chapterUrl: page ? `${page[1]}{n}` : '',
    name: location.pathname.split('/').filter(Boolean).slice(-2, -1)[0] || 'novel',
    start: page ? Number(page[2]) : 1,
    end: page ? Number(page[2]) + 49 : 50,
    titleSelector: '',
    contentSelector: '',
  };
  const live = document.createElement('div');
  live.setAttribute('role', 'status');
  live.setAttribute('aria-live', 'polite');
  live.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);';
  document.body.append(live);
  function announce(message) {
    console.log(message);
    live.textContent = '';
    setTimeout(() => { live.textContent = message; }, 50);
  }
  function escapeHtml(text) {
    return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
  function chapterUrl(number) {
    return config.chapterUrl.replace('{n}', number);
  }
  function query(root, selector) {
    try {
      return selector ? root.querySelector(selector) : null;
    } catch (error) {
      return null;
    }
  }
  // The configured content selector, or else the element holding the most paragraph text.
  function findContent(doc) {
    const chosen = query(doc, config.contentSelector);
    if (chosen && chosen.textContent.trim()) return chosen;
    const lengths = new Map();
    for (const paragraph of doc.querySelectorAll('p')) {
      const parent = paragraph.parentElement;
      lengths.set(parent, (lengths.get(parent) || 0) + paragraph.textContent.trim().length);
    }
    let best = null;
    let bestLength = 200;
    for (const [element, length] of lengths) {
      if (length > bestLength) {
        best = element;
        bestLength = length;
      }
    }
    return best;
  }
  function findTitle(doc, content, number) {
    const chosen = query(doc, config.titleSelector);
    const chosenText = chosen ? chosen.textContent.trim() : '';
    if (chosenText && chosenText.length < 200) return chosenText;
    const heading = new RegExp(`^chapter\\s*${number}\\b`, 'i');
    for (const element of [...content.querySelectorAll('h1, h2, h3, h4, p'), ...doc.querySelectorAll('h1, h2, h3, h4')]) {
      const text = element.textContent.trim();
      if (text.length < 200 && heading.test(text)) return text;
    }
    return `Chapter ${number}`;
  }
  const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const [linkStart, linkEnd] = config.chapterUrl.split('{n}').map(escapeRegExp);
  const inBetweenLink = new RegExp(`^${linkStart}(\\d+\\.\\d+)${linkEnd}/?(?:[?#].*)?$`);
  // In-between chapters, such as 769.1 after 769, that the page links to.
  function inBetweenNumbers(doc, number) {
    const whole = Math.floor(number);
    const found = new Set();
    for (const link of doc.querySelectorAll('a[href]')) {
      const match = new URL(link.getAttribute('href'), chapterUrl(number)).href.match(inBetweenLink);
      const linked = match ? Number(match[1]) : 0;
      if (linked > whole && linked < whole + 1) found.add(linked);
    }
    return [...found].sort((a, b) => a - b);
  }
  // Returns the chapter, or null when the page has no chapter text.
  async function loadChapter(number) {
    const response = await fetch(chapterUrl(number), { credentials: 'same-origin' });
    if (!response.ok || response.redirected) return null;
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    const content = findContent(doc);
    if (!content) return null;
    return { number, title: findTitle(doc, content, number), html: content.innerHTML, inBetween: inBetweenNumbers(doc, number) };
  }
  function save(chapters) {
    const first = chapters[0].number;
    const last = chapters[chapters.length - 1].number;
    const sections = chapters.map(({ number, title, html }) => `<section class="wns-chapter" data-number="${number}">\n`
      + `<span class="chapter-title">${escapeHtml(title)}</span>\n`
      + `<div class="chapter-container">${html}</div>\n</section>`);
    const file = '<!doctype html>\n<html><head><meta charset="utf-8">'
      + `<title>${escapeHtml(config.name)}</title><meta name="source" content="${escapeHtml(config.chapterUrl)}">`
      + `</head><body>\n${sections.join('\n')}\n</body></html>\n`;
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([file], { type: 'text/html' }));
    link.download = `${config.name}-${first}-${last}.html`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    return `Saved chapters ${first} to ${last} to ${link.download} in Downloads.`;
  }
  let running = false;
  let stopped = false;
  async function collect(start, end) {
    if (running) {
      announce('Already collecting. Type wnsStop() first.');
      return;
    }
    if (!(Number.isInteger(start) && Number.isInteger(end) && start >= 1 && end >= start)) {
      announce('Use wnsCollect(first, last), for example wnsCollect(100, 150).');
      return;
    }
    if (!config.chapterUrl) {
      announce('Open a chapter page whose address ends in the chapter number, then paste the script.');
      return;
    }
    const site = new URL(chapterUrl(start)).origin;
    if (site !== location.origin) {
      announce(`Open a page of ${site} in this tab, then paste the script again.`);
      return;
    }
    running = true;
    stopped = false;
    const chapters = [];
    const seen = new Set();
    let pending = [];
    let number = start;
    let message = '';
    announce(`Collecting chapters ${start} to ${end}.`);
    try {
      while (number <= end && !stopped) {
        if (chapters.length) await new Promise((resolve) => setTimeout(resolve, DELAY_MS));
        const chapter = await loadChapter(number);
        if (!chapter) {
          message = `Stopped: chapter ${number} did not show chapter text. Open it in this tab, complete any check, then paste the script again and start from chapter ${Math.floor(number)}. `;
          break;
        }
        chapters.push(chapter);
        seen.add(number);
        announce(`Captured chapter ${number}.`);
        pending = [...new Set([...pending, ...chapter.inBetween])].filter((extra) => !seen.has(extra)).sort((a, b) => a - b);
        number = pending.length ? pending.shift() : Math.floor(number) + 1;
      }
    } catch (error) {
      message = `Stopped at chapter ${number}: ${error.message}. `;
    } finally {
      running = false;
      announce(message + (chapters.length ? save(chapters) : 'No chapters were saved.'));
    }
  }
  function askAndCollect() {
    const start = Number(prompt('First chapter to collect:', String(config.start)));
    if (!start) {
      announce('Not started. Type wnsStart() to try again.');
      return;
    }
    const end = Number(prompt('Last chapter to collect:', String(Math.max(config.end, start))));
    if (!end) {
      announce('Not started. Type wnsStart() to try again.');
      return;
    }
    collect(start, end);
  }
  window.wnsStart = askAndCollect;
  window.wnsCollect = collect;
  window.wnsStop = () => {
    stopped = true;
    announce('Stopping after the current chapter.');
  };
  askAndCollect();
})();

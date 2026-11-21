"""Pure HTML and text-to-speech cleaning functions (no network, logging, or EPUB dependencies)."""

from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Comment, NavigableString

DEFAULT_STRIP_SELECTORS: Tuple[str, ...] = (
    'script',
    '.CTGIiSmv',
    '.L-MHncuP',
    '.vm-placement',
    'div[data-defid]',
    'div[data-mobid]',
    '[aria-hidden="true"]:has(button)',
)

# The start of a translator note: "TL:", "T/N:", "TL Note:".
TL_NOTE_START = r'T/?[LN](?:\s*note)?\s*:'
NOTE_TEXT = re.compile(
    rf'\b{TL_NOTE_START}|\btranslator[\'’]?s? note\b|\bchapter note(?:\s*:|\s+by\b)'
    r'|\bthanks for playing\b|^as always,? (?:thanks|thank you|enjoy)'
    r'|\bglossary of common\b|\bglossary link\b|\bcurrent schedule:|\bcheck out my patreon\b',
    re.IGNORECASE,
)
FOOTNOTE_LINE = re.compile(r'^(\d{1,2})\.(?!\d)\s*(.+)$')
LABELED_FOOTNOTE = re.compile(
    rf'^(\d{{1,2}})\.\s*(?:footnote\s*:|{TL_NOTE_START}|translator[\'’]?s?\s*note\s*:)',
    re.IGNORECASE,
)
FOOTNOTE_SUP = re.compile(r'^\[?(\d{1,3})\]?$|^\*+$')

SPLIT_WORD = re.compile(r'\b[A-Za-z]+(?:\.[A-Za-z]+)+\b')
ABBREVIATIONS = ('a.m', 'p.m', 'e.g', 'i.e', 'a.k.a')

# Text that TTS reads aloud but is not part of the story. Translator notes in brackets may hold
# one level of brackets themselves: (TL: Oppa (older brother) is ...).
NOTE_IN_BRACKETS = re.compile(rf'\s*\({TL_NOTE_START}(?:[^()]|\([^()]*\))*\)', re.IGNORECASE)
# A link in brackets, or on its own without the punctuation after it. Links may hold brackets: /wiki/Foo_(bar).
URL = r'https?://(?:[^\s()]|\([^\s()]*\))*'
LINK = re.compile(rf'\s*\(\s*{URL}\s*\)|\s*{URL}(?<![.,;:!?\'"”’])')
# A footnote mark after a word or a sentence ("collapse. [1] Deals"), but not values such as "Strength [15]" or "Level: [5]".
FOOTNOTE_MARK = re.compile(r'(?<=[A-Za-z])\[(\d{1,2})\]|(?<=[.,;!?”"’)])\s*\[(\d{1,2})\]')
# The "[email protected]" placeholder some sites put in place of censored words, decorative symbols,
# and tildes stretching a word ("Jack~~pot", "Oppa~"); ranges such as "Monday~Friday" stay.
NOISE = re.compile(
    r'\[email(?:\s|\xa0)protected\]|[★☆■□▲△▼▽◆◇●○►◄▶◀※♥♡❤♪♫]\s*'
    r'|(?<=[a-z])~+(?=[a-z])|(?<=[A-Za-z])~+(?=[\s.,;:!?”"’]|$)'
)
# A sentence that ends without a space before the next one ("neighborhood.Thanks", "Puk!Puk!"),
# but not inside links or addresses ("www.Example.com", "site.com/novel.Chapter").
RUN_ON_SENTENCE = re.compile(r'(?<![/\w])(?!www\.)([A-Za-z][a-z]+[.!?]+)(?=[A-Z][a-z])')

# A site's own name, which some sites add to the story as a watermark ("Search Example.com for the original").
SITE_NAME = re.compile(r'\b[A-Z][\w-]*\.(?:com|net|org)\b')
WATERMARK_WORDS = re.compile(
    r'\b(?:find us|search|the original|original source|on google|stolen|available on'
    r'|support us|quick look|great content)\b',
    re.IGNORECASE,
)
# A site name added after the end of a sentence or a value: '“Are you going?” Example.com', 'Attack: 25,020 Example.com'.
TRAILING_SITE_NAME = re.compile(r'(?<=[.!?…”"’\]\d])\s*' + SITE_NAME.pattern + r'\s*$')
SEPARATOR_LINE = re.compile(r'^[\s*=_~#·•—–-]+$')
CHAPTER_NUMBER_PATTERNS = (
    re.compile(r'^\d+$'),
    re.compile(r'^chapter\s+\d+$', re.IGNORECASE),
    re.compile(r'^\d+\.?$'),
)
LOOSE_TAGS = {'em', 'i', 'strong', 'b', 'br', 'span', 'a', 'sup', 'u', 's', 'font'}


def _texts(element: BeautifulSoup) -> List[NavigableString]:
    """The element's text nodes, without HTML comments."""
    return [text for text in element.find_all(string=True) if not isinstance(text, Comment)]


def _join_split_word(match: re.Match) -> str:
    word = match.group()
    parts = word.split('.')
    for short in ABBREVIATIONS:
        rest = word[len(short) + 1:]
        if word.lower() == short:
            return word
        # "a.m.The" is an abbreviation before the next word; "a.m.a.z.i.n.g" is a split word.
        if word.lower().startswith(short + '.') and len(rest) > 1 and '.' not in rest:
            return f'{word[:len(short) + 1]} {rest}'
    # "I.went" is a sentence missing a space; "I.t" is a split word.
    if parts[0] == 'I' and len(parts) == 2 and len(parts[1]) > 2:
        return word
    # A capitalised last part starts the next sentence ("G.o.d.You").
    next_sentence = parts.pop() if len(parts) > 2 and parts[-1][0].isupper() else ''
    # Join only when a single letter was split off and the word continues in lowercase,
    # so sentences missing a space ("first.I") and acronyms ("L.T.S") stay as they are.
    if any(len(part) == 1 for part in parts) and all(part[0].islower() for part in parts[1:]):
        return ''.join(parts) + (f'. {next_sentence}' if next_sentence else '')
    return word


def repair_split_words(element: BeautifulSoup) -> None:
    """Rejoin words split with dots, such as s.h.i.+eld or a.s.sa.s.sin, so TTS reads them as words."""
    for text in _texts(element):
        fixed = re.sub(r'\.([!?])\.\+', r'\1', text).replace('.+', '')
        fixed = SPLIT_WORD.sub(_join_split_word, fixed)
        if fixed != text:
            text.replace_with(fixed)


def tidy_for_speech(element: BeautifulSoup, keep_notes: bool = False, keep_footnotes: bool = False) -> None:
    """Remove text TTS should not read and add the missing space between run-on sentences.

    Bracketed translator notes stay when notes are included; links and footnote marks stay when footnotes are.
    """
    removed = [NOISE] + ([] if keep_notes else [NOTE_IN_BRACKETS]) + ([] if keep_footnotes else [LINK, FOOTNOTE_MARK])
    for text in _texts(element):
        fixed = text
        for pattern in removed:
            fixed = pattern.sub('', fixed)
        fixed = RUN_ON_SENTENCE.sub(r'\1 ', fixed)
        if fixed != text:
            text.replace_with(fixed)


def is_watermark(text: str) -> bool:
    """True for a short paragraph that advertises a site, such as 'We are "Example.com", find us on google.'"""
    rest = SITE_NAME.sub('', text)
    return bool(SITE_NAME.search(text)) and len(text.split()) <= 15 and (
        bool(WATERMARK_WORDS.search(rest)) or not re.search(r'[^\W\d_]', rest)
    )


def trim_paragraph(paragraph: BeautifulSoup) -> None:
    """Drop a site name added after a paragraph's last sentence, and the spaces at its start and end."""
    texts = _texts(paragraph)
    for text in reversed(texts):
        fixed = TRAILING_SITE_NAME.sub('', text).rstrip()
        if fixed != text:
            text.replace_with(fixed)
        if fixed:
            break
    for text in _texts(paragraph):
        fixed = text.lstrip()
        if fixed != text:
            text.replace_with(fixed)
        if fixed:
            break


def wrap_loose_text(content_div: BeautifulSoup) -> None:
    """Put story text that sits outside any <p> into a paragraph of its own.

    Text after the last paragraph is left out, as it is usually page scraps rather than story.
    """
    root = content_div.find(True)
    if root is None:
        return
    children = [child for child in root.children if not isinstance(child, Comment)]
    last_paragraph = max((i for i, child in enumerate(children) if getattr(child, 'name', None) == 'p'), default=-1)
    run = []
    for child in children[:last_paragraph + 1]:
        if isinstance(child, NavigableString) or child.name in LOOSE_TAGS:
            run.append(child)
            continue
        # Only runs with text of their own; a lone link or label, such as "Next Chapter", is not story.
        if any(isinstance(part, NavigableString) and part.strip() for part in run):
            paragraph = content_div.new_tag('p')
            run[0].insert_before(paragraph)
            for part in run:
                paragraph.append(part.extract())
        run = []


def clean_html_content(
    content_div: BeautifulSoup,
    strip_selectors: Iterable[str] = DEFAULT_STRIP_SELECTORS,
) -> BeautifulSoup:
    """Remove SSE comments, ads, scripts, and hidden page controls while preserving story markup."""
    content_str = re.sub(r'<!--sse-->.*?<!--/sse-->', '', str(content_div), flags=re.DOTALL)
    cleaned = BeautifulSoup(content_str, 'html.parser')
    for selector in strip_selectors:
        for element in cleaned.select(selector):
            element.decompose()
    return cleaned


def is_chapter_number(text: str) -> bool:
    stripped = text.strip()
    return any(pattern.match(stripped) for pattern in CHAPTER_NUMBER_PATTERNS)


def is_note_or_message(text: str, p_element: BeautifulSoup) -> bool:
    """Check if text or element content matches translator/chapter note patterns."""
    for strong in p_element.find_all(['strong', 'b']):
        strong_text = strong.text.strip().lower()
        if re.search(r'\b(?:notes?|t/n)\b', strong_text):
            return True
    return bool(NOTE_TEXT.search(text))


def is_math_exponent(sup) -> bool:
    """True when an unbracketed numeric <sup> follows a number or short unit (e.g. 10^2, 5m^2, <em>5m</em>^2)."""
    sup_text = sup.get_text(strip=True)
    if sup_text.startswith('[') and sup_text.endswith(']'):
        return False
    prev = sup.previous_sibling
    while prev is not None:
        if isinstance(prev, Comment):
            prev = prev.previous_sibling
            continue
        prev_text = str(prev) if isinstance(prev, NavigableString) else prev.get_text()
        if prev_text:
            return bool(re.search(r'(?:\d|\d\s*[A-Za-z]{1,3})$', prev_text))
        prev = prev.previous_sibling
    return False


def collect_footnote_refs(content_div: BeautifulSoup) -> Set[str]:
    """Collect footnote numbers referenced by <sup> tags or [n] markers in the chapter."""
    refs: Set[str] = set()
    for sup in content_div.find_all('sup'):
        match = re.fullmatch(r'\[?(\d{1,2})\]?', sup.get_text(strip=True))
        if match and not is_math_exponent(sup):
            refs.add(match.group(1))
    for text in _texts(content_div):
        for match in FOOTNOTE_MARK.finditer(text):
            refs.add(match.group(1) or match.group(2))
    return refs


def extract_chapter_title(title_elem: Optional[BeautifulSoup], source: str, simple_chapters: bool = False) -> Optional[str]:
    if not title_elem:
        return None
    if simple_chapters:
        url_match = re.search(r'(\d+(?:\.\d+)?)/?$', urlparse(source).path)
        return f'Chapter {url_match.group(1)}' if url_match else title_elem.text.strip()
    return title_elem.text.strip()


def extract_chapter_parts(
    content_div: BeautifulSoup,
    *,
    include_notes: bool = False,
    include_footnotes: bool = False,
    strip_selectors: Iterable[str] = DEFAULT_STRIP_SELECTORS,
    debug_log: Optional[Callable[[str], None]] = None,
) -> Tuple[List[str], List[str], Dict[str, str], List[str]]:
    """Clean and split chapter HTML into story paragraphs, chapter notes, footnotes, and debug notes."""
    cleaned = clean_html_content(content_div, strip_selectors=strip_selectors)
    wrap_loose_text(cleaned)
    referenced_footnotes = collect_footnote_refs(cleaned)
    tidy_for_speech(cleaned, keep_notes=include_notes, keep_footnotes=include_footnotes)

    paragraphs: List[str] = []
    after_separator: List[str] = []
    footnotes: Dict[str, str] = {}
    debug_notes: List[str] = []

    if debug_log is not None:
        debug_log('\nRaw content before processing:')
        debug_log(cleaned.prettify())

    for p in cleaned.find_all('p'):
        trim_paragraph(p)
        text = p.text.strip()
        if not text:
            continue

        if debug_log is not None:
            debug_log(f'\nProcessing paragraph: {text[:100]}...')

        fn_match = FOOTNOTE_LINE.match(text)
        is_footnote = bool(
            fn_match and (fn_match.group(1) in referenced_footnotes or LABELED_FOOTNOTE.match(text))
        )

        if SEPARATOR_LINE.match(text):
            # A scene break: a line in the book instead of symbols TTS would read out.
            if paragraphs and paragraphs[-1] != '<hr/>':
                paragraphs.append('<hr/>')
        elif is_watermark(text):
            debug_notes.append(f'Not story: {text}')
        elif text.startswith('T/L:') or text.startswith('T/N:'):
            if include_notes:
                after_separator.append(str(p))
            debug_notes.append(f'Start note: {str(p)}')
            if debug_log is not None:
                debug_log('Found start note')
        elif is_footnote:
            num, body = fn_match.group(1), fn_match.group(2).strip()
            if include_footnotes:
                footnotes[num] = body
            debug_notes.append(f'Footnote: {text}')
            if debug_log is not None:
                debug_log('Found footnote')
        elif is_note_or_message(text, p):
            if include_notes:
                after_separator.append(str(p))
            debug_notes.append(f'End note: {str(p)}')
            if debug_log is not None:
                debug_log('Found end note')
        else:
            if not is_chapter_number(text):
                paragraphs.append(str(p))
                if debug_log is not None:
                    debug_log('Added to main content')

    if debug_log is not None:
        debug_log('\nProcessing results:')
        debug_log(f'Main paragraphs: {len(paragraphs)}')
        debug_log(f'After separator: {len(after_separator)}')
        debug_log(f'Footnotes: {len(footnotes)}')
        debug_log(f'Debug notes: {len(debug_notes)}')
        if paragraphs:
            debug_log('\nFirst paragraph:')
            debug_log(paragraphs[0])
            debug_log('\nLast paragraph:')
            debug_log(paragraphs[-1])

    return paragraphs, after_separator, footnotes, debug_notes


def format_chapter_content(
    paragraphs: List[str],
    footnotes: Dict[str, str],
    *,
    include_footnotes: bool = False,
) -> str:
    """Link or strip footnote superscripts in cleaned chapter paragraphs."""
    processed_paragraphs: List[str] = []
    assigned_ref_ids: Set[str] = set()
    for p in paragraphs:
        if '<sup' not in p.lower():
            processed_paragraphs.append(p)
            continue
        soup_p = BeautifulSoup(p, 'html.parser')
        for sup in soup_p.find_all('sup'):
            ref_text = sup.get_text(strip=True)
            sup_match = FOOTNOTE_SUP.fullmatch(ref_text)
            ref_key = sup_match.group(1) if sup_match and sup_match.group(1) else ref_text
            if include_footnotes:
                if ref_key in footnotes and not is_math_exponent(sup):
                    if ref_key not in assigned_ref_ids:
                        sup['id'] = f'ref-{ref_key}'
                        assigned_ref_ids.add(ref_key)
                    sup.wrap(soup_p.new_tag('a', href=f'#footnote-{ref_key}'))
            elif sup_match and not is_math_exponent(sup):
                sup.decompose()
        processed_paragraphs.append(str(soup_p))
    return '\n'.join(processed_paragraphs)


def parse_chapter_elements(
    title_elem: Optional[BeautifulSoup],
    content_div: Optional[BeautifulSoup],
    source: str,
    *,
    include_notes: bool = False,
    include_footnotes: bool = False,
    simple_chapters: bool = False,
    min_content_length: int = 100,
    strip_selectors: Iterable[str] = DEFAULT_STRIP_SELECTORS,
    debug_log: Optional[Callable[[str], None]] = None,
) -> Tuple[str, str, List[str], Dict[str, str]]:
    """Turn a chapter's title element and content container into EPUB-ready parts."""
    for element in (title_elem, content_div):
        if element is not None:
            repair_split_words(element)
    chapter_title = extract_chapter_title(title_elem, source, simple_chapters=simple_chapters)
    if not chapter_title:
        raise ValueError('Chapter title not found')
    if not content_div:
        raise ValueError('Content container not found')

    if debug_log is not None:
        debug_log(f'\nProcessing chapter: {chapter_title}')
        debug_log(f'Source: {source}')

    paragraphs, after_separator, footnotes, debug_notes = extract_chapter_parts(
        content_div,
        include_notes=include_notes,
        include_footnotes=include_footnotes,
        strip_selectors=strip_selectors,
        debug_log=debug_log,
    )

    if not paragraphs:
        if debug_log is not None:
            debug_log('\nNo paragraphs found in content. Raw content:')
            debug_log(content_div.prettify())
        raise ValueError('No valid content found')

    content = format_chapter_content(paragraphs, footnotes, include_footnotes=include_footnotes)
    if len(BeautifulSoup(content, 'html.parser').get_text(strip=True)) < min_content_length:
        raise ValueError('Chapter content is shorter than the configured minimum length')

    if debug_log is not None and debug_notes:
        debug_log(f'\nExcluded notes for {chapter_title}:')
        for note in debug_notes:
            debug_log(f'Note: {note}')

    return chapter_title, content, after_separator, footnotes

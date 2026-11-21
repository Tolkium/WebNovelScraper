"""Phase 1 regression tests: rate limiting, retry classification, XHTML safety, TTS reader, saved imports, and JS collector."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from bs4 import BeautifulSoup
from ebooklib import epub

from webnovel import (
    EpubCreator,
    JobSettings,
    RateLimiter,
    ScraperCancelled,
    SiteBlocked,
    WebNovelScraper,
    is_browser_check,
    load_saved_chapters,
    parse_retry_after,
    read_epub,
    run_job,
    scan_saved_chapters,
)
from webnovel.epub import _extract_readable_text
from WebNovelScraper import main as cli_main


STORY_BODY = '<p>' + 'The hero forged a legendary sword in the quiet smithy. ' * 5 + '</p>'


class FakeResponse:
    def __init__(self, status_code, headers=None, text=''):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class RetryFixtureHandler(BaseHTTPRequestHandler):
    counts = {}

    def do_GET(self):
        RetryFixtureHandler.counts[self.path] = RetryFixtureHandler.counts.get(self.path, 0) + 1
        attempt = RetryFixtureHandler.counts[self.path]

        if self.path.startswith('/missing/'):
            self.send_response(404)
            body = b'Not found'
        elif self.path.startswith('/status-400/'):
            self.send_response(400)
            body = b'Bad request'
        elif self.path.startswith('/status-401/'):
            self.send_response(401)
            body = b'Unauthorized'
        elif self.path.startswith('/status-410/'):
            self.send_response(410)
            body = b'Gone'
        elif self.path.startswith('/status-408/'):
            if attempt == 1:
                self.send_response(408)
                body = b'Request Timeout'
            else:
                self.send_response(200)
                body = (
                    '<html><span class="chapter-title">Recovered 408</span>'
                    f'<div id="chapter-container">{STORY_BODY}</div></html>'
                ).encode()
        elif self.path.startswith('/status-500/'):
            if attempt == 1:
                self.send_response(500)
                body = b'Server Error'
            else:
                self.send_response(200)
                body = (
                    '<html><span class="chapter-title">Recovered 500</span>'
                    f'<div id="chapter-container">{STORY_BODY}</div></html>'
                ).encode()
        elif self.path.startswith('/bad-selector/'):
            self.send_response(200)
            body = b'<html><body><p>Wrong layout</p></body></html>'
        elif self.path.startswith('/cf-503-outage/'):
            if attempt == 1:
                self.send_response(503)
                self.send_header('Server', 'cloudflare')
                self.send_header('Retry-After', '0')
                body = b'<html><body>503 Service Temporarily Unavailable</body></html>'
            else:
                self.send_response(200)
                body = (
                    '<html><span class="chapter-title">Recovered 503</span>'
                    f'<div id="chapter-container">{STORY_BODY}</div></html>'
                ).encode()
        elif self.path.startswith('/cf-503-challenge/'):
            self.send_response(503)
            self.send_header('Server', 'cloudflare')
            body = b'<html><title>Just a moment...</title><div id="cf-browser-verification"></div></html>'
        elif self.path.startswith('/rate-limited/'):
            if attempt == 1:
                self.send_response(429)
                self.send_header('Retry-After', '2')
                body = b'Too Many Requests'
            else:
                self.send_response(200)
                body = (
                    '<html><span class="chapter-title">Recovered 429</span>'
                    f'<div id="chapter-container">{STORY_BODY}</div></html>'
                ).encode()
        else:
            self.send_response(200)
            body = (
                '<html><span class="chapter-title">Chapter OK</span>'
                f'<div id="chapter-container">{STORY_BODY}</div></html>'
            ).encode()

        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RateLimiterAndRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), RetryFixtureHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        RetryFixtureHandler.counts.clear()

    def test_rate_limiter_spaces_requests_evenly_with_post_sleep_monotonic_clock(self):
        now = [0.0]
        starts = []

        def fake_clock():
            return now[0]

        limiter = RateLimiter(requests_per_second=1.0, clock=fake_clock)

        def fake_sleep(seconds):
            if seconds > 0:
                now[0] += seconds

        limiter.sleep = fake_sleep

        for _ in range(5):
            limiter.wait()
            starts.append(now[0])

        self.assertEqual(starts, [0.0, 1.0, 2.0, 3.0, 4.0])

    def test_parse_retry_after_handles_seconds_http_date_and_invalid_values(self):
        self.assertEqual(parse_retry_after('5'), 5.0)
        self.assertEqual(parse_retry_after(' 0 '), 0.0)
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=12), usegmt=True)
        parsed = parse_retry_after(future)
        self.assertIsNotNone(parsed)
        self.assertTrue(5.0 <= parsed <= 15.0)
        self.assertIsNone(parse_retry_after('not-a-date'))
        self.assertIsNone(parse_retry_after(None))

    def test_retry_delay_caps_and_cancel_during_retry_sleep(self):
        limiter = RateLimiter(requests_per_second=1.0)
        self.assertEqual(limiter.retry_delay(0), 1.0)
        self.assertEqual(limiter.retry_delay(10), 60.0)
        self.assertEqual(limiter.retry_delay(0, retry_after=999.0), 300.0)

        cancel = Event()
        settings = JobSettings(retries=3, requests_per_second=100.0)
        with WebNovelScraper(settings=settings, cancel_event=cancel) as scraper:
            original_sleep = scraper.rate_limiter.sleep

            def cancel_when_sleeping(seconds):
                if seconds >= 1.0:
                    cancel.set()
                return original_sleep(seconds)

            scraper.rate_limiter.sleep = cancel_when_sleeping
            with self.assertRaises(ScraperCancelled):
                scraper.get_chapter_content(f'{self.base}/rate-limited/chapter-1')

    def test_408_and_500_retry_while_400_401_410_and_unexpected_parse_errors_fail_once(self):
        settings = JobSettings(retries=3, requests_per_second=100.0)
        with WebNovelScraper(settings=settings) as scraper:
            scraper.rate_limiter.sleep = lambda s: None
            self.assertEqual(scraper.get_chapter_content(f'{self.base}/status-408/chapter-1')[0], 'Recovered 408')
            self.assertEqual(RetryFixtureHandler.counts['/status-408/chapter-1'], 2)

            self.assertEqual(scraper.get_chapter_content(f'{self.base}/status-500/chapter-1')[0], 'Recovered 500')
            self.assertEqual(RetryFixtureHandler.counts['/status-500/chapter-1'], 2)

            for code in (400, 401, 410):
                self.assertEqual(scraper.get_chapter_content(f'{self.base}/status-{code}/chapter-1'), (None, None, None, None))
                self.assertEqual(RetryFixtureHandler.counts[f'/status-{code}/chapter-1'], 1)

            with patch.object(scraper, 'parse_chapter', side_effect=RecursionError('deep markup')):
                self.assertEqual(scraper.get_chapter_content(f'{self.base}/ok/chapter-1'), (None, None, None, None))
                self.assertEqual(RetryFixtureHandler.counts['/ok/chapter-1'], 1)

    def test_cloudflare_503_outage_is_not_treated_as_browser_check(self):
        outage = FakeResponse(503, {'server': 'cloudflare'}, '<html>503 Service Temporarily Unavailable</html>')
        challenge = FakeResponse(503, {'server': 'cloudflare'}, '<html><title>Just a moment...</title></html>')
        cf_403 = FakeResponse(403, {'server': 'cloudflare'}, '<html>Error 1020 Access denied</html>')
        mitigated = FakeResponse(403, {'cf-mitigated': 'challenge'}, 'blocked')
        self.assertFalse(is_browser_check(outage))
        self.assertTrue(is_browser_check(challenge))
        self.assertTrue(is_browser_check(cf_403))
        self.assertTrue(is_browser_check(mitigated))

    def test_404_and_parse_errors_fail_immediately_without_retries_or_compounding_backoff(self):
        settings = JobSettings(retries=3, requests_per_second=100.0)
        sleeps = []
        with WebNovelScraper(settings=settings) as scraper:
            original_sleep = scraper.rate_limiter.sleep

            def record_sleep(seconds):
                if seconds > 0.05:
                    sleeps.append(seconds)
                original_sleep(0)

            scraper.rate_limiter.sleep = record_sleep
            for n in range(1, 6):
                result = scraper.get_chapter_content(f'{self.base}/missing/chapter-{n}')
                self.assertEqual(result, (None, None, None, None))
                self.assertEqual(RetryFixtureHandler.counts[f'/missing/chapter-{n}'], 1)

            parse_result = scraper.get_chapter_content(f'{self.base}/bad-selector/chapter-1')
            self.assertEqual(parse_result, (None, None, None, None))
            self.assertEqual(RetryFixtureHandler.counts['/bad-selector/chapter-1'], 1)

        self.assertEqual(sleeps, [])

    def test_cloudflare_503_outage_retries_and_recovers_while_challenge_raises(self):
        settings = JobSettings(retries=3, requests_per_second=100.0)
        with WebNovelScraper(settings=settings) as scraper:
            scraper.rate_limiter.sleep = lambda s: None
            title, content, _, _ = scraper.get_chapter_content(f'{self.base}/cf-503-outage/chapter-1')
            self.assertEqual(title, 'Recovered 503')
            self.assertIn('legendary sword', content)
            self.assertEqual(RetryFixtureHandler.counts['/cf-503-outage/chapter-1'], 2)

            with self.assertRaises(SiteBlocked):
                scraper.get_chapter_content(f'{self.base}/cf-503-challenge/chapter-1')
            self.assertEqual(RetryFixtureHandler.counts['/cf-503-challenge/chapter-1'], 1)

    def test_429_honors_retry_after_header_and_recovers(self):
        settings = JobSettings(retries=3, requests_per_second=100.0)
        sleeps = []
        with WebNovelScraper(settings=settings) as scraper:
            scraper.rate_limiter.sleep = lambda s: sleeps.append(s) if s > 0 else None
            title, _, _, _ = scraper.get_chapter_content(f'{self.base}/rate-limited/chapter-1')
            self.assertEqual(title, 'Recovered 429')
        self.assertTrue(any(delay >= 2.0 for delay in sleeps), f'Expected >=2s Retry-After delay, got {sleeps}')

    def test_callback_log_handler_filters_debug_html_while_file_log_keeps_it(self):
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / 'debug.log'
            gui_messages = []
            with WebNovelScraper(debug_mode=True, log_callback=gui_messages.append, log_path=log_path) as scraper:
                page = BeautifulSoup(
                    f'<span class="chapter-title">Ch 1</span><div id="chapter-container">{STORY_BODY}</div>',
                    'html.parser',
                )
                scraper.parse_chapter(page.select_one('.chapter-title'), page.select_one('#chapter-container'), '1')
            file_text = log_path.read_text(encoding='utf-8')
            self.assertIn('Raw content before processing', file_text)
            self.assertFalse(any('Raw content before processing' in msg for msg in gui_messages))


class FootnoteXhtmlAndReaderTests(unittest.TestCase):
    def test_footnotes_are_escaped_valid_xhtml_and_number_is_not_duplicated(self):
        creator = EpubCreator('Test', settings=JobSettings(include_footnotes=True))
        chapter = creator.add_chapter(
            {
                'title': 'Chapter 1',
                'content': '<p>Story text.<a href="#footnote-1"><sup id="ref-1">1</sup></a></p>',
                'notes': [],
                'footnotes': {'1': 'Skill <Blacksmith> & "Precision"'},
            },
            1,
        )
        # Wrap as XML document and parse strictly with ElementTree.
        root = ET.fromstring(f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{chapter.content}</body></html>')
        fn_p = root.find('.//{http://www.w3.org/1999/xhtml}p[@id="footnote-1"]')
        self.assertIsNotNone(fn_p)
        rendered_text = ''.join(fn_p.itertext())
        self.assertIn('Skill <Blacksmith> & "Precision"', rendered_text)
        self.assertNotIn('1 1.', rendered_text)

    def test_unreferenced_litrpg_numbered_lines_stay_while_real_footnotes_including_10_are_extracted(self):
        html = (
            '<span class="chapter-title">Chapter 1</span>'
            '<div id="chapter-container">'
            + STORY_BODY
            + '<p>1. Sword Mastery: Increases attack power by 15%.</p>'
            + '<p>5. The time scale between Satisfy and reality is 1:8.</p>'
            + '<p>He felt the 1<sup>st</sup> strike land.<sup class="fn">\n10\n</sup></p>'
            + '<p>10. A tenth footnote explaining a pun.</p>'
            + '</div>'
        )
        # Footnotes ON
        soup_on = BeautifulSoup(html, 'html.parser')
        with WebNovelScraper(settings=JobSettings(include_footnotes=True)) as scraper:
            _, content_on, _, footnotes = scraper.parse_chapter(
                soup_on.select_one('.chapter-title'), soup_on.select_one('#chapter-container'), '1'
            )
        self.assertIn('1. Sword Mastery: Increases attack power by 15%.', content_on)
        self.assertIn('5. The time scale between Satisfy and reality is 1:8.', content_on)
        self.assertEqual(footnotes, {'10': 'A tenth footnote explaining a pun.'})
        self.assertIn('href="#footnote-10"', content_on)

        # Footnotes OFF: removes multiline <sup class="fn">10</sup> but keeps 1<sup>st</sup> and numbered story lines.
        soup_off = BeautifulSoup(html, 'html.parser')
        with WebNovelScraper(settings=JobSettings(include_footnotes=False)) as scraper:
            _, content_off, _, _ = scraper.parse_chapter(
                soup_off.select_one('.chapter-title'), soup_off.select_one('#chapter-container'), '1'
            )
        self.assertIn('1<sup>st</sup>', content_off)
        self.assertNotIn('class="fn"', content_off)
        self.assertIn('1. Sword Mastery:', content_off)
        self.assertNotIn('tenth footnote', content_off)

    def test_math_exponents_and_colon_brackets_are_not_footnote_refs_while_bracketed_sup_links(self):
        html = (
            '<span class="chapter-title">Chapter 1</span>'
            '<div id="chapter-container">'
            + STORY_BODY
            + '<p>The room covered <em>5m</em><sup>2</sup> and Level: [5].</p>'
            + '<p>1.5 meters remained between them.</p>'
            + '<p>2. Agility: 15</p>'
            + '<p>5. Strength: 20</p>'
            + '<p>He shouted a phrase<sup>[1]</sup> and repeated it<sup>1</sup>; 10<sup>[3]</sup> stood nearby.</p>'
            + '<p>Story.[4]</p>'
            + '<p>1. Translation note for the phrase.</p>'
            + '<p>3. Note for bracketed ten.</p>'
            + '<p>4. Bracket-only note.</p>'
            + '</div>'
        )
        soup_on = BeautifulSoup(html, 'html.parser')
        with WebNovelScraper(settings=JobSettings(include_footnotes=True)) as scraper:
            _, content_on, _, footnotes = scraper.parse_chapter(
                soup_on.select_one('.chapter-title'), soup_on.select_one('#chapter-container'), '1'
            )
        self.assertIn('2. Agility: 15', content_on)
        self.assertIn('5. Strength: 20', content_on)
        self.assertIn('1.5 meters remained between them.', content_on)
        self.assertIn('<em>5m</em><sup>2</sup>', content_on)
        self.assertIn('Level: [5].', content_on)
        self.assertEqual(footnotes, {
            '1': 'Translation note for the phrase.',
            '3': 'Note for bracketed ten.',
            '4': 'Bracket-only note.',
        })
        # Duplicate <sup>1</sup> only gets one id="ref-1"; bracket-only [4] gets no dangling backlink.
        self.assertEqual(content_on.count('id="ref-1"'), 1)
        self.assertEqual(content_on.count('href="#footnote-1"'), 2)
        self.assertIn('id="ref-3"', content_on)
        creator = EpubCreator('FnTest', settings=JobSettings(include_footnotes=True))
        chap = creator.add_chapter({'title': 'Ch 1', 'content': content_on, 'notes': [], 'footnotes': footnotes}, 1)
        self.assertIn('href="#ref-1"', chap.content)
        self.assertIn('href="#ref-3"', chap.content)
        self.assertNotIn('href="#ref-4"', chap.content)

        soup_off = BeautifulSoup(html, 'html.parser')
        with WebNovelScraper(settings=JobSettings(include_footnotes=False)) as scraper:
            _, content_off, _, _ = scraper.parse_chapter(
                soup_off.select_one('.chapter-title'), soup_off.select_one('#chapter-container'), '1'
            )
        self.assertIn('<em>5m</em><sup>2</sup>', content_off)
        self.assertIn('Level: [5].', content_off)
        self.assertIn('1.5 meters remained between them.', content_off)
        self.assertIn('2. Agility: 15', content_off)
        self.assertIn('5. Strength: 20', content_off)
        self.assertNotIn('[1]', content_off)
        self.assertNotIn('[3]', content_off)

    def test_reader_preserves_inline_formatting_and_non_p_structures_for_tts(self):
        with TemporaryDirectory() as tmp:
            epub_path = Path(tmp) / 'tts_reader.epub'
            creator = EpubCreator('TTS Book', settings=JobSettings(chapter_headings=True))
            ch = creator.add_chapter(
                {
                    'title': 'Chapter 1',
                    'content': (
                        '<p>He said <em>hello</em> to <strong>everyone</strong> in the '
                        '<span class="guild">hall</span> today.</p>'
                        '<p>Line one.<br/>Line two.</p>'
                    ),
                    'notes': [],
                    'footnotes': {},
                },
                1,
            )
            creator.finalize([ch])
            epub.write_epub(str(epub_path), creator.book, {})

            title, chapters = read_epub(epub_path)
            self.assertEqual(title, 'TTS Book')
            text = chapters[0][1]
            self.assertIn('He said hello to everyone in the hall today.', text)
            self.assertIn('Line one.\nLine two.', text)

        # Third-party EPUB markup: loose body text, mixed parent+child blocks, <section>, and <td>.
        self.assertEqual(
            _extract_readable_text(BeautifulSoup('<h1>Title</h1>Loose body text.<p>Para.</p>', 'html.parser')),
            'Title\n\nLoose body text.\n\nPara.',
        )
        self.assertEqual(
            _extract_readable_text(BeautifulSoup('<div>Intro text<p>Para.</p></div>', 'html.parser')),
            'Intro text\n\nPara.',
        )
        self.assertEqual(
            _extract_readable_text(BeautifulSoup('<h2>T</h2><section>Section text only</section>', 'html.parser')),
            'T\n\nSection text only',
        )
        self.assertEqual(
            _extract_readable_text(BeautifulSoup('<h2>T</h2><table><tr><td>Cell A</td><td>Cell B</td></tr></table>', 'html.parser')),
            'T\n\nCell A\n\nCell B',
        )
        # Hard-wrapped XHTML source lines inside one paragraph must stay on a single TTS sentence line.
        self.assertEqual(
            _extract_readable_text(BeautifulSoup('<p>First part of\n  a wrapped <em>sentence</em>\n  continues here.</p>', 'html.parser')),
            'First part of a wrapped sentence continues here.',
        )

    def test_epub_identifier_differs_across_chapter_ranges_and_matches_same_range(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            saved = folder / 'novel.html'
            saved.write_text(
                '<!doctype html><html><head><meta charset="utf-8"></head><body>'
                f'<section class="wns-chapter" data-number="1"><span class="chapter-title">Ch 1</span><div class="chapter-container">{STORY_BODY}</div></section>'
                f'<section class="wns-chapter" data-number="2"><span class="chapter-title">Ch 2</span><div class="chapter-container">{STORY_BODY}</div></section>'
                '</body></html>',
                encoding='utf-8',
            )
            s1 = JobSettings(title='Overgeared', start=1, end=1, output_dir=str(folder), filename='vol1.epub', saved_file=str(saved))
            s1_again = JobSettings(title='Overgeared', start=1, end=1, output_dir=str(folder), filename='vol1_copy.epub', saved_file=str(saved))
            s2 = JobSettings(title='Overgeared', start=2, end=2, output_dir=str(folder), filename='vol2.epub', saved_file=str(saved))

            r1 = run_job(s1, Event(), lambda m: None, lambda p: None, folder / '1.log')
            r1b = run_job(s1_again, Event(), lambda m: None, lambda p: None, folder / '1b.log')
            r2 = run_job(s2, Event(), lambda m: None, lambda p: None, folder / '2.log')

            id1 = epub.read_epub(str(r1.path)).get_metadata('DC', 'identifier')[0][0]
            id1b = epub.read_epub(str(r1b.path)).get_metadata('DC', 'identifier')[0][0]
            id2 = epub.read_epub(str(r2.path)).get_metadata('DC', 'identifier')[0][0]
            self.assertEqual(id1, id1b)
            self.assertNotEqual(id1, id2)

    def test_saved_identifier_distinguishes_same_named_folders_and_matches_between_cli_and_desktop(self):
        import os
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_a = root / 'a' / 'batch'
            batch_b = root / 'b' / 'batch'
            batch_a.mkdir(parents=True)
            batch_b.mkdir(parents=True)
            html_a = (
                '<!doctype html><html><head><meta charset="utf-8"></head><body>'
                f'<section class="wns-chapter" data-number="1"><span class="chapter-title">Ch 1</span><div class="chapter-container">{STORY_BODY}</div></section>'
                f'<section class="wns-chapter" data-number="2"><span class="chapter-title">Ch 2</span><div class="chapter-container">{STORY_BODY}</div></section>'
                '</body></html>'
            )
            (batch_a / 'chapters.html').write_text(html_a, encoding='utf-8')
            (batch_b / 'chapters.html').write_text(html_a, encoding='utf-8')

            out_a = run_job(
                JobSettings(title='Same Name', start=1, end=2, output_dir=str(root), filename='a.epub', saved_file=str(batch_a)),
                Event(), lambda m: None, lambda p: None, root / 'a.log',
            )
            out_b = run_job(
                JobSettings(title='Same Name', start=1, end=2, output_dir=str(root), filename='b.epub', saved_file=str(batch_b)),
                Event(), lambda m: None, lambda p: None, root / 'b.log',
            )
            id_a = epub.read_epub(str(out_a.path)).get_metadata('DC', 'identifier')[0][0]
            id_b = epub.read_epub(str(out_b.path)).get_metadata('DC', 'identifier')[0][0]
            self.assertNotEqual(id_a, id_b)

            # Unrestricted CLI build of batch_a matches Desktop auto-filled range (1..2).
            prev_cwd = os.getcwd()
            os.chdir(root)
            try:
                with patch('sys.argv', ['WebNovelScraper.py', '--from-folder', str(batch_a), '--title', 'Same Name', '--simple-chapters']), \
                        patch('builtins.input', return_value=''):
                    cli_main()
            finally:
                os.chdir(prev_cwd)
            id_cli = epub.read_epub(str(root / 'same_name.epub')).get_metadata('DC', 'identifier')[0][0]
            self.assertEqual(id_cli, id_a)


class SavedChaptersDiagnosticsTests(unittest.TestCase):
    def test_scan_saved_chapters_handles_reversed_attribute_order(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'reversed.html'
            path.write_text(
                '<!doctype html><html><head><meta content="https://site/ch-{n}" name="source"></head><body>'
                f'<section data-number="12" class="wns-chapter"><span class="chapter-title">Ch 12</span><div class="chapter-container">{STORY_BODY}</div></section>'
                f'<section class="wns-chapter" data-number="13"><span class="chapter-title">Ch 13</span><div class="chapter-container">{STORY_BODY}</div></section>'
                '</body></html>',
                encoding='utf-8',
            )
            scanned = scan_saved_chapters(path)
            loaded = load_saved_chapters(path)
            self.assertEqual(scanned['count'], len(loaded.chapters))
            self.assertEqual((scanned['first'], scanned['last']), (12, 13))
            self.assertEqual(scanned['sources'], ['https://site/ch-{n}'])
    def test_distinct_1_1_and_1_10_invalid_numbers_duplicates_mixed_sources_and_missing_gaps(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            short_body = '<p>Short version.</p>'
            long_body = '<p>' + 'Much longer complete version of chapter one story text. ' * 5 + '</p>'
            (folder / 'a.html').write_text(
                '<!doctype html><html><head><meta charset="utf-8">'
                '<meta name="source" content="https://site-a/novel/book/ch-{n}"></head><body>'
                f'<section class="wns-chapter" data-number="1"><span class="chapter-title">Ch 1</span><div class="chapter-container">{short_body}</div></section>'
                f'<section class="wns-chapter" data-number="1.1"><span class="chapter-title">Ch 1.1</span><div class="chapter-container">{STORY_BODY}</div></section>'
                f'<section class="wns-chapter" data-number="1.10"><span class="chapter-title">Ch 1.10</span><div class="chapter-container">{STORY_BODY}</div></section>'
                f'<section class="wns-chapter" data-number="bad"><span class="chapter-title">Bad</span><div class="chapter-container">{STORY_BODY}</div></section>'
                '</body></html>',
                encoding='utf-8',
            )
            (folder / 'b.html').write_text(
                '<!doctype html><html><head><meta charset="utf-8">'
                '<meta name="source" content="https://site-b/novel/other/ch-{n}"></head><body>'
                f'<section class="wns-chapter" data-number="1"><span class="chapter-title">Ch 1</span><div class="chapter-container">{long_body}</div></section>'
                f'<section class="wns-chapter" data-number="3"><span class="chapter-title">Ch 3</span><div class="chapter-container">{STORY_BODY}</div></section>'
                f'<section class="wns-chapter" data-number="3.1"><span class="chapter-title">Ch 3.1</span><div class="chapter-container">{STORY_BODY}</div></section>'
                '</body></html>',
                encoding='utf-8',
            )

            loaded = load_saved_chapters(folder)
            self.assertEqual([c.raw_number for c in loaded.chapters], ['1', '1.1', '1.10', '3', '3.1'])
            self.assertEqual(loaded.invalid_count, 1)
            self.assertEqual(loaded.duplicates, ['1'])
            self.assertEqual(len(loaded.sources), 2)
            self.assertIn('Much longer complete version', loaded.chapters[0].section.get_text())

            logs = []
            settings = JobSettings(
                title='Gap Test',
                start=1,
                end=3,
                output_dir=str(folder),
                saved_file=str(folder),
                simple_chapters=True,
            )
            result = run_job(settings, Event(), logs.append, lambda p: None, folder / 'job.log')
            self.assertEqual(result.success, 5)
            self.assertEqual(result.failed, [2])
            self.assertTrue(any('different sources' in line for line in logs))
            self.assertTrue(any('duplicate saved chapters' in line for line in logs))
            self.assertTrue(any('saved chapter 2 is missing' in line for line in logs))
            _, chapters = read_epub(result.path)
            self.assertEqual([name for name, _ in chapters], ['Chapter 1', 'Chapter 1.1', 'Chapter 1.10', 'Chapter 3', 'Chapter 3.1'])


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for browser collector behavior tests')
class BrowserCollectorBehaviorTests(unittest.TestCase):
    def test_collector_retries_transient_errors_honors_stop_during_wait_and_includes_end_subchapters(self):
        script_path = Path(__file__).resolve().parent.parent / 'browser' / 'collect-chapters.js'
        script_source = script_path.read_text(encoding='utf-8')
        # Replace DELAY_MS with 20ms for fast deterministic tests and inject SETTINGS.
        test_settings = {
            'chapterUrl': 'https://example.test/novel/demo/chapter-{n}',
            'name': 'demo',
            'start': 1,
            'end': 1,
            'titleSelector': 'span.chapter-title',
            'contentSelector': '#chapter-container',
        }
        patched = script_source.replace('/*SETTINGS*/null', json.dumps(test_settings), 1).replace('const DELAY_MS = 3000;', 'const DELAY_MS = 20;', 1)

        harness = r"""
const fs = require('fs');
const assert = require('assert');
const script = fs.readFileSync(process.argv[2], 'utf8');

function makeDoc(html) {
  const titleMatch = html.match(/<span class="chapter-title">([^<]*)<\/span>/);
  const contentMatch = html.match(/<div id="chapter-container">([\s\S]*?)<\/div>/);
  const links = [...html.matchAll(/<a href="([^"]+)">/g)].map((m) => ({ getAttribute: () => m[1] }));
  const contentEl = contentMatch ? {
    innerHTML: contentMatch[1],
    textContent: contentMatch[1].replace(/<[^>]+>/g, ''),
    querySelectorAll: () => [],
  } : null;
  return {
    querySelector: (sel) => {
      if (sel === '#chapter-container') return contentEl;
      if (sel === 'span.chapter-title' && titleMatch) return { textContent: titleMatch[1] };
      return null;
    },
    querySelectorAll: (sel) => (sel === 'a[href]' ? links : []),
  };
}

let savedFiles = [];
let fetchCalls = [];
let announcements = [];
let mode = 'retry_and_subchapter';
let attempts = {};

global.window = global;
global.location = { href: 'https://example.test/novel/demo/chapter-1', origin: 'https://example.test', pathname: '/novel/demo/chapter-1' };
global.console = { ...console, log: (msg) => announcements.push(String(msg)) };
global.document = {
  body: { append: () => {} },
  createElement: () => ({
    style: {},
    setAttribute: () => {},
    click: function () { savedFiles.push(this.download); },
    remove: () => {},
  }),
};
global.Blob = class { constructor(parts) { this.parts = parts; } };
global.URL.createObjectURL = () => 'blob:mock';
global.URL.revokeObjectURL = () => {};
global.DOMParser = class { parseFromString(text) { return makeDoc(text); } };
global.prompt = () => null;
global.fetch = async (url) => {
  fetchCalls.push(url);
  attempts[url] = (attempts[url] || 0) + 1;
  if (mode === 'retry_and_subchapter') {
    if (url.endsWith('/chapter-1') && attempts[url] === 1) {
      const httpDate = new Date(Date.now() + 10).toUTCString();
      return { ok: false, status: 429, redirected: false, headers: { get: (k) => (k === 'retry-after' ? httpDate : null) } };
    }
    if (url.endsWith('/chapter-1')) {
      return {
        ok: true, status: 200, redirected: false, headers: { get: () => null },
        text: async () => '<span class="chapter-title">Chapter 1</span><div id="chapter-container"><p>Story 1</p></div>'
          + '<a href="/novel/demo/chapter-1.10">Part 10</a><a href="/novel/demo/chapter-1.2">Part 2</a><a href="/novel/demo/chapter-1.1">Part 1</a>',
      };
    }
    if (url.endsWith('/chapter-1.1')) {
      return {
        ok: true, status: 200, redirected: false, headers: { get: () => null },
        text: async () => '<span class="chapter-title">Chapter 1.1</span><div id="chapter-container"><p>Story 1.1</p></div>',
      };
    }
    if (url.endsWith('/chapter-1.2')) {
      if (attempts[url] === 1) {
        return {
          ok: true, status: 200, redirected: false, headers: { get: () => null },
          text: async () => { throw new Error('body stream aborted'); },
        };
      }
      return {
        ok: true, status: 200, redirected: false, headers: { get: () => null },
        text: async () => '<span class="chapter-title">Chapter 1.2</span><div id="chapter-container"><p>Story 1.2</p></div>',
      };
    }
    if (url.endsWith('/chapter-1.10')) {
      return {
        ok: true, status: 200, redirected: false, headers: { get: () => null },
        text: async () => '<span class="chapter-title">Chapter 1.10</span><div id="chapter-container"><p>Story 1.10</p></div>',
      };
    }
  }
  if (mode === 'stop_during_wait') {
    if (url.endsWith('/chapter-1')) {
      setTimeout(() => window.wnsStop(), 5);
      return {
        ok: true, status: 200, redirected: false, headers: { get: () => null },
        text: async () => '<span class="chapter-title">Chapter 1</span><div id="chapter-container"><p>Story 1</p></div>',
      };
    }
    return {
      ok: true, status: 200, redirected: false, headers: { get: () => null },
      text: async () => '<span class="chapter-title">Chapter 2</span><div id="chapter-container"><p>Story 2</p></div>',
    };
  }
  if (mode === 'stop_in_flight') {
    if (url.endsWith('/chapter-1')) {
      await new Promise((r) => setTimeout(r, 10));
      window.wnsStop();
      return {
        ok: true, status: 200, redirected: false, headers: { get: () => null },
        text: async () => '<span class="chapter-title">Chapter 1</span><div id="chapter-container"><p>Story 1</p></div>',
      };
    }
  }
  if (mode === 'exhaust_429') {
    return { ok: false, status: 429, redirected: false, headers: { get: () => '0' } };
  }
  if (mode === 'reject_network') {
    throw new Error('network down');
  }
  return { ok: false, status: 404, redirected: false, headers: { get: () => null } };
};

eval(script);

(async () => {
  // Test 1: 429 with HTTP-date Retry-After recovers, body-read error retries, AND 1.1/1.2/1.10 stay distinct & ordered
  await window.wnsCollect(1, 1);
  assert.deepStrictEqual(savedFiles, ['demo-1-1.10.html']);
  assert.strictEqual(attempts['https://example.test/novel/demo/chapter-1'], 2);
  assert.strictEqual(attempts['https://example.test/novel/demo/chapter-1.1'], 1);
  assert.strictEqual(attempts['https://example.test/novel/demo/chapter-1.2'], 2);
  assert.strictEqual(attempts['https://example.test/novel/demo/chapter-1.10'], 1);

  // Test 2: calling wnsStop() during delay prevents fetching chapter 2
  mode = 'stop_during_wait';
  savedFiles = [];
  fetchCalls = [];
  attempts = {};
  await window.wnsCollect(1, 5);
  assert.deepStrictEqual(fetchCalls, ['https://example.test/novel/demo/chapter-1']);
  assert.deepStrictEqual(savedFiles, ['demo-1-1.html']);

  // Test 3: calling wnsStop() while chapter 1 fetch is in flight still saves chapter 1
  mode = 'stop_in_flight';
  savedFiles = [];
  fetchCalls = [];
  attempts = {};
  await window.wnsCollect(1, 5);
  assert.deepStrictEqual(fetchCalls, ['https://example.test/novel/demo/chapter-1']);
  assert.deepStrictEqual(savedFiles, ['demo-1-1.html']);

  // Test 4: 429 on all 3 attempts stops cleanly after 3 tries
  mode = 'exhaust_429';
  savedFiles = [];
  fetchCalls = [];
  announcements = [];
  attempts = {};
  await window.wnsCollect(1, 2);
  assert.strictEqual(attempts['https://example.test/novel/demo/chapter-1'], 3);
  assert.deepStrictEqual(savedFiles, []);
  assert.ok(announcements.some((m) => m.includes('did not show chapter text')));

  // Test 5: fetch rejection on all 3 attempts reports error cleanly
  mode = 'reject_network';
  savedFiles = [];
  fetchCalls = [];
  announcements = [];
  attempts = {};
  await window.wnsCollect(1, 2);
  assert.strictEqual(attempts['https://example.test/novel/demo/chapter-1'], 3);
  assert.ok(announcements.some((m) => m.includes('network down')));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""
        with TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            script_file = tmp_dir / 'collector.js'
            harness_file = tmp_dir / 'harness.js'
            script_file.write_text(patched, encoding='utf-8')
            harness_file.write_text(harness, encoding='utf-8')
            completed = subprocess.run(
                ['node', str(harness_file), str(script_file)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(completed.returncode, 0, msg=f'stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}')


if __name__ == '__main__':
    unittest.main()

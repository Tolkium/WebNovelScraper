"""Exercise desktop jobs against a small local website, without third-party downloads."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from ebooklib import epub
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtQml import QJSEngine
from PySide6.QtWidgets import QApplication, QMessageBox

from desktop_service import JobSettings, browser_script, read_epub, run_job, split_novel_url
from WebNovelDesktop import DesktopWindow
from WebNovelScraper import RateLimiter, ScraperCancelled, SiteBlocked, parse_arguments


class FixtureWebsite(BaseHTTPRequestHandler):
    requested = []

    def do_GET(self):
        self.requested.append(self.path)
        if self.path == '/cover.jpg':
            stream = BytesIO()
            Image.new('RGB', (1000, 1800), '#23766a').save(stream, format='JPEG')
            content = stream.getvalue()
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
        elif self.path.startswith('/blocked/'):
            self.send_response(403)
            self.send_header('cf-mitigated', 'challenge')
            content = b'Just a moment...'
        elif self.path.endswith('/chapter-3'):
            self.send_response(500)
            content = b'Fixture chapter unavailable'
        else:
            number = self.path.rsplit('-', 1)[-1]
            content = (f'<html><span class="chapter-title">Original chapter {number}</span>'
                       '<div id="chapter-container">'
                       '<p>' + 'This is the chapter story, ready to be read aloud. ' * 8 + '<sup>1</sup>'
                       '<span aria-hidden="true"><button>17 comments</button></span>'
                       '<button class="spoiler">revealed line</button></p>'
                       '<p>1. Footnote: a useful explanation.</p>'
                       '<p>T/N: a translator note.</p>'
                       '<script>unwanted advertising</script></div></html>').encode()
            if self.path.startswith('/custom/'):
                content = content.replace(b'<span class="chapter-title">', b'<h2 class="custom-heading">').replace(b'</span>', b'</h2>')
                content = content.replace(b'<div id="chapter-container">', b'<main class="story">').replace(b'</div>', b'</main>')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureWebsite)
        cls.server_thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = JobSettings(base_url=self.base + '/novel/', novel_id='fixture',
                                    title='Fixture book', start=1, end=2,
                                    output_dir=str(self.root), retries=1, requests_per_second=20)
        self.logs, self.updates = [], []
        FixtureWebsite.requested.clear()

    def tearDown(self):
        self.temp.cleanup()

    def job(self, cancel=None, overwrite=False):
        return run_job(self.settings, cancel or Event(), self.logs.append,
                       self.updates.append, self.root / 'job.log', overwrite)

    def window(self):
        preferences = QSettings(str(self.root / 'preferences.ini'), QSettings.Format.IniFormat)
        window = DesktopWindow(preferences, data_dir=self.root)
        window.access_check = lambda url, user_agent: False
        window._apply_settings(self.settings)
        return window

    def wait_until(self, predicate, timeout=10):
        until = time.monotonic() + timeout
        while not predicate() and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(predicate(), 'The GUI operation did not finish before timeout.')

    def test_epub_creation_cover_metadata_and_all_content_options(self):
        self.settings.include_footnotes = True
        self.settings.include_notes = True
        self.settings.simple_chapters = True
        self.settings.author = 'Example author'
        self.settings.translator = 'Example translator'
        self.settings.creator = 'Client preparation'
        self.settings.language = 'sk'
        self.settings.cover_url = self.base + '/cover.jpg'
        self.settings.max_cover_height = 600
        result = self.job()
        self.assertEqual(result.success, 2)
        self.assertEqual(result.failed, [])
        self.assertTrue(result.path.exists())
        book = epub.read_epub(str(result.path))
        self.assertEqual(book.get_metadata('DC', 'language')[0][0], 'sk')
        self.assertEqual(book.get_metadata('DC', 'creator')[0][0], 'Example author')
        self.assertEqual(book.get_metadata('DC', 'contributor')[0][0], 'Example translator')
        self.assertEqual(book.get_metadata('DC', 'publisher')[0][0], 'Client preparation')
        with ZipFile(result.path) as archive:
            content = archive.read('EPUB/chap_001.xhtml').decode()
            self.assertIn('Chapter 1', content)
            self.assertIn('footnote-1', content)
            self.assertIn('ref-1', content)
            self.assertIn('translator note', content)
            self.assertIn('style/nav.css', content)
            self.assertNotIn('unwanted advertising', content)
            self.assertNotIn('17 comments', content)
            self.assertIn('revealed line', content)
            cover = Image.open(BytesIO(archive.read('EPUB/cover.jpg')))
            self.assertEqual(cover.height, 600)
            self.assertEqual(cover.width, 400)
        title, chapters = read_epub(result.path)
        self.assertEqual(title, 'Fixture book')
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0][0], 'Chapter 1')
        self.assertEqual(self.updates[-1]['current'], 2)

    def test_content_options_exclude_notes_and_footnotes_by_default(self):
        result = self.job()
        with ZipFile(result.path) as archive:
            content = archive.read('EPUB/chap_001.xhtml').decode()
            self.assertIn('Original chapter 1', content)
            self.assertNotIn('translator note', content)
            self.assertNotIn('footnote-1', content)
            self.assertNotIn('<sup>', content)

    def test_failed_chapter_is_reported_and_successes_are_saved(self):
        self.settings.end = 3
        result = self.job()
        self.assertEqual(result.failed, [3])
        self.assertEqual(result.success, 2)
        self.assertTrue(result.path.exists())
        self.assertEqual(self.updates[-1]['stage'], 'failed')

    def test_no_successes_produces_no_epub(self):
        self.settings.start = self.settings.end = 3
        with self.assertRaisesRegex(ValueError, 'No chapters'):
            self.job()
        self.assertFalse(self.settings.output_path.exists())

    def test_custom_selectors_and_minimum_content_length(self):
        self.settings.base_url = self.base + '/custom/'
        self.settings.title_selector = 'h2.custom-heading'
        self.settings.content_selector = 'main.story'
        self.assertEqual(self.job().success, 2)
        self.settings.filename = 'too-short.epub'
        self.settings.min_content_length = 10000
        with self.assertRaisesRegex(ValueError, 'No chapters'):
            self.job()
        self.assertFalse(self.settings.output_path.exists())

    def test_output_folder_without_hard_link_support(self):
        with patch('desktop_service.os.link', side_effect=OSError('Hard links unavailable')):
            result = self.job()
        self.assertIsNone(ZipFile(result.path).testzip())
        self.assertEqual(list(self.root.glob('.webnovel-*')), [])

    def test_existing_file_requires_explicit_overwrite(self):
        self.settings.output_path.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            self.job()
        self.assertEqual(self.settings.output_path.read_bytes(), b'original')
        result = self.job(overwrite=True)
        self.assertTrue(ZipFile(result.path).testzip() is None)

    def test_cancellation_keeps_existing_file_and_removes_temporary_files(self):
        self.settings.output_path.write_bytes(b'original')
        cancel = Event()

        def progress(data):
            if data['stage'] == 'downloaded':
                cancel.set()

        result = run_job(self.settings, cancel, self.logs.append, progress, self.root / 'job.log', overwrite=True)
        self.assertTrue(result.cancelled)
        self.assertIsNone(result.path)
        self.assertEqual(self.settings.output_path.read_bytes(), b'original')
        self.assertEqual(list(self.root.glob('.webnovel-*')), [])

    def test_cancel_interrupts_retry_and_rate_limit_waits(self):
        cancel = Event()
        cancel.set()
        limiter = RateLimiter(1, cancel)
        started = time.monotonic()
        with self.assertRaises(ScraperCancelled):
            limiter.sleep(300)
        self.assertLess(time.monotonic() - started, .2)

    def test_validation_url_parsing_and_settings_round_trip(self):
        base, novel, prefix = split_novel_url('https://example.com/novel/my-book/chapter-42?test=yes')
        self.assertEqual(base, 'https://example.com/novel/')
        self.assertEqual(novel, 'my-book')
        self.assertEqual(prefix, 'chapter-')
        self.assertEqual(split_novel_url('https://example.com/novel/my-book')[2], 'chapter-')
        self.assertEqual(JobSettings.from_dict(json.loads(json.dumps(self.settings.to_dict()))), self.settings)
        self.settings.start = 5
        self.settings.filename = '../outside.epub'
        self.settings.base_url = 'file:///C:/secret'
        self.settings.title_selector = '['
        self.assertTrue({'start', 'filename', 'base_url', 'title_selector'} <= self.settings.validate().keys())
        with self.assertRaises(ValueError):
            JobSettings.from_dict({'include_notes': 'false'})

    def test_chapter_prefix_from_pasted_url_is_used_for_every_chapter(self):
        base, novel, prefix = split_novel_url(self.base + '/novel/fixture/og-chapter-2')
        self.assertEqual((base, novel, prefix), (self.base + '/novel/', 'fixture', 'og-chapter-'))
        self.settings.chapter_prefix = prefix
        self.settings.simple_chapters = True
        result = self.job()
        self.assertEqual(result.success, 2)
        self.assertEqual(FixtureWebsite.requested, ['/novel/fixture/og-chapter-1', '/novel/fixture/og-chapter-2'])
        self.assertEqual([title for title, _ in read_epub(result.path)[1]], ['Chapter 1', 'Chapter 2'])
        self.assertEqual(split_novel_url(self.base + '/novel/fixture/Chapter-7')[1:], ('fixture', 'Chapter-'))
        self.settings.chapter_prefix = ''
        self.assertNotIn('chapter_prefix', self.settings.validate())
        self.settings.chapter_prefix = 'bad/prefix-'
        self.assertIn('chapter_prefix', self.settings.validate())

    def test_browser_check_stops_the_job_at_once(self):
        self.settings.base_url = self.base + '/blocked/'
        self.settings.retries = 3
        with self.assertRaises(SiteBlocked):
            self.job()
        self.assertEqual(FixtureWebsite.requested, ['/blocked/fixture/chapter-1'])

    def test_browser_script_is_made_for_the_current_book(self):
        self.settings.start, self.settings.end = 105, 125
        self.settings.chapter_prefix = 'og-chapter-'
        self.settings.content_selector = 'div.story'
        script = browser_script(self.settings)
        settings = json.loads(re.search(r'const SETTINGS = (\{.*?\});', script).group(1))
        self.assertEqual(settings, {'chapterUrl': self.base + '/novel/fixture/og-chapter-{n}', 'name': 'fixture',
                                    'start': 105, 'end': 125, 'titleSelector': 'span.chapter-title',
                                    'contentSelector': 'div.story'})
        self.assertNotIn('BATCH_SIZE', script)
        # This Qt JavaScript engine has no async/await; dropping both still checks the rest of the syntax.
        engine = QJSEngine()
        parsed = engine.evaluate('new Function(' + json.dumps(re.sub(r'\b(async|await)\b', '', script)) + ')')
        self.assertFalse(parsed.isError(), parsed.toString())

    def test_blocked_url_opens_browser_section_and_copies_script_for_the_book(self):
        window = self.window()
        window.access_check = lambda url, user_agent: url.endswith('/chapter-105')
        try:
            self.assertFalse(window.browser_toggle.isChecked())
            self.assertTrue(window.browser_panel.isHidden())
            with patch.object(QMessageBox, 'information') as information:
                window.novel_url.setText(self.base + '/novel/other-book/chapter-105')
                window._use_url()
                self.wait_until(lambda: information.called)
            self.assertTrue(window.browser_toggle.isChecked())
            self.assertFalse(window.browser_panel.isHidden())
            self.assertIn('browser check', information.call_args.args[2])
            window._copy_browser_script()
            self.assertIn(f'"chapterUrl": "{self.base}/novel/other-book/chapter-{{n}}"', QApplication.clipboard().text())
            self.assertIn('copied', window.browser_status.text())
        finally:
            window.close()

    def test_copy_script_asks_for_the_source_first(self):
        self.settings.base_url = ''
        window = self.window()
        try:
            with patch.object(QMessageBox, 'warning') as warning, patch('WebNovelDesktop.browser_script') as script:
                window._copy_browser_script()
            warning.assert_called_once()
            self.assertIn('base URL', warning.call_args.args[2])
            script.assert_not_called()
        finally:
            window.close()

    def test_gui_job_on_blocked_site_opens_browser_section(self):
        self.settings.base_url = self.base + '/blocked/'
        window = self.window()
        try:
            with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Ok), \
                    patch.object(QMessageBox, 'information') as information, patch.object(QMessageBox, 'warning') as warning:
                window._start_job()
                self.wait_until(lambda: window.thread is None)
            information.assert_called_once()
            warning.assert_not_called()
            self.assertTrue(window.browser_toggle.isChecked())
            self.assertEqual(window.stack.currentIndex(), 0)
            self.assertTrue(window.create_button.isEnabled())
        finally:
            window.close()

    def test_saved_file_setting_opens_browser_section(self):
        self.settings.saved_file = str(self.root / 'novel-1-2.html')
        window = self.window()
        try:
            self.assertTrue(window.browser_toggle.isChecked())
            self.assertEqual(window._collect_settings().saved_file, self.settings.saved_file)
        finally:
            window.close()

    def test_cli_chapter_prefix_argument(self):
        with patch('sys.argv', ['WebNovelScraper.py']):
            self.assertEqual(parse_arguments().chapter_prefix, 'chapter-')
        with patch('sys.argv', ['WebNovelScraper.py', '--chapter-prefix', 'og-chapter-']):
            self.assertEqual(parse_arguments().chapter_prefix, 'og-chapter-')

    def test_simple_chapter_names_use_the_number_at_the_end_of_the_url(self):
        self.settings.novel_id = 'the-chapter-9-saga'
        self.settings.chapter_prefix = 'ch-'
        self.settings.simple_chapters = True
        result = self.job()
        self.assertEqual([title for title, _ in read_epub(result.path)[1]], ['Chapter 1', 'Chapter 2'])
        self.assertIn('Chapter 1 — 82 words', self.logs)

    def test_log_shows_original_titles_and_word_counts(self):
        self.job()
        self.assertIn('Chapter 1: Original chapter 1 — 82 words', self.logs)

    def test_known_site_url_fills_selectors_and_other_sites_restore_defaults(self):
        window = self.window()
        try:
            window.novel_url.setText('https://www.wuxiaworld.com/novel/overgeared/og-chapter-2')
            window._use_url()
            settings = window._collect_settings()
            self.assertEqual((settings.base_url, settings.novel_id, settings.chapter_prefix,
                              settings.title_selector, settings.content_selector),
                             ('https://www.wuxiaworld.com/novel/', 'overgeared', 'og-chapter-', 'h4', 'div.chapter-body'))
            window.novel_url.setText('https://other.example/novel/my-book/chapter-1')
            window._use_url()
            settings = window._collect_settings()
            self.assertEqual((settings.chapter_prefix, settings.title_selector, settings.content_selector),
                             ('chapter-', JobSettings.title_selector, JobSettings.content_selector))
        finally:
            window.close()

    def test_gui_navigation_all_settings_preview_and_persistence(self):
        result = self.job()
        window = self.window()
        try:
            self.assertEqual(set(window.controls), set(JobSettings.__dataclass_fields__))
            for index in range(6):
                window.nav.setCurrentRow(index)
                self.assertEqual(window.stack.currentIndex(), index)
            window._load_epub(result.path)
            self.assertEqual(window.chapter_list.count(), 2)
            self.assertIn('chapter story', window.reader_text.toPlainText())
            self.assertEqual(window.library.count(), 1)
            window.chapter_list.setCurrentRow(1)
            self.assertIn('Original chapter 2', window.reader_text.toPlainText())
            window._save_preferences()
            restored = DesktopWindow(window.preferences, data_dir=self.root)
            try:
                expected = window._collect_settings()
                self.assertEqual(restored._collect_settings(), expected)
                self.assertAlmostEqual(expected.cover_aspect_ratio, self.settings.cover_aspect_ratio, places=4)
                self.assertEqual(restored.library.count(), 1)
            finally:
                restored.close()
        finally:
            window.close()

    def test_gui_worker_creates_epub_and_returns_to_ready(self):
        window = self.window()
        try:
            with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Ok), patch.object(QMessageBox, 'warning') as warning:
                window._start_job()
                self.assertEqual(window.stack.currentIndex(), 4)
                self.assertFalse(window.create_button.isEnabled())
                self.wait_until(lambda: window.thread is None)
                warning.assert_not_called()
            self.assertTrue(window.latest_path.exists())
            self.assertEqual(window.chapter_table.rowCount(), 2)
            self.assertTrue(window.create_button.isEnabled())
            self.assertTrue(window.preview_latest_button.isEnabled())
            self.assertEqual(window.metrics['downloaded'].text(), '2')
        finally:
            if window.thread is not None:
                window.cancel.set()
                self.wait_until(lambda: window.thread is None)
            window.close()

    def test_gui_cancellation_and_worker_errors_restore_controls(self):
        window = self.window()
        try:
            with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Ok), patch.object(QMessageBox, 'warning') as warning:
                window._start_job()
                window._cancel_job()
                self.wait_until(lambda: window.thread is None)
                self.assertIsNone(window.latest_path)
                self.assertFalse(self.settings.output_path.exists())
                self.assertIn('cancelled', window.activity_status.text())
                warning.assert_not_called()
                self.settings.start = self.settings.end = 3
                window._apply_settings(self.settings)
                window._start_job()
                self.wait_until(lambda: window.thread is None)
                warning.assert_called_once()
                self.assertTrue(window.create_button.isEnabled())
                self.assertFalse(window.cancel_button.isEnabled())
                self.assertIn('No chapters', window.activity_status.text())
        finally:
            if window.thread is not None:
                window.cancel.set()
                self.wait_until(lambda: window.thread is None)
            window.close()


if __name__ == '__main__':
    unittest.main()

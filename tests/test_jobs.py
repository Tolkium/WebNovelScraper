"""Non-Qt tests for job execution, cover processing, validation, and URL parsing against a local fixture site."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from ebooklib import epub
from PIL import Image

from webnovel import (
    JobSettings,
    RateLimiter,
    ScraperCancelled,
    SiteBlocked,
    read_epub,
    run_job,
    split_novel_url,
)
from WebNovelScraper import parse_arguments


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
            content = (
                f'<html><span class="chapter-title">Original chapter {number}</span>'
                '<div id="chapter-container">'
                '<p>' + 'This is the chapter story, ready to be read aloud. ' * 8 + '<sup>1</sup>'
                '<span aria-hidden="true"><button>17 comments</button></span>'
                '<button class="spoiler">revealed line</button></p>'
                '<p>1. Footnote: a useful explanation.</p>'
                '<p>T/N: a translator note.</p>'
                '<script>unwanted advertising</script></div></html>'
            ).encode()
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


class JobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        self.settings = JobSettings(
            base_url=self.base + '/novel/',
            novel_id='fixture',
            title='Fixture book',
            start=1,
            end=2,
            output_dir=str(self.root),
            retries=1,
            requests_per_second=20,
        )
        self.logs, self.updates = [], []
        FixtureWebsite.requested.clear()

    def tearDown(self):
        self.temp.cleanup()

    def job(self, cancel=None, overwrite=False):
        return run_job(
            self.settings,
            cancel or Event(),
            self.logs.append,
            self.updates.append,
            self.root / 'job.log',
            overwrite,
        )

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
        with patch('webnovel.jobs.os.link', side_effect=OSError('Hard links unavailable')):
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
        self.assertLess(time.monotonic() - started, 0.2)

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


if __name__ == '__main__':
    unittest.main()

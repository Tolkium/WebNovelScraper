"""Build EPUBs from chapter files saved by browser/collect-chapters.js."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from bs4 import BeautifulSoup

from webnovel import JobSettings, WebNovelScraper, read_epub, read_saved_chapters, repair_split_words, run_job
from WebNovelScraper import main


def saved_file(*chapters):
    sections = ''.join(
        f'<section class="wns-chapter" data-number="{number}">'
        f'<span class="chapter-title">Chapter {number}</span>'
        f'<div class="chapter-container"><p>Chapter {number}</p>{body}</div></section>'
        for number, body in chapters)
    return f'<!doctype html><html><head><meta charset="utf-8"></head><body>{sections}</body></html>'


STORY = '<p>' + 'The s.h.i.+eld of the a.s.sa.s.sin was a G.o.ddess gift. ' * 4 + '</p>'
END_NOTES = ('<p>Glossary of Common Korean Terms.</p><p>OG: Glossary Link.</p>'
             '<p>Current schedule: 20 chapters a week.</p>'
             '<p>Check out my Patreon for early access to a certain number of unedited chapters.</p>')


class SplitWordTests(unittest.TestCase):
    def repaired(self, text):
        soup = BeautifulSoup(f'<p>{text}</p>', 'html.parser')
        repair_split_words(soup)
        return soup.p.get_text()

    def test_rejoins_dotted_words(self):
        for broken, fixed in [
            ('s.h.i.+eld', 'shield'), ('a.s.sa.s.sin', 'assassin'), ('G.o.ddess', 'Goddess'),
            ('b.u.t.ton', 'button'), ('Craftsmans.h.i.+p', 'Craftsmanship'), ('t-s.h.i.+rt', 't-shirt'),
            ('wis.h.!.+', 'wish!'), ('Flas.h.!.+”', 'Flash!”'), ('Kw.a.n.g!Kwaang', 'Kwang!Kwaang'),
            ('“h.e.l.lo?”', '“hello?”'),
        ]:
            self.assertEqual(self.repaired(broken), fixed)

    def test_keeps_sentences_numbers_and_acronyms(self):
        for text in ['first.I', 'late.Come', '1.2 meters', 'L.T.S', 'Des...cend...ant...']:
            self.assertEqual(self.repaired(text), text)

    def test_keeps_abbreviations(self):
        for text in ['at 5 a.m. today', 'at 6 p.m.', 'e.g. this', 'i.e. max level', 'a.k.a. the masked man', 'I.went home']:
            self.assertEqual(self.repaired(text), text)

    def test_abbreviation_running_into_the_next_word_gets_a_space(self):
        for broken, fixed in [('at 5 a.m.The guards', 'at 5 a.m. The guards'), ('e.g.the', 'e.g. the')]:
            self.assertEqual(self.repaired(broken), fixed)

    def test_split_words_starting_like_abbreviations_or_i_are_joined(self):
        for broken, fixed in [('a.m.a.z.i.n.g', 'amazing'), ('e.g.g.s', 'eggs'), ('I.t was late', 'It was late'),
                              ('I.n the end', 'In the end')]:
            self.assertEqual(self.repaired(broken), fixed)

    def test_split_word_followed_by_a_new_sentence(self):
        for broken, fixed in [('G.o.d.You', 'God. You'), ('h.e.l.l.It', 'hell. It'), ('cla.s.s.I accepted', 'class. I accepted')]:
            self.assertEqual(self.repaired(broken), fixed)


class SpeechCleanupTests(unittest.TestCase):
    """Chapter text is cleaned so text-to-speech reads the story and nothing else."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scraper = self.make_scraper()

    def make_scraper(self, **settings):
        scraper = WebNovelScraper(settings=JobSettings(**settings), log_path=Path(self.temp.name) / f'{len(settings)}.log')
        self.addCleanup(scraper.close)
        return scraper

    def paragraphs(self, body, scraper=None):
        section = BeautifulSoup(saved_file((1, STORY + body)), 'html.parser').select_one('section')
        content = (scraper or self.scraper).parse_saved_chapter(section, 1)[1]
        return [p.get_text() for p in BeautifulSoup(content, 'html.parser').find_all('p')][1:]

    def test_always_in_story_text_is_kept(self):
        story = ['<p>He was always one of the top ranks.</p>', '<p>As always, he didn’t think.</p>']
        self.assertEqual(self.paragraphs(''.join(story)), ['He was always one of the top ranks.', 'As always, he didn’t think.'])
        self.assertEqual(self.paragraphs('<p>As always, thanks for reading!</p>'), [])

    def test_text_outside_paragraphs_is_kept_but_trailing_scraps_are_not(self):
        self.assertEqual(self.paragraphs('<p>Grid’s guess was this:</p> The lord had joined hands. <p>Next.</p>'
                                         'gold has been acquired.gold has been acquired.'),
                         ['Grid’s guess was this:', 'The lord had joined hands.', 'Next.'])

    def test_translator_note_in_brackets_leaves_the_dialogue(self):
        self.assertEqual(self.paragraphs('<p>“Temple? No, I am overgeared.” (TL: this pun doesn’t translate.)</p>'
                                         '<p>TL NOTE: People, read the notes!</p>'),
                         ['“Temple? No, I am overgeared.”'])

    def test_run_on_sentences_get_a_space(self):
        self.assertEqual(self.paragraphs('<p>the neighborhood.Thanks to that, these days?Our G.o.d.You</p>'
                                         '<p>Puk!Puk!Puk! Step.Step.Step. L.T.S game. “...Do it.” said...Then I want...What?You</p>'),
                         ['the neighborhood. Thanks to that, these days? Our God. You',
                          'Puk! Puk! Puk! Step. Step. Step. L.T.S game. “...Do it.” said... Then I want... What? You'])

    def test_site_watermarks_are_removed(self):
        self.assertEqual(self.paragraphs('<p>“Are you going to move the capital?” Example.com</p>'
                                         '<p>Attack Power: 25,020 Example.com</p>'
                                         '<p>Search “Example.com” for the original.</p>'
                                         '<p>We are "Example.com", find us on google.</p>'
                                         '<p>????? Example.com ??? ? ??????.</p>'
                                         '<p>He bought it on amazon.com yesterday.</p>'),
                         ['“Are you going to move the capital?”', 'Attack Power: 25,020', 'He bought it on amazon.com yesterday.'])

    def test_protected_email_links_and_footnote_marks_are_removed(self):
        self.assertEqual(self.paragraphs('<p>“Fuc*[email protected]…!!”</p>'
                                         '<p>He was a Koryoin (https://en.wikipedia.org/wiki/Koryo-saram) who grew up poor.</p>'
                                         '<p>Inflicts collapse. [1] Deals 600% damage.</p>'),
                         ['“Fuc*…!!”', 'He was a Koryoin who grew up poor.', 'Inflicts collapse. Deals 600% damage.'])

    def test_footnote_marks_stay_when_footnotes_are_included(self):
        self.assertEqual(self.paragraphs('<p>Inflicts collapse. [1] Deals damage.</p>', self.make_scraper(include_footnotes=True)),
                         ['Inflicts collapse. [1] Deals damage.'])

    def test_symbols_separators_and_tildes_are_removed(self):
        self.assertEqual(self.paragraphs('<p>★ Hidden Quest ★</p><p>***</p><p>■ Level 5 ▼</p><p>“......”</p>'
                                         '<p>“Jack~~pot!!” 1,500~2,000 Oppa~</p>'),
                         ['Hidden Quest', 'Level 5', '“......”', '“Jackpot!!” 1,500~2,000 Oppa'])

    def test_story_that_names_a_site_is_kept(self):
        story = ['“I already ordered it from Amazon.com.”', 'He typed the address and opened Google.com',
                 '“Look it up on Naver.com!”', 'Read the news at Yahoo.com']
        self.assertEqual(self.paragraphs(''.join(f'<p>{line}</p>' for line in story)), story)

    def test_stat_values_ranges_and_addresses_are_kept(self):
        self.assertEqual(self.paragraphs('<p>Strength [15] Agility [12]</p><p>Open Monday~Friday.</p>'),
                         ['Strength [15] Agility [12]', 'Open Monday~Friday.'])
        self.assertEqual(self.paragraphs('<p>Visit www.Example.com today.</p>', self.make_scraper(include_footnotes=True)),
                         ['Visit www.Example.com today.'])

    def test_links_go_without_taking_punctuation(self):
        self.assertEqual(self.paragraphs('<p>See https://x.com/a, then (https://en.wikipedia.org/wiki/Foo_(bar)) go.</p>'),
                         ['See, then go.'])

    def test_scene_break_becomes_a_line(self):
        section = BeautifulSoup(saved_file((1, STORY + '<p>Before.</p><p>* * *</p><p>After.</p>')), 'html.parser').select_one('section')
        self.assertIn('<p>Before.</p>\n<hr/>\n<p>After.</p>', self.scraper.parse_saved_chapter(section, 1)[1])

    def test_note_with_brackets_inside_is_removed_whole(self):
        self.assertEqual(self.paragraphs('<p>“Oppa!” (TL: Oppa (older brother) is a term of endearment.)</p>'),
                         ['“Oppa!”'])

    def test_included_notes_and_footnotes_keep_their_text(self):
        scraper = self.make_scraper(include_notes=True, include_footnotes=True)
        self.assertEqual(self.paragraphs('<p>He was a Koryoin (https://en.wikipedia.org/wiki/Koryo-saram).</p>'
                                         '<p>“Oppa!” (TL: older brother)</p>', scraper),
                         ['He was a Koryoin (https://en.wikipedia.org/wiki/Koryo-saram).'])

    def test_comments_are_never_read(self):
        self.assertEqual(self.paragraphs('<p><!-- x --> He left.<!-- ad slot --></p>'), ['He left.'])

    def test_loose_text_around_line_breaks_and_blocks_is_kept(self):
        self.assertEqual(self.paragraphs('<p>A</p>Line one<br/>Line two<hr/>Line three<div>Box</div><p>B</p>'
                                         '<a href="/next">Next Chapter</a><p>C</p>He said <span>hello</span> to me.<p>D</p>'),
                         ['A', 'Line oneLine two', 'Line three', 'B', 'C', 'He said hello to me.', 'D'])

    def test_leading_space_before_formatting_is_removed(self):
        section = BeautifulSoup(saved_file((1, STORY + '<p> <em>Hello</em> there.</p>')), 'html.parser').select_one('section')
        self.assertIn('<p><em>Hello</em> there.</p>', self.scraper.parse_saved_chapter(section, 1)[1])

    def test_downloaded_chapters_are_cleaned_the_same_way(self):
        page = BeautifulSoup('<span class="t">Chapter 3</span><div id="c">' + STORY +
                             '<p>The s.h.i.+eld.It was G.o.d’s gift.</p></div>', 'html.parser')
        content = self.scraper.parse_chapter(page.select_one('.t'), page.select_one('#c'), 'https://site/novel/x/chapter-3')[1]
        self.assertIn('The shield. It was God’s gift.', content)


class SavedChapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_all_files_in_chapter_order_without_duplicates(self):
        (self.folder / 'b.html').write_text(saved_file((3, STORY), (2, STORY)), encoding='utf-8')
        (self.folder / 'a.html').write_text(saved_file((1, STORY), (2, STORY)), encoding='utf-8')
        (self.folder / 'notes.txt').write_text('ignored', encoding='utf-8')
        self.assertEqual([number for number, _ in read_saved_chapters(self.folder)], [1, 2, 3])

    def test_in_between_chapters_keep_their_number(self):
        (self.folder / 'novel.html').write_text(saved_file((770, STORY), (769.1, STORY), (769, STORY)), encoding='utf-8')
        chapters = read_saved_chapters(self.folder)
        self.assertEqual([number for number, _ in chapters], [769, 769.1, 770])
        with WebNovelScraper(settings=JobSettings(simple_chapters=True)) as scraper:
            number, section = chapters[1]
            self.assertEqual(scraper.parse_saved_chapter(section, number)[0], 'Chapter 769.1')

    def test_saved_chapter_is_repaired_and_end_notes_removed(self):
        (self.folder / 'novel.html').write_text(saved_file((105, STORY + END_NOTES)), encoding='utf-8')
        [(number, section)] = read_saved_chapters(self.folder)
        with WebNovelScraper() as scraper:
            title, content, notes, _ = scraper.parse_saved_chapter(section, number)
        self.assertEqual(title, 'Chapter 105')
        self.assertIn('The shield of the assassin was a Goddess gift.', content)
        for unwanted in ('Glossary', 'Current schedule', 'Patreon', '<p>Chapter 105</p>'):
            self.assertNotIn(unwanted, content)

    def test_paragraphs_do_not_start_with_spaces(self):
        story = '<p> \n “A skill that permanently improves the <em>performance</em>.”' + ' More story.' * 20 + '</p>'
        (self.folder / 'novel.html').write_text(saved_file((98, story)), encoding='utf-8')
        [(number, section)] = read_saved_chapters(self.folder)
        with WebNovelScraper() as scraper:
            content = scraper.parse_saved_chapter(section, number)[1]
        self.assertIn('<p>“A skill that permanently improves the <em>performance</em>.”', content)

    def test_empty_saved_chapter_fails_without_stopping(self):
        (self.folder / 'novel.html').write_text(saved_file((7, '')), encoding='utf-8')
        [(number, section)] = read_saved_chapters(self.folder)
        with WebNovelScraper() as scraper:
            self.assertEqual(scraper.parse_saved_chapter(section, number), (None, None, None, None))

    def saved_job(self, **changes):
        settings = JobSettings(title='Saved Book', start=2, end=3, output_dir=str(self.folder),
                               saved_file=str(self.folder / 'novel.html'), simple_chapters=True, **changes)
        return run_job(settings, Event(), lambda text: None, lambda data: None, self.folder / 'job.log')

    def test_desktop_job_builds_from_saved_file_without_a_website(self):
        (self.folder / 'novel.html').write_text(saved_file((1, STORY), (2, STORY), (3, STORY)), encoding='utf-8')
        self.assertEqual(JobSettings(title='Saved Book', output_dir=str(self.folder),
                                     saved_file=str(self.folder / 'novel.html')).validate(), {})
        result = self.saved_job()
        self.assertEqual((result.success, result.failed), (2, []))
        self.assertEqual([title for title, _ in read_epub(result.path)[1]], ['Chapter 2', 'Chapter 3'])

    def test_headings_can_be_left_out_of_the_chapter_text(self):
        (self.folder / 'novel.html').write_text(saved_file((1, STORY), (2, STORY), (3, STORY)), encoding='utf-8')
        result = self.saved_job(chapter_headings=False)
        with ZipFile(result.path) as archive:
            chapter = archive.read('EPUB/chap_001.xhtml').decode()
            self.assertNotIn('<h1', chapter)
            self.assertIn('Chapter 2', archive.read('EPUB/nav.xhtml').decode())
        title, text = read_epub(result.path)[1][0]
        self.assertEqual(title, 'Chapter 2')
        self.assertTrue(text.startswith('The shield'))

    def test_saved_file_must_exist_and_hold_the_chosen_chapters(self):
        settings = JobSettings(title='Saved Book', output_dir=str(self.folder), saved_file=str(self.folder / 'missing.html'))
        self.assertIn('saved_file', settings.validate())
        (self.folder / 'novel.html').write_text(saved_file((1, STORY)), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'No saved chapters 2 to 3'):
            self.saved_job()

    def run_cli(self, *options):
        (self.folder / 'novel.html').write_text(saved_file((1, STORY), (2, STORY), (3, STORY)), encoding='utf-8')
        argv = ['WebNovelScraper.py', '--from-folder', str(self.folder), '--title', 'Saved Book',
                '--start', '2', '--end', '3', *options]
        previous = os.getcwd()
        os.chdir(self.folder)
        try:
            with patch('sys.argv', argv), patch('builtins.input', return_value=''):
                main()
        finally:
            os.chdir(previous)
        return ZipFile(self.folder / 'saved_book.epub')

    def test_cli_no_headings_leaves_titles_out_of_the_text(self):
        with self.run_cli('--simple-chapters', '--no-headings') as archive:
            self.assertNotIn('<h1', archive.read('EPUB/chap_001.xhtml').decode())
            self.assertIn('Chapter 2', archive.read('EPUB/nav.xhtml').decode())

    def test_cli_builds_epub_from_folder_and_chapter_range(self):
        with self.run_cli('--simple-chapters') as archive:
            chapters = sorted(name for name in archive.namelist() if name.startswith('EPUB/chap_'))
            self.assertEqual(len(chapters), 2)
            self.assertIn('Chapter 2', archive.read(chapters[0]).decode())
            self.assertIn('shield', archive.read(chapters[1]).decode())
            self.assertIn('text-indent: 0', archive.read('EPUB/style/nav.css').decode())


if __name__ == '__main__':
    unittest.main()

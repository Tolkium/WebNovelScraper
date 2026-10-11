"""Native desktop workspace for preparing, previewing and reading EPUBs."""

import json
import argparse
from pathlib import Path
import sys
from threading import Event
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QSettings, QSize, Qt, QThread, QUrl, Signal, Slot, QStandardPaths
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QProxyStyle, QPushButton, QScrollArea, QStyle, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QVBoxLayout, QWidget,
)
from PySide6.QtTextToSpeech import QTextToSpeech

from desktop_service import JobSettings, read_epub, run_job, split_novel_url


STYLE = """
QMainWindow, QWidget { background: #f4f6f8; color: #233343; }
QWidget { font-family: 'Segoe UI', 'Arial'; font-size: 14px; }
QFrame#sidebar { background: #1f474d; }
QFrame#sidebar QWidget { background: transparent; }
QFrame#sidebar QLabel { background: transparent; color: #f0f7f8; }
QLabel#brand { font-size: 24px; font-weight: 700; }
QLabel#brandTag { color: #a9c9cd; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel#sidebarNote { color: #b9d3d7; font-size: 12px; }
QListWidget#navigation { background: transparent; border: none; color: #d3e5e8; outline: 0; }
QListWidget#navigation::item { padding: 12px 16px 12px 12px; margin: 0; border: none; border-left: 4px solid transparent; }
QListWidget#navigation::item:hover { background: #29565c; color: white; }
QListWidget#navigation::item:selected { background: #34737a; color: white; border-left: 4px solid #8fd6dd; font-weight: 700; }
QLabel#pageTitle { font-size: 27px; font-weight: 700; }
QLabel#subtitle { color: #5d6c7b; }
QLabel#hint { color: #6a7886; font-size: 12px; }
QLabel#emptyState { color: #7a8794; font-size: 13px; font-style: italic; }
QLabel#section { font-size: 15px; font-weight: 600; color: #233343; }
QLabel#pill { background: #e4eff0; color: #30626a; border: 1px solid #c8dfe2; border-radius: 9px; padding: 2px 10px; font-size: 12px; font-weight: 600; }
QGroupBox { background: white; border: 1px solid #dce3e9; border-radius: 10px; margin-top: 28px; padding: 16px; font-size: 15px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 0; padding: 0 0 6px 0; }
QGroupBox QWidget { background: transparent; font-size: 14px; font-weight: 400; }
QGroupBox QLabel#hint { font-size: 12px; }
QFrame#metricCard { background: white; border: 1px solid #dce3e9; border-radius: 10px; }
QFrame#metricCard QLabel { background: transparent; }
QLabel#metricLabel { color: #5d6c7b; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel#metric { font-size: 26px; font-weight: 700; color: #233343; }
QLabel#metric[tone="good"] { color: #2d7d4f; }
QLabel#metric[tone="bad"] { color: #b13636; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1px solid #bac7d1; border-radius: 6px; padding: 6px 8px; min-height: 20px; selection-background-color: #deedef; selection-color: #244c52; }
QSpinBox, QDoubleSpinBox { padding: 5px 30px 5px 8px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 25px; background: #f0f5f6; border: none; border-left: 1px solid #dce3e9; border-top-right-radius: 5px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 25px; background: #f0f5f6; border: none; border-left: 1px solid #dce3e9; border-bottom-right-radius: 5px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #deedef; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("__UI_ASSETS__/chevron-up.svg"); width: 12px; height: 12px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("__UI_ASSETS__/chevron-down.svg"); width: 12px; height: 12px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 2px solid #317c85; }
QLineEdit[invalid="true"], QSpinBox[invalid="true"], QDoubleSpinBox[invalid="true"] { border: 2px solid #b13636; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { background: #edf1f4; color: #71808c; }
QPushButton { background: white; border: 1px solid #bac7d1; border-radius: 6px; padding: 7px 16px; min-height: 20px; font-weight: 600; }
QPushButton:hover { background: #e7f0f1; border-color: #317c85; }
QPushButton:pressed { background: #cce1e4; }
QPushButton:focus { border: 2px solid #317c85; }
QPushButton#primary { background: #317c85; color: white; border-color: #317c85; }
QPushButton#primary:hover { background: #286b73; }
QPushButton#ghost { background: transparent; border: 1px solid transparent; color: #8a2e2e; }
QPushButton#ghost:hover { background: #f7e9e9; border-color: #e2bcbc; }
QPushButton:disabled { background: transparent; color: #a9b3bb; border: 1px dashed #d5dce2; }
QPushButton#primary:disabled { background: #a9c6ca; color: #eef4f5; border: 1px solid #a9c6ca; }
QCheckBox { spacing: 10px; padding: 6px 0; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #718d95; border-radius: 3px; background: white; }
QCheckBox::indicator:unchecked:hover { background: #e7f0f1; border-color: #317c85; }
QCheckBox::indicator:checked { background: #317c85; border-color: #317c85; image: url("__UI_ASSETS__/check.svg"); }
QCheckBox::indicator:checked:hover { background: #286b73; border-color: #286b73; }
QCheckBox::indicator:disabled { background: #edf1f4; border-color: #aabac0; }
QCheckBox::indicator:checked:disabled { background: #7e9ca2; border-color: #7e9ca2; }
QProgressBar { border: none; border-radius: 4px; background: #e3e9ee; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #408b95; border-radius: 4px; }
QPlainTextEdit, QTableWidget, QListWidget#chapters, QListWidget#library { background: white; border: 1px solid #dce3e9; border-radius: 8px; padding: 8px; selection-background-color: #deedef; selection-color: #244c52; }
QTableWidget { padding: 0; gridline-color: #eef2f5; }
QPlainTextEdit#log { font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px; }
QHeaderView::section { background: #eef2f5; border: none; border-bottom: 1px solid #dce3e9; padding: 8px 12px; color: #3d5060; font-weight: 600; }
QFrame#footer { border-top: 1px solid #c9d8db; background: #e6eff0; }
QFrame#footer QLabel { background: transparent; color: #233343; }
QFrame#footer QLabel#hint { color: #5d6c7b; font-size: 13px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; border: none; width: 12px; margin: 6px 2px 6px 0; }
QScrollBar::handle:vertical { background: #c3d1d4; border-radius: 4px; min-height: 40px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: #809fa5; }
QScrollBar::handle:vertical:pressed { background: #317c85; }
QScrollBar:horizontal { background: transparent; border: none; height: 12px; margin: 0 6px; }
QScrollBar::handle:horizontal { background: #c3d1d4; border-radius: 4px; min-width: 40px; margin: 2px 0; }
QScrollBar::handle:horizontal:hover { background: #809fa5; }
QScrollBar::handle:horizontal:pressed { background: #317c85; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: transparent; border: none; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; background: transparent; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; border: none; }
QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow, QScrollBar::right-arrow { width: 0; height: 0; background: transparent; border: none; }
QAbstractScrollArea::corner { background: transparent; }
QMenuBar { background: #2b5c62; color: #eef6f7; border-bottom: 1px solid #1a3d42; padding: 2px 8px; font-size: 13px; }
QMenuBar::item { background: transparent; padding: 4px 12px; margin: 0 2px; border-radius: 4px; }
QMenuBar::item:selected { background: rgba(255, 255, 255, 0.12); color: white; }
QMenuBar::item:pressed { background: rgba(255, 255, 255, 0.18); color: white; }
QMenu { background: #fbfcfc; color: #233343; border: 1px solid #e1e8ea; border-radius: 8px; padding: 4px; font-size: 13px; }
QMenu::item { padding: 6px 16px 6px 12px; margin: 1px 0; border-radius: 5px; background: transparent; }
QMenu::item:selected { background: #e4f0f1; color: #1f474d; }
QMenu::item:disabled { color: #a0abb0; }
QMenu::separator { height: 1px; background: #e8eef0; margin: 4px 10px; }
QComboBox QAbstractItemView { background: #fbfcfc; border: 1px solid #e1e8ea; border-radius: 8px; padding: 4px; outline: 0; selection-background-color: #e4f0f1; selection-color: #1f474d; }
QToolTip { background: #233343; color: #f0f7f8; border: none; border-radius: 4px; padding: 5px 8px; }
QStatusBar { background: #1f474d; color: #c9dfe2; border-top: 1px solid #2b5c62; padding: 1px 12px; font-size: 12px; }
QStatusBar QLabel { background: transparent; color: #c9dfe2; }
QStatusBar::item { border: none; }
""".replace('__UI_ASSETS__', (Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'webnovel_assets').as_posix())


class ModernStyle(QProxyStyle):
    """Fusion with Windows 11 behaviour: mnemonic underlines only appear while Alt is held."""

    def styleHint(self, hint, option=None, widget=None, data=None):
        if hint == QStyle.StyleHint.SH_UnderlineShortcut:
            return 0
        return super().styleHint(hint, option, widget, data)


class RoundedPopups(QObject):
    """Ask Windows 11 to round popup corners and give them the native soft shadow."""

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Show and sys.platform == 'win32'
                and isinstance(obj, QWidget) and obj.isWindow()
                and obj.windowType() in (Qt.WindowType.Popup, Qt.WindowType.ToolTip)):
            import ctypes
            try:
                preference = ctypes.c_int(3 if obj.windowType() == Qt.WindowType.ToolTip else 2)  # small / round
                ctypes.windll.dwmapi.DwmSetWindowAttribute(int(obj.winId()), 33, ctypes.byref(preference),
                                                           ctypes.sizeof(preference))
            except (AttributeError, OSError):
                pass
        return False


class DownloadWorker(QObject):
    log = Signal(str)
    progress = Signal(dict)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings, cancel, log_path, overwrite=False):
        super().__init__()
        self.settings, self.cancel, self.log_path = settings, cancel, log_path
        self.overwrite = overwrite

    @Slot()
    def run(self):
        try:
            result = run_job(self.settings, self.cancel, self.log.emit,
                             self.progress.emit, self.log_path, self.overwrite)
            self.completed.emit(result)
        except Exception as error:
            self.log.emit(f'Error: {error}')
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


class DesktopWindow(QMainWindow):
    PAGE_NAMES = ['Novel setup', 'EPUB options', 'Book details', 'Advanced', 'Activity', 'Library & reader']
    DESCRIPTIONS = [
        'Choose your novel, chapter range and where the book will be saved.',
        'Shape the content for comfortable reading and text-to-speech.',
        'Give your EPUB the right author, credits, language and cover.',
        'Fine-tune connection, content selection and image processing.',
        'Follow the current job, check failed chapters and inspect the log.',
        'Open your finished books, preview chapters and listen with installed voices.',
    ]

    def __init__(self, preferences=None, data_dir=None):
        super().__init__()
        self.preferences = preferences or QSettings('WebNovelScraper', 'Desktop')
        self.data_dir = Path(data_dir) if data_dir is not None else Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.controls = {}
        self.thread = None
        self.worker = None
        self.cancel = Event()
        self.pending_close = False
        self.latest_path = None
        self.reader_chapters = []
        self.speech = None
        self.voices = []
        self.setWindowTitle('')
        self.resize(1180, 820)
        self.setMinimumSize(960, 690)
        icon = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'icon.ico'
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setStyleSheet(STYLE)
        self._build_layout()
        self._align_form_labels()
        self._style_number_fields()
        self._build_menus()
        self._load_preferences()
        self._init_speech()
        self.nav.setCurrentRow(0)
        self._refresh_summary()
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().showMessage('Ready. Set up a novel, then create your EPUB.')

    def showEvent(self, event):
        super().showEvent(event)
        self._theme_title_bar()

    def _theme_title_bar(self):
        """Color the native Windows caption while retaining native window controls."""
        if sys.platform != 'win32' or QApplication.instance().platformName() != 'windows':
            return
        import ctypes
        from ctypes import wintypes
        try:
            set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
            set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            set_attribute.restype = ctypes.c_long
            # DWM colors are COLORREF values (0x00BBGGRR), not RGB hex strings.
            for attribute, value in [(20, 1), (34, 0x4d471f), (35, 0x4d471f), (36, 0xf8f7f0)]:
                color = ctypes.c_uint32(value)
                set_attribute(int(self.winId()), attribute, ctypes.byref(color), ctypes.sizeof(color))
        except (AttributeError, OSError):
            # Older Windows versions retain their system caption appearance.
            pass

    def _build_layout(self):
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(215)
        left = QVBoxLayout(sidebar)
        left.setContentsMargins(0, 20, 0, 10)
        left.setSpacing(6)
        brand_panel = QWidget()
        brand_layout = QVBoxLayout(brand_panel)
        brand_layout.setContentsMargins(16, 0, 16, 0)
        brand_layout.setSpacing(6)
        brand = QLabel('Web Novel\nScraper')
        brand.setObjectName('brand')
        brand_layout.addWidget(brand)
        subtitle = QLabel('EPUB WORKSPACE')
        subtitle.setObjectName('brandTag')
        brand_layout.addWidget(subtitle)
        left.addWidget(brand_panel)
        left.addSpacing(18)
        self.nav = QListWidget()
        self.nav.setObjectName('navigation')
        self.nav.setAccessibleName('Workspace sections')
        for i, name in enumerate(self.PAGE_NAMES, 1):
            self.nav.addItem(f'{i:02d}  {name}')
        left.addWidget(self.nav, 1)
        sidebar_notes = QWidget()
        notes_layout = QVBoxLayout(sidebar_notes)
        notes_layout.setContentsMargins(16, 0, 16, 0)
        notes_layout.setSpacing(6)
        note = QLabel('Prepare EPUBs for your client’s personal accessible reading.')
        note.setObjectName('sidebarNote')
        note.setWordWrap(True)
        notes_layout.addWidget(note)
        left.addWidget(sidebar_notes)
        root.addWidget(sidebar)

        right = QWidget()
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 24, 24, 8)
        headings = QVBoxLayout()
        headings.setSpacing(4)
        self.page_title = QLabel()
        self.page_title.setObjectName('pageTitle')
        self.page_description = QLabel()
        self.page_description.setObjectName('subtitle')
        self.page_description.setWordWrap(True)
        headings.addWidget(self.page_title)
        headings.addWidget(self.page_description)
        header_layout.addLayout(headings, 1)
        self.pill = QLabel('Draft')
        self.pill.setObjectName('pill')
        self.pill.setAccessibleName('Job status')
        header_layout.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        layout.addWidget(header)
        self.stack = QStackedWidget()
        for builder in (self._novel_page, self._options_page, self._metadata_page,
                        self._advanced_page, self._activity_page, self._reader_page):
            self.stack.addWidget(builder())
        layout.addWidget(self.stack, 1)
        self.nav.currentRowChanged.connect(self._change_page)

        footer = QFrame()
        footer.setObjectName('footer')
        bottom = QHBoxLayout(footer)
        bottom.setContentsMargins(24, 12, 24, 12)
        bottom.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.summary_label = QLabel('No book configured')
        self.summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.summary_label.setStyleSheet('font-weight: 600;')
        self.summary_detail = QLabel('Set up your novel to get started.')
        self.summary_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.summary_detail.setObjectName('hint')
        text.addWidget(self.summary_label)
        text.addWidget(self.summary_detail)
        bottom.addLayout(text, 1)
        self.cancel_button = self._button('Cancel job', self._cancel_job)
        self.cancel_button.setObjectName('ghost')
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        bottom.addWidget(self.cancel_button)
        bottom.addSpacing(16)
        self.create_button = self._button('Review and create EPUB', self._start_job, primary=True)
        bottom.addWidget(self.create_button)
        layout.addWidget(footer)
        root.addWidget(right, 1)
        self.setCentralWidget(central)

    def _page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 0, 24, 24)
        layout.setSpacing(8)
        scroll.setWidget(page)
        return scroll, layout

    def _card(self, layout, title, description=''):
        # '&' marks a keyboard mnemonic in Qt titles; escape it so it renders literally.
        group = QGroupBox(title.replace('&', '&&'))
        box = QVBoxLayout(group)
        box.setContentsMargins(0, 8, 0, 0)
        box.setSpacing(8)
        if description:
            box.addWidget(self._hint(description))
        layout.addWidget(group)
        return box

    def _hint(self, text):
        label = QLabel(text)
        label.setObjectName('hint')
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def _button(self, text, callback, primary=False):
        button = QPushButton(text)
        button.setAccessibleName(text)
        if primary:
            button.setObjectName('primary')
        button.clicked.connect(callback)
        return button

    def _form(self, layout):
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(16)
        layout.addLayout(form)
        return form

    def _style_number_fields(self):
        for control in self.findChildren(QSpinBox) + self.findChildren(QDoubleSpinBox):
            edit = control.lineEdit()
            edit.setStyleSheet('QLineEdit { background: transparent; border: none; border-radius: 0; padding: 0; min-height: 0; }')
            edit.setTextMargins(5, 0, 2, 0)

    def _add_form_row(self, form, label, control, hint=''):
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text = QLabel(label.rstrip(':') + ' :')
        text.setBuddy(control)
        text.setProperty('formCaption', True)
        text.setContentsMargins(0, 2, 0, 0)
        field = control
        if hint:
            field = QWidget()
            column = QVBoxLayout(field)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            column.addWidget(control, 0, Qt.AlignmentFlag.AlignLeft if control.maximumWidth() < 10000 else Qt.AlignmentFlag(0))
            column.addWidget(self._hint(hint))
        form.addRow(text, field)

    def _align_form_labels(self):
        """Give every form on a page the same label column so inputs line up across cards."""
        self.ensurePolished()
        roots = [self.stack.widget(i) for i in range(self.stack.count())]
        for root in roots:
            captions = [label for label in root.findChildren(QLabel) if label.property('formCaption')]
            if not captions:
                continue
            for label in captions:
                label.ensurePolished()
            column_width = max(label.sizeHint().width() for label in captions)
            for label in captions:
                label.setMinimumWidth(column_width)

    def _field(self, form, key, label, kind='text', placeholder='', low=1, high=100000, decimals=2, width=None, hint=''):
        if kind == 'int':
            control = QSpinBox()
            control.setRange(low, high)
            control.valueChanged.connect(self._refresh_summary)
        elif kind == 'float':
            control = QDoubleSpinBox()
            control.setDecimals(decimals)
            control.setRange(low, high)
            control.setSingleStep(.1)
            control.valueChanged.connect(self._refresh_summary)
        else:
            control = QLineEdit()
            control.setPlaceholderText(placeholder)
            control.textChanged.connect(self._refresh_summary)
        control.setAccessibleName(label)
        if width is None and kind in ('int', 'float'):
            width = 180
        if width:
            control.setMaximumWidth(width)
        self.controls[key] = control
        self._add_form_row(form, label, control, hint)
        return control

    def _check(self, layout, key, title, description):
        checkbox = QCheckBox(title)
        checkbox.setAccessibleName(title)
        checkbox.setAccessibleDescription(description)
        self.controls[key] = checkbox
        layout.addWidget(checkbox)
        layout.addWidget(self._hint(description))
        checkbox.toggled.connect(self._refresh_summary)
        return checkbox

    def _novel_page(self):
        page, layout = self._page()
        source = self._card(layout, '1. Choose a novel',
                            'Paste the novel page address to fill the source automatically, or enter the base URL and novel ID below.')
        paste = QHBoxLayout()
        self.novel_url = QLineEdit()
        self.novel_url.setPlaceholderText('https://website.example/novel/your-novel-id')
        self.novel_url.setAccessibleName('Full novel URL')
        paste.addWidget(self.novel_url, 1)
        paste.addWidget(self._button('Use this URL', self._use_url))
        source.addLayout(paste)
        form = self._form(source)
        self._field(form, 'base_url', 'Website base URL', placeholder='https://website.example/novel/')
        self._field(form, 'novel_id', 'Novel ID', placeholder='your-novel-id')
        self._field(form, 'title', 'Book title', placeholder='The title that appears in your EPUB')

        range_box = self._card(layout, '2. Choose chapters', 'Both the first and last chapter are included.')
        range_form = self._form(range_box)
        self._field(range_form, 'start', 'First chapter', kind='int', high=1000000)
        self._field(range_form, 'end', 'Last chapter', kind='int', high=1000000)
        self.range_hint = self._hint('10 chapters selected')
        range_box.addWidget(self.range_hint)

        output = self._card(layout, '3. Save your book')
        output_form = self._form(output)
        folder = QWidget()
        row = QHBoxLayout(folder)
        row.setContentsMargins(0, 0, 0, 0)
        self.controls['output_dir'] = QLineEdit()
        self.controls['output_dir'].setAccessibleName('Output folder')
        self.controls['output_dir'].textChanged.connect(self._refresh_summary)
        row.addWidget(self.controls['output_dir'], 1)
        row.addWidget(self._button('Browse…', self._choose_folder))
        self._add_form_row(output_form, 'Output folder', folder)
        self._field(output_form, 'filename', 'EPUB filename', placeholder='Leave empty to use the book title')
        self.output_hint = self._hint('')
        output.addWidget(self.output_hint)
        layout.addStretch()
        return page

    def _options_page(self):
        page, layout = self._page()
        content = self._card(layout, 'Content to include', 'Choose how much supporting material appears alongside the story.')
        self._check(content, 'include_footnotes', 'Include footnotes',
                    'Keep footnotes and links from their references. Turn this off for a more uninterrupted listening experience.')
        self._check(content, 'include_notes', 'Include chapter and translator notes',
                    'Keep recognized translator notes and end-of-chapter notes in a separate chapter notes section.')
        naming = self._card(layout, 'Chapter headings')
        self._check(naming, 'simple_chapters', 'Use simple chapter names',
                    'Use “Chapter 1”, “Chapter 2”, and so on, following the source chapter number. Otherwise keep original chapter titles.')
        expectations = self._card(layout, 'Reading-ready EPUBs')
        expectations.addWidget(self._hint('Every book includes a table of contents, chapter navigation and reading styles. '
                                         'The existing content cleaner removes recognized advertisements, scripts and repeated chapter numbers. '
                                         'You can preview the finished text on the Library & reader page before sharing it with your client.'))
        layout.addStretch()
        return page

    def _metadata_page(self):
        page, layout = self._page()
        credits = self._card(layout, 'Book identity & credits', 'Optional credits can be left empty.')
        form = self._form(credits)
        self._field(form, 'author', 'Author', placeholder='Original author')
        self._field(form, 'translator', 'Translator', placeholder='Translation credit')
        self._field(form, 'creator', 'Creator / publisher')
        self._field(form, 'language', 'Language code', placeholder='en, en-US, sk, de…', width=180,
                    hint='Saved in the EPUB so reading software can choose an appropriate voice.')
        cover = self._card(layout, 'Cover image', 'Paste a direct image URL. The cover is cropped to the chosen ratio and optimized as a JPEG.')
        cover_form = self._form(cover)
        self._field(cover_form, 'cover_url', 'Cover image URL', placeholder='https://website.example/cover.jpg',
                    hint='Leave empty for a book without a cover. Image size and ratio are on the Advanced page.')
        layout.addStretch()
        return page

    def _advanced_page(self):
        page, layout = self._page()
        network = self._card(layout, 'Connection & retries', 'A modest request rate is friendlier to the source website. Failed requests are retried automatically.')
        form = self._form(network)
        self._field(form, 'requests_per_second', 'Requests per second', kind='float', low=.1, high=20)
        self._field(form, 'timeout', 'Request timeout (seconds)', kind='int', high=600)
        self._field(form, 'retries', 'Attempts per chapter', kind='int', high=20)
        self._field(form, 'user_agent', 'Browser user agent')
        selectors = self._card(layout, 'Website content', 'The defaults match the website supported by the existing scraper. Other websites may also need changes to its content cleaning rules.')
        form = self._form(selectors)
        self._field(form, 'title_selector', 'Chapter title CSS selector')
        self._field(form, 'content_selector', 'Chapter content CSS selector')
        self._field(form, 'min_content_length', 'Minimum chapter characters', kind='int', high=100000)
        images = self._card(layout, 'Cover processing')
        form = self._form(images)
        self._field(form, 'max_cover_height', 'Maximum height (pixels)', kind='int', low=100, high=10000)
        self._field(form, 'cover_aspect_ratio', 'Width / height ratio', kind='float', low=.1, high=3, decimals=4)
        images.addWidget(self._hint('The default ratio is 0.6667 (2:3). Images are cropped to this ratio, then resized if needed.'))
        diagnostics = self._card(layout, 'Diagnostics')
        self._check(diagnostics, 'debug', 'Enable detailed debug logging',
                    'Record raw chapter content and processing decisions. This can produce a large log; normal progress is always shown in Activity.')
        diagnostics.addWidget(self._button('Restore advanced defaults', self._reset_advanced))
        layout.addStretch()
        return page

    def _activity_page(self):
        page, layout = self._page()
        layout.setSpacing(16)
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.metrics = {}
        for key, title in [('downloaded', 'DOWNLOADED'), ('failed', 'FAILED'), ('remaining', 'REMAINING')]:
            card = QFrame()
            card.setObjectName('metricCard')
            box = QVBoxLayout(card)
            box.setContentsMargins(16, 12, 16, 12)
            box.setSpacing(2)
            caption = QLabel(title)
            caption.setObjectName('metricLabel')
            box.addWidget(caption)
            value = QLabel('0')
            value.setObjectName('metric')
            value.setAccessibleName(title.capitalize())
            box.addWidget(value)
            self.metrics[key] = value
            metrics.addWidget(card)
        layout.addLayout(metrics)
        status = QVBoxLayout()
        status.setSpacing(8)
        self.activity_status = QLabel('No job running. Your next EPUB starts with Novel setup.')
        self.activity_status.setObjectName('emptyState')
        self.activity_status.setWordWrap(True)
        self.activity_status.setTextFormat(Qt.TextFormat.PlainText)
        status.addWidget(self.activity_status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setAccessibleName('Chapter download progress')
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        status.addWidget(self.progress_bar)
        layout.addLayout(status)

        layout.addWidget(self._section('Chapters'))
        self.chapter_stack = QStackedWidget()
        self.chapter_stack.setMinimumHeight(120)
        empty = QLabel('No chapters yet. Results appear here while an EPUB is being created.')
        empty.setObjectName('emptyState')
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        empty_frame = QFrame()
        empty_frame.setObjectName('metricCard')
        QVBoxLayout(empty_frame).addWidget(empty)
        self.chapter_stack.addWidget(empty_frame)
        self.chapter_table = QTableWidget(0, 3)
        self.chapter_table.setHorizontalHeaderLabels(['Chapter', 'Status', 'Title / source'])
        self.chapter_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.chapter_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.chapter_table.setAccessibleName('Chapter download results')
        self.chapter_table.verticalHeader().setVisible(False)
        self.chapter_table.setShowGrid(False)
        header = self.chapter_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.chapter_stack.addWidget(self.chapter_table)
        layout.addWidget(self.chapter_stack, 2)

        log_header = QHBoxLayout()
        log_header.addWidget(self._section('Activity log'), 1)
        self.save_log_button = self._button('Save log…', self._export_log)
        self.save_log_button.setEnabled(False)
        log_header.addWidget(self.save_log_button)
        layout.addLayout(log_header)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName('log')
        self.log_view.setReadOnly(True)
        self.log_view.setAccessibleName('Activity log')
        self.log_view.setPlaceholderText('The log is empty. Messages from the next job appear here.')
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setMinimumHeight(120)
        self.log_view.textChanged.connect(
            lambda: self.save_log_button.setEnabled(bool(self.log_view.toPlainText())))
        layout.addWidget(self.log_view, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.setContentsMargins(0, 8, 0, 0)
        self.open_latest_button = self._button('Open finished EPUB', self._open_latest, primary=True)
        self.preview_latest_button = self._button('Preview in reader', self._preview_latest)
        for button in (self.open_latest_button, self.preview_latest_button):
            button.setEnabled(False)
            button.setVisible(False)
            actions.addWidget(button)
        actions.addWidget(self._button('Open output folder', self._open_output))
        actions.addStretch()
        layout.addLayout(actions)
        return page

    def _section(self, text):
        label = QLabel(text)
        label.setObjectName('section')
        return label

    def _set_metric(self, key, value, tone=''):
        label = self.metrics[key]
        label.setText(str(value))
        if label.property('tone') != tone:
            label.setProperty('tone', tone)
            label.style().unpolish(label)
            label.style().polish(label)

    def _show_finished_actions(self, visible):
        for button in (self.open_latest_button, self.preview_latest_button):
            button.setEnabled(visible)
            button.setVisible(visible)

    def _reader_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 0, 24, 24)
        actions = QHBoxLayout()
        actions.addWidget(self._button('Open EPUB…', self._choose_epub, primary=True))
        actions.addWidget(self._button('Open in default app', self._open_reader_external))
        actions.addStretch()
        layout.addLayout(actions)
        split = QSplitter(Qt.Orientation.Horizontal)
        library_panel = QWidget()
        left = QVBoxLayout(library_panel)
        left.setContentsMargins(0, 8, 12, 0)
        left.addWidget(self._section('Recent books'))
        self.library = QListWidget()
        self.library.setObjectName('library')
        self.library.setAccessibleName('Recent EPUB books')
        self.library.itemActivated.connect(self._library_open)
        self.library.itemClicked.connect(self._library_open)
        left.addWidget(self.library, 1)
        left.addWidget(self._section('Chapters'))
        self.chapter_list = QListWidget()
        self.chapter_list.setObjectName('chapters')
        self.chapter_list.setAccessibleName('Book chapters')
        self.chapter_list.currentRowChanged.connect(self._reader_chapter)
        left.addWidget(self.chapter_list, 2)
        split.addWidget(library_panel)
        reading_panel = QWidget()
        right = QVBoxLayout(reading_panel)
        right.setContentsMargins(4, 8, 0, 0)
        self.reader_title = QLabel('Your reading preview')
        self.reader_title.setWordWrap(True)
        self.reader_title.setTextFormat(Qt.TextFormat.PlainText)
        self.reader_title.setStyleSheet('font-size: 20px; font-weight: 600;')
        right.addWidget(self.reader_title)
        self.reader_text = QPlainTextEdit()
        self.reader_text.setReadOnly(True)
        self.reader_text.setAccessibleName('Chapter reading text')
        self.reader_text.setPlaceholderText('Create or open an EPUB to preview its chapters here.')
        self.reader_text.setStyleSheet('font-size: 17px; padding: 16px;')
        right.addWidget(self.reader_text, 1)
        speech_controls = QHBoxLayout()
        self.speak_button = self._button('Read aloud', self._speak)
        self.pause_button = self._button('Pause', self._pause_speech)
        self.stop_button = self._button('Stop', self._stop_speech)
        for button in (self.speak_button, self.pause_button, self.stop_button):
            button.setEnabled(False)
            speech_controls.addWidget(button)
        speech_controls.addStretch()
        self.reader_font = QSpinBox()
        self.reader_font.setRange(12, 32)
        self.reader_font.setValue(17)
        self.reader_font.setSuffix(' px')
        self.reader_font.setAccessibleName('Reader text size')
        self.reader_font.valueChanged.connect(lambda size: self.reader_text.setStyleSheet(f'font-size: {size}px; padding: 16px;'))
        speech_controls.addWidget(QLabel('Text size'))
        speech_controls.addWidget(self.reader_font)
        right.addLayout(speech_controls)
        speech_form = QFormLayout()
        self.voice_box = QComboBox()
        self.voice_box.setAccessibleName('Installed reading voice')
        self.voice_box.currentIndexChanged.connect(self._set_voice)
        self._add_form_row(speech_form, 'Voice', self.voice_box)
        self.speech_rate = QDoubleSpinBox()
        self.speech_rate.setRange(-1, 1)
        self.speech_rate.setDecimals(1)
        self.speech_rate.setSingleStep(.1)
        self.speech_rate.setAccessibleName('Speech speed: negative is slower, positive is faster')
        self.speech_rate.valueChanged.connect(self._set_speech_rate)
        self._add_form_row(speech_form, 'Speech speed', self.speech_rate)
        right.addLayout(speech_form)
        self.speech_status = self._hint('Open a book to preview its text.')
        right.addWidget(self.speech_status)
        split.addWidget(reading_panel)
        split.setSizes([230, 580])
        layout.addWidget(split, 1)
        self.reader_path = None
        return page

    def _build_menus(self):
        file_menu = self.menuBar().addMenu('&File')
        self.project_actions = []

        def add(menu, title, callback, shortcut=None, project=False):
            action = QAction(title, self)
            if shortcut is not None:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            menu.addAction(action)
            if project:
                self.project_actions.append(action)
            return action

        add(file_menu, '&New project', self._new_project, QKeySequence.StandardKey.New, project=True)
        add(file_menu, '&Open EPUB', self._choose_epub, QKeySequence.StandardKey.Open)
        file_menu.addSeparator()
        add(file_menu, '&Load settings', self._load_settings_file, 'Ctrl+L', project=True)
        add(file_menu, '&Save settings', self._save_settings_file, QKeySequence.StandardKey.Save, project=True)
        file_menu.addSeparator()
        add(file_menu, 'E&xit', self.close, 'Alt+F4')
        help_menu = self.menuBar().addMenu('&Help')
        add(help_menu, '&How to use this workspace', self._help, QKeySequence.StandardKey.HelpContents)

    def _change_page(self, index):
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        self.page_title.setText(self.PAGE_NAMES[index])
        self.page_description.setText(self.DESCRIPTIONS[index])

    def _defaults(self):
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        return JobSettings(output_dir=str(Path(documents or str(Path.home())) / 'Web Novel EPUBs'))

    def _apply_settings(self, settings):
        for key, control in self.controls.items():
            control.blockSignals(True)
            value = getattr(settings, key)
            if isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
                control.setValue(value)
            else:
                control.setText(value)
                control.setCursorPosition(0)
            control.setProperty('invalid', False)
            control.style().unpolish(control)
            control.style().polish(control)
            control.blockSignals(False)
        self._refresh_summary()

    def _collect_settings(self):
        values = {}
        for key, control in self.controls.items():
            if isinstance(control, QCheckBox):
                values[key] = control.isChecked()
            elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
                values[key] = control.value()
            else:
                values[key] = control.text().strip()
        return JobSettings(**values)

    def _refresh_summary(self, *args):
        if not hasattr(self, 'summary_label') or len(self.controls) < len(JobSettings.__dataclass_fields__):
            return
        settings = self._collect_settings()
        count = settings.end - settings.start + 1
        self.summary_label.setText(settings.title or 'Your next EPUB')
        self.summary_detail.setText(f'{max(count, 0)} chapters  ·  {settings.language or "Language unset"}  ·  {settings.output_path.name}')
        self.range_hint.setText(f'{count} chapters selected' if settings.start <= settings.end else 'The last chapter must come after the first chapter.')
        self.output_hint.setText(f'Will save to: {settings.output_path}')
        if self.thread is None and not self.chapter_table.rowCount():
            self._set_metric('remaining', max(count, 0))
        for control in self.controls.values():
            if control.property('invalid'):
                control.setProperty('invalid', False)
                control.style().unpolish(control)
                control.style().polish(control)

    def _load_preferences(self):
        settings = self._defaults()
        try:
            data = self.preferences.value('draft', '')
            if data:
                settings = JobSettings.from_dict(json.loads(data))
        except (ValueError, TypeError):
            pass
        self._apply_settings(settings)
        try:
            for path in reversed(json.loads(self.preferences.value('recent', '[]'))):
                if isinstance(path, str) and Path(path).is_file():
                    self._remember_book(Path(path))
        except (ValueError, TypeError):
            pass
        size = self.preferences.value('reader_size', 17, type=int)
        self.reader_font.setValue(size)
        geometry = self.preferences.value('geometry')
        if geometry:
            self.restoreGeometry(geometry)

    def _save_preferences(self):
        self.preferences.setValue('draft', json.dumps(self._collect_settings().to_dict()))
        paths = [self.library.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.library.count())]
        self.preferences.setValue('recent', json.dumps(paths[:30]))
        self.preferences.setValue('reader_size', self.reader_font.value())
        self.preferences.setValue('geometry', self.saveGeometry())
        self.preferences.sync()

    def _use_url(self):
        try:
            base, novel = split_novel_url(self.novel_url.text())
        except ValueError as error:
            QMessageBox.warning(self, 'Check the novel URL', str(error))
            return
        self.controls['base_url'].setText(base)
        self.controls['novel_id'].setText(novel)
        self.statusBar().showMessage('Source filled in. Add your book title and chapter range.')

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose EPUB output folder', self.controls['output_dir'].text())
        if folder:
            self.controls['output_dir'].setText(folder)

    def _new_project(self):
        if QMessageBox.question(self, 'New project', 'Clear the current draft and start a new book?') == QMessageBox.StandardButton.Yes:
            self._apply_settings(self._defaults())
            self.novel_url.clear()
            self.nav.setCurrentRow(0)
            self.pill.setText('Draft')

    def _reset_advanced(self):
        defaults = self._defaults()
        current = self._collect_settings()
        for key in ['requests_per_second', 'timeout', 'retries', 'user_agent', 'title_selector',
                    'content_selector', 'min_content_length', 'max_cover_height', 'cover_aspect_ratio', 'debug']:
            setattr(current, key, getattr(defaults, key))
        self._apply_settings(current)

    def _load_settings_file(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Load project settings', '', 'Project settings (*.json)')
        if name:
            try:
                settings = JobSettings.from_dict(json.loads(Path(name).read_text(encoding='utf-8')))
                self._apply_settings(settings)
                self.nav.setCurrentRow(0)
                self.statusBar().showMessage(f'Loaded {Path(name).name}')
            except (OSError, ValueError, TypeError) as error:
                QMessageBox.warning(self, 'Could not load settings', str(error))

    def _save_settings_file(self):
        name, _ = QFileDialog.getSaveFileName(self, 'Save project settings', 'Web Novel project.json', 'Project settings (*.json)')
        if name:
            try:
                Path(name).write_text(json.dumps(self._collect_settings().to_dict(), indent=2), encoding='utf-8')
                self.statusBar().showMessage(f'Saved {Path(name).name}')
            except OSError as error:
                QMessageBox.warning(self, 'Could not save settings', str(error))

    def _start_job(self):
        if self.thread is not None:
            return
        settings = self._collect_settings()
        errors = settings.validate()
        if errors:
            for key in errors:
                control = self.controls[key]
                control.setProperty('invalid', True)
                control.style().unpolish(control)
                control.style().polish(control)
            first = next(iter(errors))
            control = self.controls[first]
            for index in range(4):
                if self.stack.widget(index).isAncestorOf(control):
                    self.nav.setCurrentRow(index)
                    self.stack.widget(index).ensureWidgetVisible(control)
                    break
            QMessageBox.warning(self, 'Check your settings', '\n\n'.join(errors.values()))
            control.setFocus()
            return
        total = settings.end - settings.start + 1
        overwrite = settings.output_path.exists()
        dialog = QMessageBox(self)
        dialog.setWindowTitle('Review your EPUB')
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f'Create “{settings.title}” from {total} chapters?')
        dialog.setInformativeText(f'Chapters {settings.start}–{settings.end}\nSave to: {settings.output_path}' +
                                  ('\n\nThis will replace the existing EPUB only after the new book is complete.' if overwrite else ''))
        dialog.setDetailedText('\n'.join(f'{key.replace("_", " ").capitalize()}: {value}' for key, value in settings.to_dict().items()))
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel if overwrite else QMessageBox.StandardButton.Ok)
        dialog.button(QMessageBox.StandardButton.Ok).setText('Replace and create' if overwrite else 'Create EPUB')
        if dialog.exec() != QMessageBox.StandardButton.Ok:
            return
        self._save_preferences()
        self.log_view.clear()
        self.chapter_table.setRowCount(0)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.chapter_stack.setCurrentIndex(0)
        self._set_metric('downloaded', 0)
        self._set_metric('failed', 0)
        self._set_metric('remaining', total)
        self.activity_status.setText(f'Preparing “{settings.title}”…')
        self._show_finished_actions(False)
        self.latest_path = None
        self.cancel = Event()
        self.thread = QThread(self)
        log_path = self.data_dir / 'logs' / f'job-{datetime.now():%Y%m%d-%H%M%S-%f}.log'
        self.worker = DownloadWorker(settings, self.cancel, log_path, overwrite)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._job_progress)
        self.worker.completed.connect(self._job_completed)
        self.worker.failed.connect(self._job_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self._set_running(True)
        self.nav.setCurrentRow(4)
        self._append_log(f'Full diagnostic log: {log_path}')
        self.thread.start()

    def _set_running(self, running):
        for index in range(4):
            self.stack.widget(index).setEnabled(not running)
        for action in self.project_actions:
            action.setEnabled(not running)
        self.create_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.cancel_button.setVisible(running)
        self.activity_status.setObjectName('' if running else 'emptyState')
        self.activity_status.style().unpolish(self.activity_status)
        self.activity_status.style().polish(self.activity_status)
        self.pill.setText('Working' if running else 'Ready')
        self.statusBar().showMessage('Creating EPUB in the background…' if running else 'Ready for the next book.')

    @Slot(str)
    def _append_log(self, text):
        self.log_view.appendPlainText(text)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    @Slot(dict)
    def _job_progress(self, data):
        self.progress_bar.setValue(data['current'])
        self._set_metric('downloaded', data['success'], 'good' if data['success'] else '')
        self._set_metric('failed', data['failed'], 'bad' if data['failed'] else '')
        self._set_metric('remaining', data['total'] - data['current'])
        stage = data['stage']
        if stage == 'fetching':
            self.activity_status.setText(f'Downloading chapter {data["number"]}…')
            return
        self.chapter_stack.setCurrentIndex(1)
        row = self.chapter_table.rowCount()
        self.chapter_table.insertRow(row)
        for column, value in enumerate([str(data['number']), 'Downloaded' if stage == 'downloaded' else 'Failed', data['title']]):
            self.chapter_table.setItem(row, column, QTableWidgetItem(value))
        self.chapter_table.scrollToBottom()
        self.activity_status.setText(f'{data["current"]} of {data["total"]} chapters processed.')

    @Slot(object)
    def _job_completed(self, result):
        if result.cancelled:
            self.activity_status.setText('Job cancelled. No EPUB was saved.')
        else:
            self.latest_path = result.path
            self._remember_book(result.path)
            self._show_finished_actions(True)
            text = f'EPUB ready: {result.path.name} — {result.success} chapters.'
            if result.failed:
                text += f' Missing chapters: {", ".join(map(str, result.failed))}. You can adjust the range and create another EPUB for those chapters.'
            self.activity_status.setText(text)
            self.statusBar().showMessage(text)
        self._save_preferences()

    @Slot(str)
    def _job_failed(self, error):
        self.activity_status.setText(f'Could not create EPUB: {error}')
        if not self.pending_close:
            QMessageBox.warning(self, 'EPUB creation failed', error)

    @Slot()
    def _thread_finished(self):
        self.thread = None
        self.worker = None
        self._set_running(False)
        self.pill.setText('EPUB ready' if self.latest_path else 'Draft')
        if self.pending_close:
            self.close()

    def _cancel_job(self):
        if self.thread is not None:
            self.cancel.set()
            self.cancel_button.setEnabled(False)
            self.activity_status.setText('Cancelling… Waiting for the current network request to finish. No EPUB will be saved.')

    def _export_log(self):
        name, _ = QFileDialog.getSaveFileName(self, 'Save activity log', 'Web Novel activity.log', 'Log files (*.log);;Text files (*.txt)')
        if name:
            try:
                Path(name).write_text(self.log_view.toPlainText(), encoding='utf-8')
            except OSError as error:
                QMessageBox.warning(self, 'Could not save log', str(error))

    def _open_path(self, path):
        path = Path(path)
        if not path.exists():
            QMessageBox.information(self, 'File not found', f'This file or folder is not available:\n{path}')
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            QMessageBox.warning(self, 'Could not open', 'Choose a default EPUB reading app in your operating system, or use the built-in reader.')

    def _open_latest(self):
        if self.latest_path:
            self._open_path(self.latest_path)

    def _preview_latest(self):
        if self.latest_path:
            self._load_epub(self.latest_path)

    def _open_output(self):
        self._open_path(self.latest_path.parent if self.latest_path else self.controls['output_dir'].text())

    def _choose_epub(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Open EPUB for preview', self.controls['output_dir'].text(), 'EPUB books (*.epub)')
        if name:
            self._load_epub(Path(name))

    def _remember_book(self, path):
        path = str(Path(path).resolve())
        for index in range(self.library.count() - 1, -1, -1):
            if self.library.item(index).data(Qt.ItemDataRole.UserRole) == path:
                self.library.takeItem(index)
        item = QListWidgetItem(Path(path).stem)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self.library.insertItem(0, item)
        while self.library.count() > 30:
            self.library.takeItem(self.library.count() - 1)

    def _library_open(self, item):
        self._load_epub(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _load_epub(self, path):
        try:
            title, chapters = read_epub(path)
        except Exception as error:
            QMessageBox.warning(self, 'Could not open EPUB', str(error))
            return
        self._stop_speech()
        self.reader_path = path
        self.reader_title.setText(title)
        self.reader_chapters = chapters
        self.chapter_list.clear()
        self.chapter_list.addItems([chapter[0] for chapter in chapters])
        self.chapter_list.setCurrentRow(0)
        self._remember_book(path)
        self.nav.setCurrentRow(5)
        self.statusBar().showMessage(f'Opened {path.name} — {len(chapters)} readable chapters.')

    def _reader_chapter(self, row):
        self._stop_speech()
        if 0 <= row < len(self.reader_chapters):
            self.reader_text.setPlainText(self.reader_chapters[row][1])
            self.reader_text.moveCursor(QTextCursor.MoveOperation.Start)
            self.speak_button.setEnabled(self.speech is not None and bool(self.voices))

    def _open_reader_external(self):
        if self.reader_path:
            self._open_path(self.reader_path)
        else:
            self._choose_epub()

    def _init_speech(self):
        engines = QTextToSpeech.availableEngines()
        if not engines:
            self.speech_status.setText('No system TTS engine found. You can still preview text or open the EPUB in your reading app.')
            return
        engine = 'sapi' if 'sapi' in engines else engines[0]
        self.speech = QTextToSpeech(engine, self)
        self.speech.stateChanged.connect(self._speech_state)
        self.speech.errorOccurred.connect(lambda reason, message: self.speech_status.setText(f'Reading voice error: {message}'))
        self.voices = self.speech.availableVoices()
        for voice in self.voices:
            self.voice_box.addItem(f'{voice.name()} — {voice.locale().nativeLanguageName()}')
        if self.voices:
            self._set_voice(0)
            self.speech_status.setText('Read aloud uses voices installed on this computer. Select a chapter, then press Read aloud.')
        else:
            self.speech_status.setText('No installed reading voices found. Text preview is available.')

    def _set_voice(self, index):
        if self.speech is not None and 0 <= index < len(self.voices):
            self.speech.setVoice(self.voices[index])

    def _set_speech_rate(self, rate):
        if self.speech is not None:
            self.speech.setRate(rate)

    def _speak(self):
        if self.speech is not None and self.reader_text.toPlainText():
            self.speech.setRate(self.speech_rate.value())
            self.speech.say(self.reader_text.toPlainText())

    def _pause_speech(self):
        if self.speech is not None:
            if self.speech.state() == QTextToSpeech.State.Paused:
                self.speech.resume()
            else:
                self.speech.pause()

    def _stop_speech(self):
        if self.speech is not None:
            self.speech.stop()

    def _speech_state(self, state):
        speaking = state in (QTextToSpeech.State.Speaking, QTextToSpeech.State.Paused)
        can_pause = bool(self.speech.engineCapabilities() & QTextToSpeech.Capability.PauseResume)
        self.pause_button.setEnabled(speaking and can_pause)
        self.pause_button.setText('Resume' if state == QTextToSpeech.State.Paused else 'Pause')
        self.stop_button.setEnabled(speaking)
        self.speak_button.setEnabled(not speaking and bool(self.reader_chapters) and bool(self.voices))
        if state == QTextToSpeech.State.Speaking:
            self.speech_status.setText('Reading the selected chapter aloud…')
        elif state == QTextToSpeech.State.Paused:
            self.speech_status.setText('Reading paused.')
        elif state == QTextToSpeech.State.Ready:
            self.speech_status.setText('Ready to read the selected chapter.')

    def _help(self):
        QMessageBox.information(self, 'Your EPUB workspace',
            '1. Novel setup: paste a novel URL, choose chapters, and select an output folder.\n\n'
            '2. EPUB options: decide whether to include footnotes and translator notes.\n\n'
            '3. Book details: add credits, a language code, and an optional cover.\n\n'
            '4. Advanced: change request rate, retries, selectors, or debug logging if needed.\n\n'
            '5. Review & create EPUB: confirm the settings and follow progress in Activity.\n\n'
            '6. Library & reader: preview the finished book, listen with system voices, or open it in another reading app.\n\n'
            'File → Save settings lets you reuse a project. Your current draft is also remembered when you close the app.\n\n'
            'This app is intended solely to prepare EPUBs for an individual blind person’s personal reading with TTS.')

    def closeEvent(self, event):
        if self.thread is not None:
            if not self.pending_close:
                answer = QMessageBox.question(self, 'Job still running',
                    'Cancel the current job and close when the active request finishes?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self.pending_close = True
                    self._cancel_job()
            event.ignore()
            return
        self._stop_speech()
        self._save_preferences()
        event.accept()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--cli':
        sys.argv.pop(1)
        from WebNovelScraper import main as cli_main
        cli_main()
        return 0
    parser = argparse.ArgumentParser(description='Web Novel Scraper desktop workspace')
    parser.add_argument('--cli', action='store_true', help='Run the original command-line interface; put --cli before scraper options')
    parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName('WebNovelScraper')
    app.setOrganizationName('WebNovelScraper')
    app.setApplicationDisplayName('Web Novel Scraper')
    app.setStyle(ModernStyle('Fusion'))
    popups = RoundedPopups(app)
    app.installEventFilter(popups)
    app.setFont(QFont('Segoe UI', 10))
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor('#a3afb9'))
    app.setPalette(palette)
    window = DesktopWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
